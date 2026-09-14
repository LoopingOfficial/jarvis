"""Transcripts d'appels : normalisation, puis extraction pour devis.

Étape 1 — une seule forme
-------------------------
VTT, SRT, JSON et TXT se réduisent tous à un dialogue attribué. Tout est donc
ramené à une liste d'`Utterance(speaker, text)` AVANT toute extraction : sinon
chaque format nourrirait la suite à sa façon et multiplierait les cas
particuliers.

Le piège des cues roulantes
---------------------------
Les VTT produits par du sous-titrage en direct répètent un préfixe grandissant
d'une cue à l'autre :

    Alors pour le logo
    Alors pour le logo il faudrait
    Alors pour le logo il faudrait compter 5 000 €

Une déduplication sur les doublons exacts ne voit rien, et « 5 000 € » est lu
trois fois — le devis sort à 15 000 €. C'est réglé ici, à la normalisation,
parce qu'aucune passe suivante ne peut le rattraper.

Étape 2 — extraction hybride
----------------------------
Passe déterministe (regex) d'abord : montants, quantités, taux de TVA,
coordonnées. Elle est reproductible et suffit aux cas explicites. La passe LLM
est OPTIONNELLE et ne fait que compléter : si le modèle est indisponible, on
rend ce que la regex a trouvé au lieu d'échouer. Chaque élément extrait porte
sa `source` (`regex` ou `llm`) et la phrase dont il vient : on doit pouvoir
vérifier d'où sort un montant avant de l'envoyer à un client.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

VTT = "vtt"
SRT = "srt"
JSON_FMT = "json"
TEXT = "txt"

SUPPORTED_SUFFIXES = {".vtt", ".srt", ".json", ".txt", ".md", ".log"}

# --- motifs de nettoyage ----------------------------------------------------
_RE_TIMESTAMP = re.compile(
    r"^\s*(?:\d+\s*)?"                                  # index SRT éventuel
    r"\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3}\s*-->\s*"
    r"\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3}.*$")
_RE_SRT_INDEX = re.compile(r"^\s*\d+\s*$")
_RE_VTT_HEADER = re.compile(r"^\s*(WEBVTT|NOTE|STYLE|REGION|Kind:|Language:)", re.I)
_RE_VOICE_TAG = re.compile(r"<v\s+([^>]+?)>", re.I)
_RE_ANY_TAG = re.compile(r"</?[cvbiu.][^>]*>|</v>", re.I)
_RE_SPEAKER_LINE = re.compile(r"^\s*([A-ZÀ-Ý][\w .'\-]{1,38}?)\s*[:：]\s*(.+)$")
_RE_BRACKET_NOISE = re.compile(r"\[(?:inaudible|silence|rires?|musique)[^\]]*\]", re.I)


@dataclass
class Utterance:
    speaker: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"speaker": self.speaker, "text": self.text}


def detect_format(content: str, filename: str = "") -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".vtt":
        return VTT
    if suffix == ".srt":
        return SRT
    if suffix == ".json":
        return JSON_FMT
    head = content.lstrip()[:400]
    if head.upper().startswith("WEBVTT"):
        return VTT
    if head.startswith(("{", "[")):
        return JSON_FMT
    if _RE_TIMESTAMP.search(content[:2000] or ""):
        return SRT if "," in (_RE_TIMESTAMP.search(content[:2000]).group(0) or "") else VTT
    return TEXT


def _clean_line(line: str) -> tuple[str, str]:
    """Renvoie (locuteur, texte) pour une ligne déjà débarrassée du minutage."""
    speaker = ""
    voice = _RE_VOICE_TAG.search(line)
    if voice:
        speaker = voice.group(1).strip()
    line = _RE_ANY_TAG.sub("", line)
    line = _RE_BRACKET_NOISE.sub(" ", line)
    line = line.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    if not speaker:
        match = _RE_SPEAKER_LINE.match(line)
        if match:
            speaker, line = match.group(1).strip(), match.group(2)
    return speaker, re.sub(r"\s+", " ", line).strip()


def _parse_cue_based(content: str) -> list[Utterance]:
    """VTT et SRT : on jette minutage, index, en-têtes et balises."""
    out: list[Utterance] = []
    current_speaker = ""
    for raw in content.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if _RE_VTT_HEADER.match(line) or _RE_TIMESTAMP.match(line) or _RE_SRT_INDEX.match(line):
            continue
        if "-->" in line:                      # minutage d'un format exotique
            continue
        speaker, text = _clean_line(line)
        if not text:
            continue
        if speaker:
            current_speaker = speaker
        out.append(Utterance(speaker or current_speaker, text))
    return out


def _parse_json(content: str) -> list[Utterance]:
    """Accepte les formes courantes sans en privilégier une seule."""
    try:
        data = json.loads(content)
    except Exception:
        return _parse_text(content)
    if isinstance(data, dict):
        for key in ("segments", "utterances", "transcript", "messages", "results", "entries"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        return []
    out: list[Utterance] = []
    for item in data:
        if isinstance(item, str):
            speaker, text = _clean_line(item)
            if text:
                out.append(Utterance(speaker, text))
            continue
        if not isinstance(item, dict):
            continue
        text = ""
        for key in ("text", "content", "utterance", "sentence", "value", "body"):
            if isinstance(item.get(key), str) and item[key].strip():
                text = item[key]
                break
        if not text:
            continue
        speaker = ""
        for key in ("speaker", "speaker_name", "from", "author", "name", "role"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                speaker = value.strip()
                break
        _, cleaned = _clean_line(text)
        if cleaned:
            out.append(Utterance(speaker, cleaned))
    return out


def _parse_text(content: str) -> list[Utterance]:
    out: list[Utterance] = []
    current_speaker = ""
    for raw in content.splitlines():
        if not raw.strip():
            continue
        if _RE_TIMESTAMP.match(raw):
            continue
        speaker, text = _clean_line(raw)
        if not text:
            continue
        if speaker:
            current_speaker = speaker
        out.append(Utterance(speaker or current_speaker, text))
    return out


def _norm(text: str) -> str:
    return re.sub(r"[^\w]+", " ", text.lower()).strip()


def dedupe_rolling(utterances: list[Utterance]) -> list[Utterance]:
    """Supprime les répétitions du sous-titrage en direct.

    Trois cas, dans cet ordre :
      1. cue identique à la précédente ;
      2. cue qui PROLONGE la précédente (préfixe grandissant) → on garde la
         plus longue ;
      3. cue qui reprend la fin de la précédente (fenêtre glissante) → on ne
         garde que la partie nouvelle.
    """
    out: list[Utterance] = []
    for item in utterances:
        if not out:
            out.append(item)
            continue
        prev = out[-1]
        if prev.speaker != item.speaker and item.speaker and prev.speaker:
            out.append(item)
            continue
        a, b = _norm(prev.text), _norm(item.text)
        if not b or a == b:
            continue                                  # doublon exact
        if b.startswith(a):
            out[-1] = Utterance(prev.speaker or item.speaker, item.text)   # prolongement
            continue
        if a.startswith(b):
            continue                                  # redite plus courte
        # Fenêtre glissante : la fin de `a` est le début de `b`.
        overlap = _longest_overlap(a, b)
        if overlap >= 12:
            words_b = item.text.split()
            keep = words_b[len(_overlap_words(a, b)):]
            if keep:
                out.append(Utterance(item.speaker or prev.speaker, " ".join(keep)))
            continue
        out.append(item)
    return out


def _longest_overlap(a: str, b: str) -> int:
    limit = min(len(a), len(b))
    for size in range(limit, 0, -1):
        if a.endswith(b[:size]):
            return size
    return 0


def _overlap_words(a: str, b: str) -> list[str]:
    words_a, words_b = a.split(), b.split()
    for size in range(min(len(words_a), len(words_b)), 0, -1):
        if words_a[-size:] == words_b[:size]:
            return words_b[:size]
    return []


def merge_same_speaker(utterances: list[Utterance]) -> list[Utterance]:
    out: list[Utterance] = []
    for item in utterances:
        if out and out[-1].speaker == item.speaker:
            joined = f"{out[-1].text} {item.text}".strip()
            out[-1] = Utterance(item.speaker, re.sub(r"\s+", " ", joined))
        else:
            out.append(item)
    return out


def parse(content: str, filename: str = "") -> dict[str, Any]:
    """Point d'entrée : texte brut → dialogue normalisé et dédupliqué."""
    fmt = detect_format(content or "", filename)
    if fmt in (VTT, SRT):
        raw = _parse_cue_based(content)
    elif fmt == JSON_FMT:
        raw = _parse_json(content)
    else:
        raw = _parse_text(content)
    deduped = dedupe_rolling(raw)
    # `utterances` reste au grain de la PHRASE. Fusionner les tours de parole
    # d'un même locuteur rend le texte plus lisible, mais fait perdre à
    # l'extraction la frontière entre deux prestations : une quantité énoncée
    # dans une phrase serait appliquée au montant de la suivante. La version
    # fusionnée ne sert donc qu'à l'affichage et à la passe LLM.
    merged = merge_same_speaker(deduped)
    speakers = []
    for u in deduped:
        if u.speaker and u.speaker not in speakers:
            speakers.append(u.speaker)
    return {
        "format": fmt,
        "utterances": deduped,
        "merged": merged,
        "speakers": speakers,
        "raw_cues": len(raw),
        "kept_cues": len(deduped),
        "text": "\n".join(f"{u.speaker}: {u.text}" if u.speaker else u.text for u in merged),
    }


