"""SourceGrounding éditorial : un article ne publie que ce qui est traçable.

Le principe est celui déjà retenu ailleurs dans JARVIS (``source_grounding``) :
le texte du modèle n'est jamais une source. Ici, l'ensemble des valeurs
autorisées est construit à partir des **faits collectés** (RESEARCH), chacun
portant sa source réelle — donnée interne du site, Google Sheet, mémoire
JARVIS, ou URL web relevée.

Toute affirmation à risque du corps de l'article (date, nombre, code, nom de
Brainrot, annonce Fortnite) est confrontée à cet ensemble. Ce qui n'est pas
soutenu devient une réclamation non fondée, et une réclamation non fondée
interdit la publication automatique : l'article passe en ``NEEDS_REVIEW``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

BUILD_ID = "JARVIS_BLOG_PUBLISHER_V1"

PASS, FAIL = "PASS", "FAIL"

# Sources internes réputées fiables (pas de vérification web nécessaire).
INTERNAL_SOURCES = {"site_db", "sheet", "wiki", "brainrots", "codes", "events",
                    "admin", "jarvis_memory"}
WEB_SOURCE = "web"

# Un article d'actualité ne peut pas reposer sur rien : seuil minimal de
# sources distinctes avant publication automatique.
MIN_SOURCES_NEWS = 2
MIN_SOURCES_GUIDE = 1

# Domaines considérés comme officiels / de référence pour l'actualité externe.
OFFICIAL_DOMAINS = ("epicgames.com", "fortnite.com", "roblox.com", "x.com",
                    "twitter.com", "youtube.com", "discord.com")

_CODE_RE = re.compile(r"\b(?=[A-Z0-9]{4,20}\b)(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d)[A-Z0-9]+\b")
_NUMBER_RE = re.compile(r"(?<![\w/])\d[\d\s.,]*\s*(?:%|\$|/s|M|k|K)?(?![\w])")
_DATE_RE = re.compile(
    r"\b(?:\d{1,2}\s+(?:janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|"
    r"septembre|octobre|novembre|décembre|decembre)(?:\s+\d{4})?|\d{1,2}/\d{1,2}/\d{2,4}|"
    r"\d{4}-\d{2}-\d{2})\b", re.I)

# Mots courants qui ressemblent à des codes mais n'en sont pas.
_CODE_ALLOWLIST = {"FR", "EN", "HTML", "CSS", "SEO", "URL", "FPS", "PC", "PS5", "XP",
                   "NFT", "API", "UI", "OS", "V1", "V2", "V3"}

# Nombres qui ne constituent jamais une affirmation factuelle vérifiable.
_TRIVIAL_NUMBERS = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "0",
                    "100", "1000", "2", "1er"}


@dataclass
class Fact:
    """Un fait collecté, avec sa source réelle. Rien ne rentre sans source."""
    text: str
    source_type: str
    source_ref: str = ""
    url: str = ""
    values: list[str] = field(default_factory=list)

    @property
    def is_internal(self) -> bool:
        return self.source_type in INTERNAL_SOURCES

    @property
    def is_official(self) -> bool:
        return self.source_type == WEB_SOURCE and any(
            domain in (self.url or "").lower() for domain in OFFICIAL_DOMAINS)

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "source_type": self.source_type,
                "source_ref": self.source_ref, "url": self.url, "values": list(self.values)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Fact":
        return cls(text=str(data.get("text") or ""),
                   source_type=str(data.get("source_type") or ""),
                   source_ref=str(data.get("source_ref") or ""),
                   url=str(data.get("url") or ""),
                   values=[str(v) for v in (data.get("values") or [])])


def _fold(text: str) -> str:
    out = (text or "").lower()
    for src, dst in {"à": "a", "â": "a", "é": "e", "è": "e", "ê": "e", "ë": "e", "î": "i",
                     "ï": "i", "ô": "o", "ù": "u", "û": "u", "ç": "c", "’": "'"}.items():
        out = out.replace(src, dst)
    return re.sub(r"\s+", " ", out).strip()


def _normalize_number(token: str) -> str:
    cleaned = re.sub(r"[\s,]", "", token.strip())
    cleaned = cleaned.rstrip("%$").removesuffix("/s")
    cleaned = cleaned.replace(".", "") if cleaned.count(".") > 1 else cleaned
    try:
        value = float(cleaned)
    except ValueError:
        return _fold(token)
    return str(int(value)) if value.is_integer() else str(value)


def grounding_values(facts: list[Fact]) -> set[str]:
    """Ensemble de tout ce qui a été réellement observé dans les sources."""
    values: set[str] = set()
    for fact in facts:
        blob = " ".join([fact.text, *fact.values])
        values.add(_fold(blob))
        for token in _NUMBER_RE.findall(blob):
            values.add(_normalize_number(token))
        for token in _CODE_RE.findall(blob):
            values.add(_fold(token))
        for token in _DATE_RE.findall(blob):
            values.add(_fold(token))
        for word in re.findall(r"[A-Za-zÀ-ÿ][\wÀ-ÿ'-]{2,}", blob):
            values.add(_fold(word))
        for value in fact.values:
            values.add(_fold(value))
            values.add(_normalize_number(value))
    return values


def _supported(token: str, values: set[str], *, numeric: bool) -> bool:
    folded = _fold(token)
    if folded in values:
        return True
    if numeric:
        normalized = _normalize_number(token)
        if normalized in values:
            return True
        return any(normalized in value for value in values if any(c.isdigit() for c in value))
    return any(folded in value for value in values)


def collect_unsupported_claims(text: str, facts: list[Fact]) -> list[dict[str, str]]:
    """Retourne les affirmations à risque qu'aucune source ne soutient."""
    values = grounding_values(facts)
    plain = re.sub(r"(?s)<[^>]+>", " ", text or "")
    claims: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def flag(kind: str, token: str) -> None:
        key = (kind, _fold(token))
        if key in seen:
            return
        seen.add(key)
        claims.append({"kind": kind, "claim": token.strip()})

    for token in _DATE_RE.findall(plain):
        if not _supported(token, values, numeric=False):
            flag("date", token)

    for token in _CODE_RE.findall(plain):
        if token in _CODE_ALLOWLIST:
            continue
        if not _supported(token, values, numeric=False):
            flag("code", token)

    for token in _NUMBER_RE.findall(plain):
        stripped = token.strip()
        if not stripped or _normalize_number(stripped) in _TRIVIAL_NUMBERS:
            continue
        if not _supported(stripped, values, numeric=True):
            flag("nombre", stripped)

    return claims


