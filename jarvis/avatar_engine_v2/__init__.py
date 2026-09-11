"""AvatarEngine V2: MPFB is the only anatomical source of truth.

The host side is deliberately Blender-agnostic. It validates and normalizes
the deterministic preset, then serializes a job for the Blender-side adapter.
"""
from .parameters import JarvisAvatarParameters
from .presets import BUILD_ID, PRESET_ID, generate_from_preset, load_preset
from .mpfb_adapter import MpfbAdapter, MpfbUnavailable

__all__ = [
    "BUILD_ID", "PRESET_ID", "JarvisAvatarParameters", "MpfbAdapter",
    "MpfbUnavailable", "generate_from_preset", "load_preset",
]