def plain_text(parsed: dict[str, Any]) -> str:
    return str(parsed.get("text") or "")


# ===========================================================================
# Passe 1 — extraction déterministe (regex & heuristiques)
# ===========================================================================
# Montants français : « 5 000 € », « 5000 EUR », « 1 200,50 euros ».
# L'espace peut être normal, insécable ou fine insécable.
_SP = r"[\s  ]"
_RE_AMOUNT = re.compile(
    rf"(\d{{1,3}}(?:{_SP}\d{{3}})+|\d+)(?:[.,](\d{{1,2}}))?{_SP}*"
    r"(?:€|EUR\b|euros?\b)", re.I)
_RE_VAT = re.compile(rf"TVA{_SP}*(?:à|a|de)?{_SP}*(\d{{1,2}}(?:[.,]\d)?){_SP}*%", re.I)
_RE_DISCOUNT = re.compile(rf"remise{_SP}*(?:de|d')?{_SP}*(\d{{1,2}}(?:[.,]\d)?){_SP}*%", re.I)
_RE_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
_RE_PHONE = re.compile(r"(?:\+33|0)\s*\d(?:[\s.\-]*\d{2}){4}\b")
_RE_TTC = re.compile(r"\bTTC\b", re.I)
_RE_HT = re.compile(r"\bHT\b|hors\s+taxes?", re.I)
_RE_PER_UNIT = re.compile(r"\b(l'unit[ée]|chacune?|chacun|pi[eè]ce|par\s+\w+)\b", re.I)

