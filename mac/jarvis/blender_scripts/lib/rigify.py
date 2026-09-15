"""Rigging : squelette humain complet JARVIS + liaison à un maillage.

Le squelette vient de assets/blender/rig.py (convention Z-up / -Y forward,
compatible glTF → Three.js).
"""
from __future__ import annotations

import importlib.util
import os

import bpy
from mathutils import Vector

from geometry import select


def _load_rig_module(spec_path: str):
    path = spec_path or os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "assets", "blender", "rig.py")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Module rig introuvable : {path}")
    spec = importlib.util.spec_from_file_location("jarvis_rig_asset", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _edit_bone_names(armature) -> set:
    return {eb.name for eb in armature.data.edit_bones}


def build_human_rig(spec_path: str, scale: float = 1.0,
                    name: str = "AR_JARVIS") -> bpy.types.Object:
    rig = _load_rig_module(spec_path)
    bpy.ops.object.armature_add(location=(0.0, 0.0, 0.0))
    arm = bpy.context.active_object
    arm.name = name
    bpy.ops.object.mode_set(mode="EDIT")
    for eb in list(arm.data.edit_bones):
        arm.data.edit_bones.remove(eb)
    for bone_name, head, tail, parent, roll in rig.BONES:
        eb = arm.data.edit_bones.new(bone_name)
        eb.head = Vector(head) * float(scale)
        eb.tail = Vector(tail) * float(scale)
        eb.roll = float(roll)
        if parent and parent in arm.data.edit_bones:
            eb.parent = arm.data.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    arm.data.show_names = False
    return arm


def bind_mesh(mesh: bpy.types.Object, armature: bpy.types.Object) -> str:
    """Liaison réelle : poids automatiques, sinon liaison par nom."""
    bpy.ops.object.select_all(action="DESELECT")
    mesh.select_set(True)
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    try:
        bpy.ops.object.parent_set(type="ARMATURE_AUTO")
        return "auto_weights"
    except Exception:
        try:
            bpy.ops.object.parent_set(type="ARMATURE_NAME")
            return "name"
        except Exception as exc:
            return f"error:{exc}"