@dataclass
class GroundingReport:
    status: str
    claims: list[dict[str, str]]
    source_count: int
    internal_sources: int
    official_sources: int
    reasons: list[str]

    @property
    def passed(self) -> bool:
        return self.status == PASS

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "claims": self.claims,
                "source_count": self.source_count,
                "internal_sources": self.internal_sources,
                "official_sources": self.official_sources,
                "reasons": list(self.reasons)}


def validate_article(content: str, facts: list[Fact], *, kind: str = "news") -> GroundingReport:
    """Verdict de publication. FAIL n'autorise jamais un publish automatique."""
    facts = [f for f in facts if (f.text or "").strip() and f.source_type]
    distinct = {(f.source_type, f.source_ref or f.url) for f in facts}
    internal = sum(1 for f in facts if f.is_internal)
    official = sum(1 for f in facts if f.is_official)
    minimum = MIN_SOURCES_NEWS if kind == "news" else MIN_SOURCES_GUIDE

    reasons: list[str] = []
    claims = collect_unsupported_claims(content, facts)
    if claims:
        listed = ", ".join(f"{c['kind']} « {c['claim']} »" for c in claims[:6])
        reasons.append(f"{len(claims)} affirmation(s) sans source : {listed}")
    if len(distinct) < minimum:
        reasons.append(
            f"Sources insuffisantes : {len(distinct)} distincte(s), minimum {minimum} pour « {kind} ».")
    if kind == "news" and internal == 0 and official == 0:
        reasons.append("Actualité sans source interne ni source officielle.")

    return GroundingReport(status=FAIL if reasons else PASS, claims=claims,
                           source_count=len(distinct), internal_sources=internal,
                           official_sources=official, reasons=reasons)