_NUMBER_WORDS = {
    "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6,
    "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12,
}
_RE_QUANTITY = re.compile(
    r"\b(\d{1,3}|" + "|".join(_NUMBER_WORDS) + r")\s+"
    r"(d[ée]clinaisons?|jours?|journ[ée]es?|heures?|pages?|modules?|"
    r"s[ée]ances?|ateliers?|exemplaires?|unit[ée]s?|lots?|visuels?)\b", re.I)

# Béquilles de l'oral : elles polluent la désignation d'une ligne de devis.
_RE_FILLER = re.compile(
    r"\b(alors|donc|euh|bon|voil[àa]|du coup|en fait|ok|d'accord|"
    r"il faudrait|il faut|on part sur|je dirais|comptez|compter|"
    r"[çc]a fait|ce sera|c'est|environ|[àa] peu pr[èe]s)\b", re.I)


def _amount_to_decimal(match: "re.Match") -> Decimal:
    whole = re.sub(_SP, "", match.group(1))
    cents = match.group(2) or "0"
    return Decimal(f"{whole}.{cents.ljust(2, '0')[:2]}")


def _describe_from(text: str, amount_span: tuple) -> str:
    """Désignation = la phrase débarrassée du montant et des béquilles orales."""
    cleaned = text[:amount_span[0]] + " " + text[amount_span[1]:]
    cleaned = _RE_FILLER.sub(" ", cleaned)
    cleaned = _RE_QUANTITY.sub(lambda m: m.group(2), cleaned)
    cleaned = re.sub(r"\bHT\b|\bTTC\b|hors\s+taxes?", " ", cleaned, flags=re.I)
    # Le taux de TVA et les marqueurs de totalisation sont déjà captés comme
    # données : les laisser dans la désignation les ferait imprimer sur le devis.
    cleaned = _RE_VAT.sub(" ", cleaned)
    cleaned = re.sub(r"\b(au total|en tout|l'ensemble|sur l'ensemble|l'unit[ée]|"
                     r"chacune?|chacun|la pi[èe]ce)\b", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"[,;:.\-–—?!%]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,;:")
    # Les mots-outils s'empilent en tête (« pour le logo » → « logo ») et
    # restent en queue une fois le montant retiré (« déclinaisons à »).
    _EDGE = r"pour|de|des|du|le|la|les|un|une|et|avec|à|a|sur|en|par"
    while True:
        stripped = re.sub(rf"^(?:{_EDGE})\s+", "", cleaned, flags=re.I)
        stripped = re.sub(rf"\s+(?:{_EDGE})$", "", stripped, flags=re.I)
        if stripped == cleaned:
            break
        cleaned = stripped
    return (cleaned[:80].strip() or "Prestation")


