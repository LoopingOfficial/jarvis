"""AvatarEngine — le LLM choisit, le moteur exécute.

API haut niveau. Aucun script bpy improvisé par le modèle.
"""
from __future__ import annotations

from .engine import AvatarEngine
from .schema import BUILD_ID, OPERATIONS, PRESETS

__all__ = ["AvatarEngine", "BUILD_ID", "OPERATIONS", "PRESETS"]
