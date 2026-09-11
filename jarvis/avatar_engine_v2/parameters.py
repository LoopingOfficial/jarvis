"""Deterministic JARVIS appearance parameters and MPFB mapping."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _number(value: Any, default: float, low: float = 0.5, high: float = 1.8) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class BodyParameters:
    build: str = "slim"
    height: float = 0.98
    shoulder_width: float = 0.94
    leg_length: float = 1.05
    chest_width: float = 0.92
    waist_width: float = 0.88


@dataclass(frozen=True)
class HeadParameters:
    scale: float = 1.10
    face_width: float = 0.94
    jaw_width: float = 0.90
    cheek_volume: float = 1.08
    jaw_roundness: float = 0.82


@dataclass(frozen=True)
class EyeParameters:
    scale: float = 1.20
    spacing: float = 0.96
    color: str = "brown"


@dataclass(frozen=True)
class JarvisAvatarParameters:
    """Stable, JSON-compatible appearance contract.

    The LLM may propose values, but only this normalized object reaches MPFB.
    """

    body: BodyParameters = field(default_factory=BodyParameters)
    head: HeadParameters = field(default_factory=HeadParameters)
    eyes: EyeParameters = field(default_factory=EyeParameters)
    skin: str = "young_caucasian_male"
    hair: str = "short03"
    outfit: str = "male_casualsuit03"
    rig: str = "default"

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None = None) -> "JarvisAvatarParameters":
        raw = raw or {}
        body = raw.get("body") if isinstance(raw.get("body"), dict) else {}
        head = raw.get("head") if isinstance(raw.get("head"), dict) else {}
        eyes = raw.get("eyes") if isinstance(raw.get("eyes"), dict) else {}
        return cls(
            body=BodyParameters(
                build=str(body.get("build", "slim")),
                height=_number(body.get("height"), 0.98),
                shoulder_width=_number(body.get("shoulder_width"), 0.94),
                leg_length=_number(body.get("leg_length"), 1.05),
                chest_width=_number(body.get("chest_width"), 0.92),
                waist_width=_number(body.get("waist_width"), 0.88),
            ),
            head=HeadParameters(
                scale=_number(head.get("scale"), 1.10),
                face_width=_number(head.get("face_width"), 0.94),
                jaw_width=_number(head.get("jaw_width"), 0.90),
                cheek_volume=_number(head.get("cheek_volume"), 1.08),
                jaw_roundness=_number(head.get("jaw_roundness"), 0.82),
            ),
            eyes=EyeParameters(
                scale=_number(eyes.get("scale"), 1.20),
                spacing=_number(eyes.get("spacing"), 0.96),
                color=str(eyes.get("color", "brown")),
            ),
            skin=str(raw.get("skin", "young_caucasian_male")),
            hair=str(raw.get("hair", "short03")),
            outfit=str(raw.get("outfit", "male_casualsuit03")),
            rig=str(raw.get("rig", "default")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_mpfb_macro_details(self) -> dict[str, Any]:
        """Map JARVIS proportions to MPFB macro target coordinates [0, 1]."""
        b = self.body
        return {
            "gender": 0.15,  # young male
            "age": 0.18,
            "muscle": 0.34,
            "weight": max(0.0, min(1.0, 0.50 + (b.waist_width - 1.0) * 0.8)),
            "proportions": max(0.0, min(1.0, 0.50 + (b.shoulder_width - 1.0) * 0.8)),
            "height": max(0.0, min(1.0, 0.50 + (b.height - 1.0) * 1.4)),
            "cupsize": 0.5,
            "firmness": 0.62,
            "race": {"asian": 0.08, "caucasian": 0.84, "african": 0.08},
        }

    def fingerprint(self) -> str:
        import hashlib
        import json
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
