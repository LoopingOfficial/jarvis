"""Ouverture / import / sauvegarde de scenes, et inventaire reel."""
from __future__ import annotations

import math
import os

import bpy

IMPORT_FORMATS = {".glb": "gltf", ".gltf": "gltf", ".fbx": "fbx", ".obj": "obj",
                  ".stl": "stl", ".dae": "dae", ".ply": "ply", ".blend": "blend"}


def open_blend(path: str) -> bool:
    """Reprend un projet existant. False si le fichier n'existe pas."""
    if not path or not os.path.isfile(path):
        return False
    bpy.ops.wm.open_mainfile(filepath=str(path))
    return True


def new_scene(clear: bool = True) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=bool(clear))
    bpy.context.scene.unit_settings.system = "METRIC"


def clear_objects() -> int:
    count = len(bpy.data.objects)
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    return count


def import_file(path: str) -> dict:
    """Importe un vrai fichier 3D. Retourne les objets ajoutes."""
    if not path or not os.path.isfile(path):
        return {"ok": False, "error": "Fichier introuvable : %s" % path}
    ext = os.path.splitext(path)[1].lower()
    kind = IMPORT_FORMATS.get(ext)
    if not kind:
        return {"ok": False, "error": "Format d'import non supporte : %s" % ext}
    before = {o.name for o in bpy.data.objects}
    try:
        if kind == "gltf":
            bpy.ops.import_scene.gltf(filepath=path)
        elif kind == "fbx":
            bpy.ops.import_scene.fbx(filepath=path)
        elif kind == "obj":
            if hasattr(bpy.ops.wm, "obj_import"):
                bpy.ops.wm.obj_import(filepath=path)
            else:
                bpy.ops.import_scene.obj(filepath=path)
        elif kind == "stl":
            if hasattr(bpy.ops.wm, "stl_import"):
                bpy.ops.wm.stl_import(filepath=path)
            else:
                bpy.ops.import_mesh.stl(filepath=path)
        elif kind == "dae":
            bpy.ops.wm.collada_import(filepath=path)
        elif kind == "ply":
            if hasattr(bpy.ops.wm, "ply_import"):
                bpy.ops.wm.ply_import(filepath=path)
            else:
                bpy.ops.import_mesh.ply(filepath=path)
        elif kind == "blend":
            with bpy.data.libraries.load(path) as (src, dst):
                dst.objects = list(src.objects)
            for obj in dst.objects:
                if obj is not None:
                    bpy.context.collection.objects.link(obj)
    except Exception as exc:
        return {"ok": False, "error": "Import %s echoue : %s" % (kind, str(exc)[:300])}
    added = [o.name for o in bpy.data.objects if o.name not in before]
    return {"ok": bool(added), "format": kind, "objects": added,
            "error": "" if added else "Aucun objet importe."}


def inventory() -> dict:
    """Inventaire factuel de la scene (rien n'est estime)."""
    from animation import animation_summary
    from camera import framing_info
    from lighting import light_summary
    from materials import material_summary
    from optimize import triangle_count
    from rigging import rig_summary
    from textures import texture_summary

    objects = []
    for obj in bpy.data.objects:
        entry = {"name": obj.name, "type": obj.type,
                 "location": [round(float(v), 3) for v in obj.location],
                 "dimensions": [round(float(v), 3) for v in obj.dimensions]}
        if obj.type == "MESH":
            mesh = obj.data
            try:
                mesh.calc_loop_triangles()
                tris = len(mesh.loop_triangles)
            except Exception:
                tris = 0
            entry.update({"vertices": len(mesh.vertices), "faces": len(mesh.polygons),
                          "triangles": tris,
                          "materials": [m.name for m in mesh.materials if m],
                          "uv_layers": [uv.name for uv in mesh.uv_layers],
                          "shape_keys": ([k.name for k in mesh.shape_keys.key_blocks]
                                         if mesh.shape_keys else []),
                          "modifiers": [m.type for m in obj.modifiers]})
        objects.append(entry)
    return {
        "objects": objects,
        "counts": {"objects": len(bpy.data.objects),
                   "meshes": len([o for o in bpy.data.objects if o.type == "MESH"]),
                   "triangles": triangle_count(),
                   "materials": len(bpy.data.materials),
                   "images": len([i for i in bpy.data.images
                                  if i.name not in {"Render Result", "Viewer Node"}]),
                   "actions": len(bpy.data.actions)},
        "materials": material_summary(),
        "textures": texture_summary(),
        "lights": light_summary(),
        "rig": rig_summary(),
        "animations": animation_summary(),
        "framing": framing_info(),
    }


def main_object():
    """Objet principal : le mesh le plus lourd, sinon le premier objet."""
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    if not meshes:
        return next((o for o in bpy.data.objects if o.type in {"CURVE", "FONT"}), None)
    return max(meshes, key=lambda o: len(o.data.polygons))


def find_object(name: str):
    if not name:
        return main_object()
    obj = bpy.data.objects.get(name)
    if obj is not None:
        return obj
    low = str(name).lower()
    for candidate in bpy.data.objects:
        if low in candidate.name.lower():
            return candidate
    return main_object()


def target_objects(names=None) -> list:
    """Objets vises par une operation : liste explicite, sinon tous les meshes."""
    if names:
        found = [find_object(n) for n in names]
        return [o for o in found if o is not None]
    return [o for o in bpy.data.objects if o.type in {"MESH", "CURVE", "FONT"}]


def ground_and_center(objects=None) -> None:
    """Repose le modele sur Z=0 et le centre en XY (attendu d'un viewer web)."""
    from camera import scene_bounds
    from mathutils import Vector

    objs = objects or [o for o in bpy.data.objects
                       if o.type in {"MESH", "CURVE", "FONT", "ARMATURE"}]
    if not objs:
        return
    center, dims, _ = scene_bounds()
    lowest = center.z - dims.z / 2.0
    shift = Vector((-center.x, -center.y, -lowest))
    for obj in objs:
        if obj.parent is None:
            obj.location += shift
