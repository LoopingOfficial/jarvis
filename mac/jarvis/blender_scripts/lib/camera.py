"""Caméras : cadrages prédéfinis regardant la scène."""
from __future__ import annotations

import bpy
from mathutils import Vector

PRESETS = {
    "front": {"loc": (0.0, -5.2, 0.9), "target": (0.0, 0.0, 0.5), "lens": 50.0},
    "three_quarter": {"loc": (3.0, -3.8, 1.6), "target": (0.0, 0.0, 0.4), "lens": 50.0},
    "side": {"loc": (4.6, 0.0, 0.6), "target": (0.0, 0.0, 0.4), "lens": 50.0},
    "top": {"loc": (0.0, 0.01, 5.4), "target": (0.0, 0.0, 0.0), "lens": 35.0},
    "closeup": {"loc": (1.4, -1.9, 0.7), "target": (0.0, 0.0, 0.2), "lens": 85.0},
}


def add_camera(angle: str = "three_quarter", name: str = "Cam_JARVIS",
               location=None, target=None) -> bpy.types.Object:
    preset = PRESETS.get(str(angle or "three_quarter").lower(), PRESETS["three_quarter"])
    if location is None:
        location = preset["loc"]
    if target is None:
        target = preset["target"]
    bpy.ops.object.camera_add(location=location)
    cam = bpy.context.active_object
    cam.name = name
    direction = Vector(target) - Vector(location)
    if direction.length > 1e-6:
        cam.rotation_euler = direction.normalized().to_track_quat("-Z", "Y").to_euler()
    cam.data.lens = float(preset["lens"])
    bpy.context.scene.camera = cam
    return cam