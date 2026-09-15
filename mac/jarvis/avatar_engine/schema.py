"""Contrats stables de l'AvatarEngine : morphs, opérations, presets, états."""
from __future__ import annotations

from typing import Any

BUILD_ID = "JARVIS_AVATAR_ENGINE_20260911_A"

BODY_MORPHS = (
    "head_scale", "shoulder_width", "chest_width", "waist_width",
    "arm_length", "forearm_length", "hand_scale",
    "leg_length", "thigh_width", "calf_width", "foot_scale",
)

FACE_MORPHS = (
    "face_width", "face_height", "jaw_width", "jaw_roundness", "chin_length",
    "cheek_volume", "forehead_height",
    "eye_size", "eye_spacing", "eye_height", "eye_depth",
    "brow_height", "brow_angle",
    "nose_length", "nose_width", "nose_tip",
    "mouth_width", "lip_upper", "lip_lower", "smile_base",
    "ear_scale",
)

EXPRESSION_KEYS = (
    "smile", "mouth_open", "mouth_close", "frown",
    "blink_L", "blink_R",
    "brow_up", "brow_down", "brow_inner_up",
)

VISEMES = ("REST", "A", "E", "I", "O", "U", "MBP", "FV", "L")

ANIMATIONS = (
    "idle", "breathing", "blink", "look_around", "head_nod", "head_tilt",
    "thinking", "talking", "typing", "wave", "point", "explain", "walk",
)

AVATAR_STATES = (
    "IDLE", "LISTENING", "THINKING", "USING_TOOL", "CODING",
    "SPEAKING", "SUCCESS", "WARNING", "ERROR", "WALKING",
)

OPERATIONS = (
    "load_base", "set_body_proportions", "set_face_morphs", "set_eyes",
    "set_hair", "set_outfit", "set_materials", "ensure_rig", "ensure_face_rig",
    "create_visemes", "create_animations", "validate", "export_glb",
    "modify_face", "modify_body", "inspect",
)

PRESETS = {
    "face": "jarvis_face_v1",
    "hair": "side_swept_voluminous",
    "outfit": "jarvis_casual_01",
    "animation": "jarvis_conversation_pack",
}

OUTFIT_JARVIS_CASUAL_01 = {
    "id": "jarvis_casual_01",
    "shirt": {"name": "JARVIS_Shirt", "color": "#f4f4ee", "material": "cotton_shirt"},
    "overshirt": {"name": "JARVIS_Jacket", "color": "#909eb0", "material": "grey_overshirt"},
    "trousers": {"name": "JARVIS_Pants", "color": "#4c5566", "material": "trousers"},
    "belt": {"name": "JARVIS_Belt", "color": "#3a2a1c", "material": "leather_belt"},
    "shoes": {"name": "JARVIS_Shoes", "color": "#f5f5f5", "material": "white_sneakers"},
}

HAIR_SIDE_SWEPT = {
    "preset": "side_swept_voluminous",
    "volumes": {"front": 1.12, "top": 1.18, "side_left": 1.08, "side_right": 0.92, "back": 1.06},
    "color": "#5a301c",
}

REQUIRED_PARTS = (
    "JARVIS_Head", "JARVIS_Skin", "JARVIS_Hands", "JARVIS_Hair",
    "JARVIS_Eyes", "JARVIS_Iris", "JARVIS_Pupils", "JARVIS_FaceParts",
    "JARVIS_Mouth", "JARVIS_Brows", "JARVIS_Shirt", "JARVIS_Jacket",
    "JARVIS_Pants", "JARVIS_Belt", "JARVIS_Shoes",
)

DEFAULT_PARAMS: dict[str, float] = {
    "head_scale": 1.07,
    "shoulder_width": 0.94,
    "chest_width": 0.92,
    "waist_width": 0.88,
    "arm_length": 1.0,
    "forearm_length": 1.0,
    "hand_scale": 1.0,
    "leg_length": 1.05,
    "thigh_width": 0.92,
    "calf_width": 0.90,
    "foot_scale": 1.0,
    "face_width": 0.94,
    "face_height": 1.04,
    "jaw_width": 0.90,
    "jaw_roundness": 0.82,
    "chin_length": 1.02,
    "cheek_volume": 0.78,
    "forehead_height": 1.04,
    "eye_size": 1.20,
    "eye_spacing": 0.95,
    "eye_height": 1.0,
    "eye_depth": 1.0,
    "brow_height": 1.0,
    "brow_angle": 0.0,
    "nose_length": 0.96,
    "nose_width": 0.90,
    "nose_tip": 1.0,
    "mouth_width": 0.96,
    "lip_upper": 1.0,
    "lip_lower": 1.05,
    "smile_base": 0.18,
    "ear_scale": 1.0,
}


def clamp_param(name: str, value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DEFAULT_PARAMS.get(name, 1.0)
    return max(0.5, min(1.8, number))


def merge_params(overrides: dict[str, Any] | None = None) -> dict[str, float]:
    out = dict(DEFAULT_PARAMS)
    for key, value in (overrides or {}).items():
        if key in out:
            out[key] = clamp_param(key, value)
    return out
