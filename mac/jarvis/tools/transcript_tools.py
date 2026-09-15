"""Outil de lecture de transcripts d'appels et de pré-remplissage de devis.

Chaîne complète :
    fichier (.vtt/.srt/.json/.txt)
      → normalisation + déduplication des cues roulantes  (jarvis/transcripts)
      → passe 1 regex (déterministe)  [+ passe 2 LLM, optionnelle]
      → charge utile prête pour `pdf.generate_quote`
      → résolution du client par le CRM, avec levée d'ambiguïté

Rien n'est généré ici : l'outil prépare et rend la main. La génération reste
`pdf.generate_quote`, qui exige sa confirmation. Un devis produit d'un trait
depuis un transcript, sans que personne ne relise les montants extraits d'une
conversation, serait exactement le raccourci à ne pas prendre.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..crm import AMBIGUOUS, NOT_FOUND, clarification_question
from ..permissions import READ_ONLY
from ..transcripts import (
    SUPPORTED_SUFFIXES, build_quote_payload, extract_llm, extract_regex, parse,
)
from .base import ToolContext, ToolResult, registry

MAX_BYTES = 2_000_000


def _load_content(ctx: ToolContext) -> tuple[str, str, str | None]:
    """Renvoie (contenu, nom de fichier, erreur)."""
    args = ctx.arguments
    inline = args.get("content")
    if isinstance(inline, str) and inline.strip():
        return inline, str(args.get("filename") or "transcript.txt"), None

    attachment_id = str(args.get("attachment_id") or "").strip()
    if attachment_id:
        try:
            att = ctx.core.attachments.get(attachment_id)
        except Exception:
            att = None
        if not att:
            return "", "", f"Pièce jointe introuvable : {attachment_id}."
        try:
            extracted = ctx.core.attachments.extract(att)
        except Exception as exc:
            return "", "", f"Lecture de la pièce jointe impossible : {exc}"[:200]
        text = str(extracted.get("text") or extracted.get("content") or "")
        return text, getattr(att, "filename", "transcript.txt"), None

    path_arg = str(args.get("path") or "").strip()
    if not path_arg:
        return "", "", "Fournis un transcript : `path`, `attachment_id` ou `content`."
    path = Path(path_arg).expanduser()
    if not path.is_file():
        return "", "", f"Fichier introuvable : {path_arg}"
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        return "", "", (f"Format non pris en charge : {path.suffix or 'sans extension'}. "
                        f"Attendu : {', '.join(sorted(SUPPORTED_SUFFIXES))}.")
    if path.stat().st_size > MAX_BYTES:
        return "", "", f"Transcript trop volumineux ({path.stat().st_size // 1024} Ko, max 2 Mo)."
    return path.read_text(encoding="utf-8", errors="replace"), path.name, None


def _resolve_client(ctx: ToolContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Cherche le client dans le CRM. Ne tranche jamais une ambiguïté."""
    store = getattr(ctx.core, "crm", None)
    candidates = [payload.get("client_name"), payload.get("company")]
    # À défaut, un locuteur : on les propose tous plutôt que d'en élire un.
    candidates += [s for s in payload.get("speakers") or []]
    for query in [c for c in candidates if c and str(c).strip()]:
        if store is None:
            break
        result = store.resolve(str(query))
        if result["status"] == AMBIGUOUS:
            question = clarification_question(result)
            try:
                ctx.core.events.emit("crm.clarification.needed", {
                    "query": query, "options": result["options"], "question": question,
                    "task_id": ctx.task_id})
            except Exception:
                pass
            return {"status": AMBIGUOUS, "query": query,
                    "options": result["options"], "question": question}
        if result["status"] != NOT_FOUND:
            return {"status": "found", "contact": result["contact"]}
    return {"status": NOT_FOUND, "tried": [c for c in candidates if c]}


