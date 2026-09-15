"""Vision → contraintes numériques exploitables par AvatarEngine."""
from __future__ import annotations

from typing import Any

from .schema import DEFAULT_PARAMS, HAIR_SIDE_SWEPT, clamp_param, merge_params

_SIZE = {"small": 0.88, "average": 1.0, "medium": 1.0, "large": 1.20, "wide": 1.12, "narrow": 0.90}
_BUILD = {
    "slim": {"shoulder_width": 0.94, "chest_width": 0.92, "waist_width": 0.86, "thigh_width": 0.90},
    "athletic": {"shoulder_width": 1.06, "chest_width": 1.04, "waist_width": 0.92, "thigh_width": 1.02},
    "average": {"shoulder_width": 1.0, "chest_width": 1.0, "waist_width": 0.96, "thigh_width": 1.0},
    "heavy": {"shoulder_width": 1.12, "chest_width": 1.10, "waist_width": 1.08, "thigh_width": 1.10},
}
_VOLUME = {"flat": 0.86, "medium": 1.0, "voluminous": 1.18, "high": 1.18}
_FACE = {"round": 1.08, "oval": 0.96, "square": 1.04, "long": 0.90}


def _get(source: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = source
    for part in path.split("."):
        if not isinstance(current, dict):
            return default
        current = current.get(part)
        if current is None:
            return default
    return current


def _num(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _size(value: Any, fallback: float = 1.0) -> float:
    if isinstance(value, (int, float)):
        return clamp_param("eye_size", value)
    return _SIZE.get(str(value or "").lower(), fallback)


def features_to_params(features: dict[str, Any] | None) -> dict[str, Any]:
    """Produit le JSON de contraintes (nombres), pas des adjectifs seuls."""
    src = features or {}
    params = merge_params()

    head_ratio = _get(src, "head.head_to_body_ratio")
    if isinstance(head_ratio, (int, float)) and 0.10 <= float(head_ratio) <= 0.30:
        params["head_scale"] = clamp_param("head_scale", float(head_ratio) / 0.145)
    else:
        params["head_scale"] = clamp_param("head_scale", _get(src, "head_scale", params["head_scale"]))

    face_shape = str(_get(src, "head.face_shape", "") or "").lower()
    params["face_width"] = clamp_param("face_width", _FACE.get(face_shape, params["face_width"]))
    params["jaw_roundness"] = clamp_param(
        "jaw_roundness", _get(src, "head.jaw_roundness", 0.82 if "round" in face_shape else params["jaw_roundness"]))
    cheek = str(_get(src, "head.cheek_volume", "") or "").lower()
    params["cheek_volume"] = {"low": 0.70, "medium": 0.90, "high": 1.12}.get(cheek, params["cheek_volume"])

    params["eye_size"] = _size(_get(src, "eyes.size"), params["eye_size"])
    spacing = str(_get(src, "eyes.spacing", "") or "").lower()
    params["eye_spacing"] = {"close": 0.90, "narrow": 0.90, "wide": 1.08, "average": 0.95}.get(
        spacing, params["eye_spacing"])

    build = str(_get(src, "body.build", "slim") or "slim").lower()
    params.update({k: clamp_param(k, v) for k, v in _BUILD.get(build, _BUILD["slim"]).items()})
    leg = str(_get(src, "body.leg_length", "") or "").lower()
    params["leg_length"] = {"short": 0.94, "average": 1.0, "long": 1.05}.get(leg, 1.05)

    hair_color = str(_get(src, "hair.color", HAIR_SIDE_SWEPT["color"]) or HAIR_SIDE_SWEPT["color"])
    volume = _VOLUME.get(str(_get(src, "hair.volume", "voluminous") or "").lower(), 1.18)
    hair = {
        "preset": "side_swept_voluminous",
        "volume": volume,
        "color": hair_color if hair_color.startswith("#") else "#5a301c",
        "volumes": dict(HAIR_SIDE_SWEPT["volumes"]),
    }
    hair["volumes"]["top"] = volume

    body = {
        "build": build if build in _BUILD else "slim",
        "leg_ratio": params["leg_length"],
    }

    # Les valeurs numériques renvoyées par Vision sont les contraintes
    # prioritaires : elles ne doivent pas être perdues au profit des classes
    # fermées (slim/large/etc.).
    morphs = _get(src, "morphs", {})
    if isinstance(morphs, dict):
        for name, value in morphs.items():
            if name in params:
                params[name] = clamp_param(name, value)

    outfit_src = _get(src, "outfit", {})
    outfit = {
        "shirt": {"name": "JARVIS_Shirt", "color": str(_get(outfit_src, "shirt.color", "#f4f4ee"))},
        "overshirt": {"name": "JARVIS_Jacket", "color": str(_get(outfit_src, "overshirt.color", "#909eb0"))},
        "trousers": {"name": "JARVIS_Pants", "color": str(_get(outfit_src, "pants.color", "#4c5566"))},
        "belt": {"name": "JARVIS_Belt", "color": str(_get(outfit_src, "belt.color", "#3a2a1c"))},
        "shoes": {"name": "JARVIS_Shoes", "color": str(_get(outfit_src, "shoes.color", "#f5f5f5"))},
        "preset": "jarvis_casual_01",
    }

    return {
        **params,
        "hair": hair,
        "body": body,
        "eyes": {
            "size": params["eye_size"],
            "spacing": params["eye_spacing"],
            "color": str(_get(src, "eyes.color", "#5c3a22") or "#5c3a22"),
        },
        "outfit": outfit,
        "skin": {
            "color": str(_get(src, "skin.tone", "#e0b498") or "#e0b498"),
            "roughness": _num(_get(src, "skin.roughness", 0.58), 0.58),
        },
    }


def issues_to_params(issues: list[str], current: dict[str, Any] | None = None) -> dict[str, Any]:
    """Transforme les retours Vision (« eyes too small ») en deltas de paramètres."""
    params = merge_params(current)
    for raw in issues or []:
        text = str(raw).lower()
        if "eye" in text and "small" in text:
            params["eye_size"] = clamp_param("eye_size", params["eye_size"] * 1.12)
        elif "eye" in text and "large" in text:
            params["eye_size"] = clamp_param("eye_size", params["eye_size"] * 0.92)
        elif "hair" in text and ("flat" in text or "casque" in text or "helmet" in text):
            params["hair_volume"] = 1.18
        elif "jaw" in text and "wide" in text:
            params["jaw_width"] = clamp_param("jaw_width", params["jaw_width"] * 0.92)
        elif "jaw" in text and "narrow" in text:
            params["jaw_width"] = clamp_param("jaw_width", params["jaw_width"] * 1.08)
        elif "head" in text and "small" in text:
            params["head_scale"] = clamp_param("head_scale", params["head_scale"] * 1.06)
    return params
