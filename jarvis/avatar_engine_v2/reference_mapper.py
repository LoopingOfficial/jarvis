"""Reference -> constraints -> parameters. Never creates mesh geometry."""
from __future__ import annotations

from typing import Any

from .parameters import JarvisAvatarParameters


def map_reference_constraints(features: dict[str, Any] | None) -> JarvisAvatarParameters:
    features = features or {}
    if features.get("analysis_success") is False:
        raise ValueError("Vision analysis failed; MPFB build stopped")
    raw = features.get("avatar_parameters")
    if not isinstance(raw, dict):
        raw = {}
    return JarvisAvatarParameters.from_dict(raw)