def _sentences(text):
    """Decoupe une replique en phrases.

    On n'accepte une coupure qu'apres une ponctuation forte suivie d'une
    majuscule (ou un point-virgule) : sans cette garde, un montant ecrit
    « 1.500 » ou une abreviation couperait la phrase en deux et separerait
    un prix de sa designation.
    """
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZA-YÀ-Ý])|\s*;\s*", text)
    return [p.strip() for p in parts if p and p.strip()]


def extract_regex(parsed: dict) -> dict:
    """Passe déterministe : rapide, reproductible, et traçable — chaque élément
    conserve la phrase exacte dont il provient."""
    items: list = []
    emails: list = []
    phones: list = []
    discount_pct = None
    default_vat = None

    for utt in parsed.get("utterances", []):
        # Une meme replique peut porter deux prestations. On descend au
        # grain de la PHRASE, sinon la quantite et le taux de TVA de la
        # premiere contaminent le montant de la seconde.
        for text in _sentences(utt.text):
            for found in _RE_EMAIL.finditer(text):
                if found.group(0) not in emails:
                    emails.append(found.group(0))
            for found in _RE_PHONE.finditer(text):
                value = re.sub(r"[\s.\-]+", " ", found.group(0)).strip()
                if value not in phones:
                    phones.append(value)
            discount = _RE_DISCOUNT.search(text)
            if discount and discount_pct is None:
                discount_pct = Decimal(discount.group(1).replace(",", "."))
            vat = _RE_VAT.search(text)
            # Un taux énoncé DANS une phrase qui porte un montant ne vaut que
            # pour cette ligne-là. « Deux journées à 600 €, TVA à 10 % » ne
            # fait pas passer le logo à 10 % : seule une mention isolée
            # (« tout est à 20 % ») devient le taux par défaut du devis.
            if vat and default_vat is None and not _RE_AMOUNT.search(text):
                default_vat = Decimal(vat.group(1).replace(",", "."))

            for amount in _RE_AMOUNT.finditer(text):
                value = _amount_to_decimal(amount)
                quantity = 1
                qty = _RE_QUANTITY.search(text)
                if qty:
                    token = qty.group(1).lower()
                    quantity = int(token) if token.isdigit() else _NUMBER_WORDS.get(token, 1)
                # « à l'unité » / « chacun » : le montant EST unitaire. Sinon, un
                # montant énoncé avec une quantité est un total, qu'on ramène au
                # prix unitaire — sans quoi le devis est multiplié par la quantité.
                per_unit = bool(_RE_PER_UNIT.search(text))
                unit_price = value if (per_unit or quantity == 1) else value / quantity
                local_vat = _RE_VAT.search(text)
                items.append({
                    "description": _describe_from(text, amount.span()),
                    "quantity": quantity,
                    "unit_price": str(unit_price.quantize(Decimal("0.01"))),
                    "vat_rate": (str(Decimal(local_vat.group(1).replace(",", ".")))
                                 if local_vat else None),
                    "amount_is_ttc": bool(_RE_TTC.search(text)) and not _RE_HT.search(text),
                    "source": "regex",
                    "evidence": text[:160],
                    "speaker": utt.speaker,
                })

    return {
        "items": items,
        "emails": emails,
        "phones": phones,
        "discount_pct": str(discount_pct) if discount_pct is not None else None,
        "default_vat_rate": str(default_vat) if default_vat is not None else None,
    }


# ===========================================================================
# Passe 2 — complément LLM (optionnel, lecture seule)
# ===========================================================================
_LLM_PROMPT = (
    "Tu analyses la transcription d'un appel commercial et tu en extrais les "
    "éléments chiffrables d'un devis.\n"
    "Réponds UNIQUEMENT par un objet JSON valide, sans texte autour, de la forme :\n"
    '{"client_name": "", "company": "", "items": [{"description": "", '
    '"quantity": 1, "unit_price": 0, "vat_rate": 20}], "discount_pct": 0, "notes": ""}\n'
    "Règles strictes :\n"
    "- N'invente AUCUN montant. Si un prix n'est pas énoncé, omets la ligne.\n"
    "- unit_price est le prix UNITAIRE hors taxes, en euros, sans symbole.\n"
    "- Si aucune TVA n'est mentionnée, utilise 20.\n"
    "- Si le client n'est pas nommé, laisse client_name vide.\n"
    "- items peut être une liste vide."
)


