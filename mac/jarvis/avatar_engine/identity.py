"""Identité 3D : CURRENT (live) vs CANDIDATE vs MASTER / BASE.

Le mannequin actuellement affiché dans le Command Center n'est JAMAIS écrasé
tant que l'utilisateur n'a pas accepté le candidat.
"""
from __future__ import annotations

from pathlib import Path

from ..config import DATA_DIR, ROOT, UI_DIR

BASE_BLEND = ROOT / "assets" / "avatar" / "jarvis_base.blend"
MASTER_BLEND = ROOT / "assets" / "avatar" / "jarvis_master.blend"
LEGACY_MASTER = ROOT / "assets" / "blender" / "jarvis_avatar.blend"
LIVE_GLB = UI_DIR / "assets" / "avatar" / "jarvis_avatar.glb"
CANDIDATE_DIR = DATA_DIR / "generated" / "avatar" / "candidate"
LIBRARY_DIR = ROOT / "assets" / "avatar" / "library"


def ensure_dirs() -> None:
    for path in (
        CANDIDATE_DIR,
        LIBRARY_DIR / "hair",
        LIBRARY_DIR / "clothes",
        LIBRARY_DIR / "shoes",
        LIBRARY_DIR / "materials",
        BASE_BLEND.parent,
    ):
        path.mkdir(parents=True, exist_ok=True)


def source_blend() -> Path | None:
    """Fichier de travail : master validé, sinon base, sinon legacy (lecture seule)."""
    for path in (MASTER_BLEND, BASE_BLEND, LEGACY_MASTER):
        if path.is_file():
            return path
    return None


def candidate_blend() -> Path:
    return CANDIDATE_DIR / "jarvis_candidate.blend"


def candidate_glb() -> Path:
    return CANDIDATE_DIR / "jarvis_candidate.glb"


def snapshot(kind: str) -> dict[str, str]:
    ensure_dirs()
    src = source_blend()
    return {
        "current_glb": str(LIVE_GLB) if LIVE_GLB.is_file() else "",
        "candidate_glb": str(candidate_glb()) if candidate_glb().is_file() else "",
        "candidate_blend": str(candidate_blend()) if candidate_blend().is_file() else "",
        "master_blend": str(MASTER_BLEND) if MASTER_BLEND.is_file() else "",
        "base_blend": str(BASE_BLEND) if BASE_BLEND.is_file() else "",
        "source_blend": str(src) if src else "",
        "live_protected": str(LIVE_GLB),
        "role": kind,
    }
