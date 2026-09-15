"""Modificateurs & optimisation de maillages."""
from __future__ import annotations

import bpy

from geometry import select


def _object_mode() -> None:
    try:
        bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass


def apply_modifier(obj: bpy.types.Object, modifier_name: str) -> None:
    select(obj)
    _object_mode()
    try:
        bpy.ops.object.modifier_apply(modifier=modifier_name)
    except Exception:
        try:
            mod = next((m for m in obj.modifiers if m.name == modifier_name), None)
            if mod is not None:
                obj.modifiers.remove(mod)
        except Exception:
            pass


def decimate(obj: bpy.types.Object, ratio: float = 0.5) -> None:
    select(obj)
    _object_mode()
    mod = obj.modifiers.new("JARVIS_Decimate", "DECIMATE")
    mod.ratio = max(0.01, min(1.0, float(ratio)))
    mod.use_collapse_triangulate = True
    apply_modifier(obj, mod.name)


def remove_doubles(obj: bpy.types.Object, threshold: float = 0.0001) -> None:
    select(obj)
    _object_mode()
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=float(threshold))
    _object_mode()


def triangulate(obj: bpy.types.Object) -> None:
    select(obj)
    _object_mode()
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    try:
        bpy.ops.mesh.quads_convert_to_tris(quad_method="BEAUTY", ngon_method="BEAUTY")
    except Exception:
        pass
    _object_mode()


def subsurf(obj: bpy.types.Object, levels: int = 2) -> None:
    select(obj)
    _object_mode()
    mod = obj.modifiers.new("JARVIS_Subsurf", "SUBSURF")
    mod.levels = max(1, min(4, int(levels)))
    mod.render_levels = mod.levels
    apply_modifier(obj, mod.name)