"""Contrats et helpers du pipeline d'analyse en lecture seule.

Le résultat d'une analyse n'est jamais un document éditable.  Ce module garde
donc explicitement séparés la source lue et le rapport produit par le modèle.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from typing import Any


@dataclass(frozen=True)
class SourceDocument:
    path: str
    content: str
    hash: str
    size: int

    @classmethod
    def from_content(cls, path: str, content: str) -> "SourceDocument":
        raw = content or ""
        return cls(path=path, content=raw,
                   hash=sha256(raw.encode("utf-8")).hexdigest(), size=len(raw))


@dataclass
class AnalysisFinding:
    severity: str = "info"
    category: str = ""
    line: int | None = None
    evidence: str = ""
    risk: str = ""
    recommendation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AnalysisResult:
    file: str
    read_only: bool = True
    findings: list[AnalysisFinding] = field(default_factory=list)
    summary: str = ""
    recommendations: list[str] = field(default_factory=list)
    source_hash_before: str = ""
    source_hash_after: str = ""
    source_size: int = 0
    chunks: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "read_only": self.read_only,
            "findings": [finding.to_dict() for finding in self.findings],
            "summary": self.summary,
            "recommendations": list(self.recommendations),
            "source_hash_before": self.source_hash_before,
            "source_hash_after": self.source_hash_after,
            "source_size": self.source_size,
            "chunks": self.chunks,
        }


READ_ONLY_ALLOWED_TOOLS = {
    "ssh.read_file", "ssh.list", "ssh.search", "ssh.stat", "ssh.logs", "ssh.status",
    "fs.read", "fs.list", "fs.search", "filesystem.read",
    "security.scan_file", "security.scan_project", "security.dependencies", "security.secrets",
    "security.permissions", "security.headers", "security.report",
    "google.sheets.read",
    # Mode « lire/appliquer la page » : naviguer et observer reste en LECTURE.
    "browser.navigate", "browser.wait", "browser.back", "browser.scroll",
    "browser.pause", "browser.close",
}

READ_ONLY_DENIED_TOOLS = {
    "ssh.write_file", "ssh.create_file", "ssh.delete_file", "ssh.move", "ssh.rename",
    "filesystem.write", "fs.write", "apply_patch", "replace_file", "deploy", "service.restart",
    "ssh.upload", "ssh.service", "database.write",
}


def split_into_chunks(content: str, *, size: int = 24000, overlap: int = 1200) -> list[tuple[int, str]]:
    """Découpe un fichier en fenêtres avec un léger recouvrement de contexte."""
    text = content or ""
    if not text:
        return [(1, "")]
    size = max(1000, int(size))
    overlap = max(0, min(int(overlap), size // 3))
    step = size - overlap
    chunks: list[tuple[int, str]] = []
    start = 0
    while start < len(text):
        chunks.append((start + 1, text[start:start + size]))
        if start + size >= len(text):
            break
        start += step
    return chunks


def parse_findings(text: str) -> list[AnalysisFinding]:
    """Extrait une liste JSON tolérante depuis une réponse de modèle."""
    raw = (text or "").strip()
    candidates = [raw]
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.I | re.S)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            payload = payload.get("findings")
        if not isinstance(payload, list):
            continue
        findings: list[AnalysisFinding] = []
        for item in payload:
            if not isinstance(item, dict) or any(
                    key not in item for key in ("severity", "category", "line", "evidence", "risk", "recommendation")):
                raise ValueError("Finding incomplet.")
            severity = str(item.get("severity") or "info").lower()
            if severity not in {"critical", "high", "medium", "low", "info"}:
                raise ValueError("Sévérité invalide.")
            line = item.get("line")
            try:
                line = int(line) if line is not None else None
            except (TypeError, ValueError):
                raise ValueError("Ligne invalide.")
            findings.append(AnalysisFinding(
                severity=severity,
                category=str(item.get("category") or "security_review"),
                line=line,
                evidence=str(item.get("evidence") or "")[:1200],
                risk=str(item.get("risk") or "")[:1200],
                recommendation=str(item.get("recommendation") or "")[:1200],
            ))
        return findings
    raise ValueError("Rapport JSON invalide ou champ findings manquant.")


def deduplicate_findings(findings: list[AnalysisFinding]) -> list[AnalysisFinding]:
    seen: set[tuple[str, str, int | None]] = set()
    result: list[AnalysisFinding] = []
    for finding in findings:
        key = (finding.category.casefold(), finding.evidence.casefold(), finding.line)
        if key in seen:
            continue
        seen.add(key)
        result.append(finding)
    return result
