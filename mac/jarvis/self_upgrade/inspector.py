"""Inspecteur de projet : recherche, lecture ciblée et contexte comprimé."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

MAX_READ_LINES = 800
MAX_CONTEXT_CHARS = int(os.getenv("JARVIS_SU_CONTEXT_CHARS", "24000"))


class ProjectInspector:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    # -- listing / recherche ---------------------------------------------
    def list_files(self, glob_pattern: str = "**/*.py") -> list[str]:
        try:
            return sorted(str(p.relative_to(self._root)).replace("\\", "/")
                          for p in self._root.glob(glob_pattern)
                          if p.is_file() and not any(part.startswith(".") or part in {
                              ".venv", "node_modules", "__pycache__", ".git", "upgrade-workspaces",
                              "releases", "backups", "state"} for part in p.relative_to(self._root).parts))
        except Exception:
            return []

    def search(
        self,
        pattern: str,
        include: str = "*.py",
        path: str = "",
        max_results: int = 40,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            return []
        base = (self._root / path) if path else self._root
        if not base.is_dir():
            if base.is_file() and regex.search(base.read_text(encoding="utf-8", errors="replace")):
                return [{"file": str(base.relative_to(self._root)).replace("\\", "/"), "line": 1, "text": pattern}]
            return []
        for p in base.rglob("*.py") if include == "*.py" else base.rglob(include):
            if any(part.startswith(".") or part in {".venv", "node_modules", "__pycache__",
                                                    ".git", "upgrade-workspaces"} for part in p.relative_to(self._root).parts):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    results.append({
                        "file": str(p.relative_to(self._root)).replace("\\", "/"),
                        "line": i,
                        "text": line.strip()[:220],
                    })
                    if len(results) >= max_results:
                        return results
        return results

    def read_file(self, rel_path: str, max_lines: int = MAX_READ_LINES) -> str:
        p = self._root / rel_path
        if not p.exists():
            return ""
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
        lines = text.splitlines()
        truncated = len(lines) > max_lines
        kept = "\n".join(lines[:max_lines])
        if truncated:
            kept += f"\n# … fichier tronqué ({len(lines) - max_lines} lignes en moins)."
        return kept

    def chunk_file(self, rel_path: str, chunk_size: int = 400) -> list[str]:
        text = self.read_file(rel_path, max_lines=10_000_000)
        lines = text.splitlines()
        return ["\n".join(lines[i:i + chunk_size]) for i in range(0, len(lines), chunk_size)]

    # -- contexte --------------------------------------------------------
    def build_coder_context(
        self,
        changes_overview: str = "",
        files_to_read: list[str] | None = None,
        files_to_write: list[str] | None = None,
    ) -> str:
        parts: list[str] = []
        parts.append(f"PROJECT ROOT: {self._root}")
        parts.append("RULES: never modify path under supervisor/, .env, data/, state/, backups/, releases/.")
        if changes_overview:
            parts.append(f"TASK:\n{changes_overview[:3000]}")
        read_files = files_to_read or []
        if read_files:
            for rel in read_files[:8]:
                body = self.read_file(rel)
                if body:
                    parts.append(f"\n----- FILE: {rel} -----\n{body[:MAX_CONTEXT_CHARS // max(1, len(read_files) * 2)]}")
        write_files = files_to_write or []
        if write_files:
            parts.append("\nTARGET FILES (to create/modify): " + ", ".join(write_files))
        context = "\n".join(parts)
        if len(context) > MAX_CONTEXT_CHARS:
            context = context[:MAX_CONTEXT_CHARS] + "\n… (contexte tronqué)"
        return context

    def find_class(self, rel_path: str, class_name: str) -> dict[str, Any] | None:
        p = self._root / rel_path
        if not p.exists():
            return None
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, marker in enumerate(lines):
            if re.match(rf"\s*(?:class|async\s+def|def)\s+{re.escape(class_name)}\b", marker):
                start = i
                end = i
                depth = 0
                for j in range(i + 1, min(i + 200, len(lines))):
                    depth += lines[j].count("{") - lines[j].count("}") if "{" in lines[j] else 0
                    end = j
                    if "def " in lines[j] and j > start + 1 and depth <= 0:
                        break
                return {"file": rel_path, "line": start + 1,
                        "code": "\n".join(lines[start:end + 1])[:4000]}
        return None