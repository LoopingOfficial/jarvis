"""Animation : rotation, rebond, orbite, pulsation, keyframes propres."""
from __future__ import annotations

import math

import bpy

from geometry import select


def setup_frames(start: int = 1, end: int = 120, fps: int = 24) -> None:
    scene = bpy.context.scene
    scene.frame_start = max(1, int(start))
    scene.frame_end = max(int(start) + 2, int(end or 120))
    try:
        scene.render.fps = max(1, int(fps))
    except Exception:
        pass


_AXIS = {"X": 0, "Y": 1, "Z": 2}


def _insert(obj: bpy.types.Object, path: str, frame: int) -> None:
    obj.keyframe_insert(data_path=path, frame=frame)


def animate(obj: bpy.types.Object, kind: str = "rotate", axis: str = "Z",
            degrees: float = 360.0, frames: tuple = (1, 120),
            amplitude: float = 0.4, loops: int = 1) -> str:
    kind = str(kind or "rotate").lower()
    start, end = int(frames[0]) or 1, int(frames[1]) or 120
    if end <= start:
        end = start + 60
    setup_frames(start, end)
    select(obj)
    bpy.context.scene.frame_set(start)

    if kind == "rotate":
        axis_i = _AXIS.get(axis.upper(), "Z")
        base = list(obj.rotation_euler)
        bpy.context.scene.frame_set(start)
        obj.rotation_euler = base[:]
        _insert(obj, "rotation_euler", start)
        bpy.context.scene.frame_set(end)
        target = base[:]
        target[axis_i] = (base[axis_i] or 0.0) + math.radians(float(degrees) * max(1, int(loops)))
        obj.rotation_euler = target
        _insert(obj, "rotation_euler", end)
        return "rotate"

    if kind == "bounce":
        bpy.context.scene.frame_set(start)
        obj.location.z = float(amplitude)
        _insert(obj, "location", start)
        bpy.context.scene.frame_set(end)
        obj.location.z = 0.0
        _insert(obj, "location", end)
        return "bounce"

    if kind == "pulse":
        bpy.context.scene.frame_set(start)
        obj.scale = (1.0, 1.0, 1.0)
        _insert(obj, "scale", start)
        mid = (start + end) // 2
        bpy.context.scene.frame_set(mid)
        s = 1.0 + float(amplitude)
        obj.scale = (s, s, s)
        _insert(obj, "scale", mid)
        bpy.context.scene.frame_set(end)
        obj.scale = (1.0, 1.0, 1.0)
        _insert(obj, "scale", end)
        return "pulse"

    if kind == "orbit":
        bpy.ops.object.empty_add(type="PLAIN_AXES", location=(0.0, 0.0, 0.0))
        pivot = bpy.context.active_object
        pivot.name = "Pivot_JARVIS"
        select(pivot)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = pivot
        bpy.ops.object.parent_set(type="OBJECT", keep_transform=False)
        bpy.context.scene.frame_set(start)
        pivot.rotation_euler.z = 0.0
        _insert(pivot, "rotation_euler", start)
        bpy.context.scene.frame_set(end)
        pivot.rotation_euler.z = math.radians(float(degrees))
        _insert(pivot, "rotation_euler", end)
        return "orbit"

    return "rotate"