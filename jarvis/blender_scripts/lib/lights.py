"""Lumières : éclairage studio trois points réutilisable."""
from __future__ import annotations

import bpy

from common import hex_to_rgb


def add_light(light_type: str = "AREA", energy: float = 200.0,
              location=(3.0, -3.0, 3.0), color="FFFFFF",
              name: str = "Lamp_JARVIS") -> bpy.types.Object:
    bpy.ops.object.light_add(type=light_type, location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.data.energy = float(energy)
    try:
        obj.data.color = tuple(hex_to_rgb(color))
    except Exception:
        pass
    if light_type == "AREA":
        try:
            obj.data.size = 2.0
        except Exception:
            pass
    return obj


def default_lights() -> list[bpy.types.Object]:
    """Clé + remplissage + contre-jour : un rendu ne dépend pas de la scène."""
    out = []
    out.append(add_light("AREA", 320.0, (3.2, -4.0, 3.6), "FFFFFF", "Key_JARVIS"))
    out.append(add_light("AREA", 90.0, (-4.2, 2.2, 1.8), "C5CCFF", "Fill_JARVIS"))
    out.append(add_light("POINT", 80.0, (0.0, 3.2, -1.5), "2F3A4A", "Rim_JARVIS"))
    return out