"""Chemin protégés et allowlist pour les upgrades."""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parent.parent.parent

# Patterns de fichiers/dossiers que les upgrades ne peuvent JAMAIS modifier.
# Les chemins sont relatifs à la racine du projet.
PROTECTED_PATTERNS: list[str] = [
    "supervisor/*",
    "supervisor",
    ".env",
    ".env.*",
    "*.secret",
    "*.credential",
    "data/*.db",
    "data/*.db-wal",
    "data/*.db-shm",
    "state/*",
    "state",
    "backups/*",
    "backups",
    "releases/*",
    "releases",
    "upgrade-workspaces/*",
    "upgrade-workspaces",
    "jarvis/self_upgrade/*",
    "jarvis/self_upgrade",
    ".git/*",
    ".git",
]

# Zones autorisées à la modification (patterns relatifs à ROOT).
ALLOWED_ZONES: list[str] = [
    "jarvis/*.py",
    "jarvis/**/*.py",
    "ui/**/*",
    "tests/*.py",
    "tests/**/*.py",
    "requirements.txt",
    "requirements-audio.txt",
    "README.md",
    ".gitignore",
]


def _normalise(rel: str) -> str:
    return rel.replace("\\", "/").strip("/")


def is_protected(project_root: Path, rel_path: str) -> bool:
    normed = _normalise(rel_path)
    for pat in PROTECTED_PATTERNS:
        if fnmatch.fnmatch(normed, pat):
            return True
    parts = normed.split("/")
    for i in range(len(parts)):
        prefix = "/".join(parts[: i + 1])
        for pat in PROTECTED_PATTERNS:
            if fnmatch.fnmatch(prefix, pat):
                return True
    return False


def is_allowed(project_root: Path, rel_path: str) -> bool:
    normed = _normalise(rel_path)
    for pat in ALLOWED_ZONES:
        if fnmatch.fnmatch(normed, pat):
            return True
    return False


def validate_paths(project_root: Path, paths: Sequence[str]) -> list[str]:
    errors: list[str] = []
    for p in paths:
        if is_protected(project_root, p):
            errors.append(f"PROTECTED: {p}")
        elif not is_allowed(project_root, p):
            errors.append(f"NOT_ALLOWED: {p}")
    return errors