def _first_json_object(text: str):
    """Premier objet JSON équilibré : les modèles encadrent souvent leur
    réponse d'une clôture de code ou d'une phrase d'introduction."""
    start = text.find("{")
    while start != -1:
        depth, in_string, escape = 0, False, False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:index + 1])
                    except Exception:
                        break
        start = text.find("{", start + 1)
    return None


def extract_llm(core, transcript_text: str, timeout: float = 90.0) -> dict:
    """Complément conversationnel. Échoue proprement : la passe 1 fait foi."""
    try:
        from .llm.base import ChatMessage
    except Exception as exc:
        return {"ok": False, "error": f"LLM indisponible : {exc}"[:200], "items": []}
    try:
        response = core.llm.chat(
            [ChatMessage(role="system", content=_LLM_PROMPT),
             ChatMessage(role="user", content=transcript_text[:12000])],
            role="fast", temperature=0.1, timeout=timeout)
    except Exception as exc:
        return {"ok": False, "error": f"Appel au modèle impossible : {exc}"[:200], "items": []}
    if not getattr(response, "ok", False):
        return {"ok": False, "error": (getattr(response, "error", "") or "réponse vide")[:200],
                "items": []}
    payload = _first_json_object(response.text or "")
    if not isinstance(payload, dict):
        return {"ok": False, "error": "Le modèle n'a pas renvoyé de JSON exploitable.", "items": []}

    items = []
    for raw in (payload.get("items") or []):
        if not isinstance(raw, dict):
            continue
        description = str(raw.get("description") or "").strip()
        price = raw.get("unit_price")
        if not description or price in (None, "", 0, "0"):
            continue                      # aucun prix énoncé → aucune ligne
        try:
            unit_price = str(Decimal(str(price).replace(",", ".")).quantize(Decimal("0.01")))
        except Exception:
            continue                      # prix illisible : on ne devine pas
        items.append({
            "description": description[:80],
            "quantity": raw.get("quantity") or 1,
            "unit_price": unit_price,
            "vat_rate": str(raw.get("vat_rate")) if raw.get("vat_rate") is not None else None,
            "source": "llm",
            "evidence": "",
            "speaker": "",
        })
    return {
        "ok": True,
        "client_name": str(payload.get("client_name") or "").strip(),
        "company": str(payload.get("company") or "").strip(),
        "items": items,
        "discount_pct": payload.get("discount_pct"),
        "notes": str(payload.get("notes") or "").strip(),
    }


# ===========================================================================
# Assemblage : charge utile prête pour pdf.generate_quote
# ===========================================================================
def _dedupe_items(items: list) -> list:
    """Une prestation trouvée par les DEUX passes ne doit compter qu'une fois.
    La ligne `regex` l'emporte : elle porte la phrase d'origine."""
    out: list = []
    for item in items:
        key = (_norm(item["description"])[:40], str(item["unit_price"]))
        match = next((o for o in out
                      if (_norm(o["description"])[:40], str(o["unit_price"])) == key), None)
        if match is None:
            out.append(item)
        elif match["source"] == "llm" and item["source"] == "regex":
            out[out.index(match)] = item
    return out


def build_quote_payload(parsed: dict, regex_result: dict, llm_result: dict = None) -> dict:
    """Charge utile directement consommable par `pdf.generate_quote`."""
    llm_result = llm_result or {}
    default_vat = regex_result.get("default_vat_rate")
    items = _dedupe_items(list(regex_result.get("items") or [])
                          + list(llm_result.get("items") or []))
    lines = [{
        "description": item["description"],
        "quantity": item.get("quantity") or 1,
        "unit_price": item["unit_price"],
        "vat_rate": item.get("vat_rate") or default_vat or "20",
    } for item in items]

    discount = regex_result.get("discount_pct") or llm_result.get("discount_pct") or 0
    return {
        # On ne devine pas le client au-delà de ce qui est nommé :
        # `crm.search_contact` tranchera, avec sa levée d'ambiguïté.
        "client_name": str(llm_result.get("client_name") or "").strip(),
        "company": str(llm_result.get("company") or "").strip(),
        "speakers": list(parsed.get("speakers") or []),
        "lines": lines,
        "discount_pct": discount,
        "emails": regex_result.get("emails") or [],
        "phones": regex_result.get("phones") or [],
        "notes": str(llm_result.get("notes") or ""),
        "sources": {"regex": sum(1 for i in items if i["source"] == "regex"),
                    "llm": sum(1 for i in items if i["source"] == "llm")},
        "items": items,
    }
