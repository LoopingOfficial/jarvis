"""Preset loading and deterministic generation entry point."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .parameters import JarvisAvatarParameters

BUILD_ID = "JARVIS_MPFBA_V1_20260911_A"
PRESET_ID = "jarvis_v1"
PRESET_PATH = Path(__file__).resolve().parents[2] / "presets" / "jarvis_v1.json"


def load_preset(name: str = PRESET_ID) -> JarvisAvatarParameters:
    if name != PRESET_ID:
        raise ValueError(f"Preset inconnu: {name}")
    if not PRESET_PATH.is_file():
        raise FileNotFoundError(f"Preset MPFB absent: {PRESET_PATH}")
    with PRESET_PATH.open("r", encoding="utf-8") as fh:
        return JarvisAvatarParameters.from_dict(json.load(fh))


def generate_from_preset(name: str = PRESET_ID) -> dict[str, Any]:
    """Return only deterministic generation instructions; Blender executes them."""
    params = load_preset(name)
    return {
        "build_id": BUILD_ID,
        "preset_id": name,
        "parameters": params.to_dict(),
        "mpfb_macro_details": params.to_mpfb_macro_details(),
        "fingerprint": params.fingerprint(),
        "requires_mpfb": True,
        "milestone": "static",
    }