def _parse_transcript(ctx: ToolContext) -> ToolResult:
    content, filename, error = _load_content(ctx)
    if error:
        return ToolResult(False, error)
    if not content.strip():
        return ToolResult(False, "Le transcript est vide.")

    parsed = parse(content, filename)
    if not parsed["utterances"]:
        return ToolResult(False, "Aucune réplique exploitable dans ce transcript.")

    regex_result = extract_regex(parsed)

    # Passe 2 : complément conversationnel. Optionnelle par construction —
    # si le modèle ne répond pas, la passe déterministe fait foi.
    llm_result: dict[str, Any] = {}
    llm_note = ""
    if ctx.arguments.get("use_llm", True):
        llm_result = extract_llm(ctx.core, parsed["text"])
        if not llm_result.get("ok"):
            llm_note = f" (complément LLM indisponible : {llm_result.get('error', '')})"
            llm_result = {}

    payload = build_quote_payload(parsed, regex_result, llm_result)
    payload["filename"] = filename
    payload["format"] = parsed["format"]
    payload["raw_cues"] = parsed["raw_cues"]
    payload["kept_cues"] = parsed["kept_cues"]
    payload["client"] = _resolve_client(ctx, payload)

    try:
        ctx.core.events.emit("transcript.parsed", {
            "filename": filename, "format": parsed["format"],
            "lines": len(payload["lines"]), "sources": payload["sources"],
            "client": payload["client"].get("status"), "task_id": ctx.task_id})
    except Exception:
        pass

    if not payload["lines"]:
        return ToolResult(
            True,
            f"Transcript lu ({parsed['format'].upper()}, {parsed['kept_cues']} répliques) "
            f"mais aucun montant n'y est énoncé : impossible de pré-remplir un devis."
            + llm_note,
            data=payload)

    client_status = payload["client"]["status"]
    if client_status == AMBIGUOUS:
        headline = payload["client"]["question"]
    elif client_status == "found":
        contact = payload["client"]["contact"]
        headline = f"Client : {contact.get('name')} ({contact.get('company') or 'particulier'})."
    else:
        headline = "Client non identifié dans le CRM — précise-le avant de générer le devis."

    detail = "\n".join(
        f"- {l['description']} : {l['quantity']} x {l['unit_price']} € HT (TVA {l['vat_rate']} %)"
        for l in payload["lines"])
    discount = payload.get("discount_pct") or 0
    summary = (f"{len(payload['lines'])} prestation(s) chiffrée(s) dans {filename} :\n{detail}"
               + (f"\nRemise évoquée : {discount} %" if discount else "")
               + f"\n{headline}"
               + "\nVérifie ces montants avant de générer le devis." + llm_note)
    return ToolResult(True, summary, data=payload)


registry.add(
    id="document.parse_transcript", name="Lire un transcript d'appel", category="Documents",
    description=(
        "Lit un compte rendu d'appel ou un fichier de sous-titres (.vtt, .srt, .json, .txt), "
        "en retire le minutage et les répétitions du sous-titrage en direct, puis extrait "
        "les prestations chiffrées (désignation, quantité, prix unitaire HT, TVA), la remise "
        "évoquée et les coordonnées. Renvoie une charge utile directement utilisable par "
        "pdf.generate_quote, et cherche le client dans le CRM. NE génère aucun document : "
        "fais relire les montants à l'utilisateur, puis appelle pdf.generate_quote."
    ),
    handler=_parse_transcript, risk=READ_ONLY, agents=("jarvis", "email", "task"),
    input_schema={"type": "object", "properties": {
        "path": {"type": "string", "description": "Chemin du fichier transcript."},
        "attachment_id": {"type": "string", "description": "Identifiant d'une pièce jointe envoyée."},
        "content": {"type": "string", "description": "Contenu brut du transcript."},
        "filename": {"type": "string", "description": "Nom de fichier, utilisé pour détecter le format."},
        "use_llm": {"type": "boolean",
                    "description": "Compléter l'extraction déterministe par le modèle (défaut vrai)."}},
        "required": []},
)
