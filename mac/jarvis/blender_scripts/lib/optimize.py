"""Optimisation temps reel : decimate, merge, normales, transforms, LOD.

Profils :
  web           -> budget navigateur generaliste (Three.js)
  jarvis_avatar -> avatar anime : preserve l'armature et les shape keys
  none          -> nettoyage minimal (transforms + doublons)
"""
from __future__ import annotations

import math
import os

import bpy

PROFILES = {
    "web": {"max_triangles": 60000, "merge_distance": 0.0002, "texture_max": 1024,
            "remove_hidden": True, "apply_transforms": True, "fix_normals": True,
            "remove_unused_materials": True, "join_meshes": True},
    "jarvis_avatar": {"max_triangles": 45000, "merge_distance": 0.0001, "texture_max": 2048,
                      "remove_hidden": True, "apply_transforms": False, "fix_normals": True,
                      "remove_unused_materials": True, "join_meshes": False},
    "none": {"max_triangles": 0, "merge_distance": 0.0, "texture_max": 0,
             "remove_hidden": False, "apply_transforms": True, "fix_normals": False,
             "remove_unused_materials": True, "join_meshes": False},
}


def profile(name: str) -> dict:
    return dict(PROFILES.get(str(name or "web").lower(), PROFILES["web"]))


def triangle_count(objects=None) -> int:
    total = 0
    for obj in (objects or [o for o in bpy.data.objects if o.type == "MESH"]):
        try:
            obj.data.calc_loop_triangles()
            total += len(obj.data.loop_triangles)
        except Exception:
            total += len(obj.data.polygons) * 2
    return total


def _meshes():
    return [o for o in bpy.data.objects if o.type == "MESH"]


def has_armature_deform(obj) -> bool:
    return any(m.type == "ARMATURE" for m in obj.modifiers)


def apply_transforms(skip_rigged: bool = True) -> int:
    done = 0
    for obj in _meshes():
        if skip_rigged and has_armature_deform(obj):
            continue
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        try:
            bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
            done += 1
        except Exception:
            pass
    return done


def merge_vertices(distance: float = 0.0002) -> int:
    """Fusionne les doublons. Retourne le nombre de sommets supprimes."""
    if distance <= 0:
        return 0
    removed = 0
    for obj in _meshes():
        before = len(obj.data.vertices)
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        try:
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.mesh.remove_doubles(threshold=float(distance))
            bpy.ops.object.mode_set(mode="OBJECT")
            removed += before - len(obj.data.vertices)
        except Exception:
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception:
                pass
    return max(0, removed)


def fix_normals() -> int:
    done = 0
    for obj in _meshes():
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        try:
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.mesh.normals_make_consistent(inside=False)
            bpy.ops.object.mode_set(mode="OBJECT")
            done += 1
        except Exception:
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception:
                pass
    return done


def remove_hidden() -> int:
    removed = 0
    for obj in list(bpy.data.objects):
        if obj.type == "MESH" and (obj.hide_render or obj.hide_viewport):
            bpy.data.objects.remove(obj, do_unlink=True)
            removed += 1
    return removed


def remove_unused_materials() -> int:
    from materials import remove_unused

    for obj in _meshes():
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        try:
            bpy.ops.object.material_slot_remove_unused()
        except Exception:
            pass
    return remove_unused()


def decimate_to(target_triangles: int) -> dict:
    """Reduit le maillage jusqu'au budget demande, proportionnellement."""
    from modifiers import decimate

    current = triangle_count()
    if target_triangles <= 0 or current <= target_triangles:
        return {"applied": False, "before": current, "after": current, "ratio": 1.0}
    ratio = max(0.02, float(target_triangles) / float(current))
    for obj in _meshes():
        if obj.data.shape_keys:
            continue        # decimate est incompatible avec les morph targets
        try:
            decimate(obj, ratio, apply=True)
        except Exception:
            pass
    after = triangle_count()
    return {"applied": True, "before": current, "after": after, "ratio": round(ratio, 4)}


def resize_textures(max_size: int = 1024) -> list:
    """Redimensionne reellement les images trop grandes (pack en memoire)."""
    changed = []
    if max_size <= 0:
        return changed
    for img in bpy.data.images:
        if img.name in {"Render Result", "Viewer Node"} or not img.has_data:
            continue
        w, h = img.size
        if max(w, h) <= max_size:
            continue
        factor = float(max_size) / float(max(w, h))
        try:
            img.scale(max(1, int(w * factor)), max(1, int(h * factor)))
            img.pack()
            changed.append({"name": img.name, "from": [w, h], "to": list(img.size)})
        except Exception:
            pass
    return changed


def join_meshes(name: str = "Model") -> str:
    """Reduit les draw calls en fusionnant les meshes non riggees."""
    targets = [o for o in _meshes() if not has_armature_deform(o) and not o.data.shape_keys]
    if len(targets) < 2:
        return targets[0].name if targets else ""
    bpy.ops.object.select_all(action="DESELECT")
    for obj in targets:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = targets[0]
    try:
        bpy.ops.object.join()
        merged = bpy.context.active_object
        merged.name = name
        return merged.name
    except Exception:
        return targets[0].name


def make_lod(ratios=(0.5, 0.25)) -> list:
    """Genere de vrais niveaux de detail dupliques (LOD1, LOD2...)."""
    from modifiers import decimate

    created = []
    sources = [o for o in _meshes() if not o.name.startswith("LOD")]
    for level, ratio in enumerate(ratios, start=1):
        for src in sources:
            copy = src.copy()
            copy.data = src.data.copy()
            copy.name = "LOD%d_%s" % (level, src.name)
            bpy.context.collection.objects.link(copy)
            try:
                decimate(copy, float(ratio), apply=True)
                copy.hide_render = True
                created.append({"name": copy.name, "ratio": float(ratio),
                                "triangles": triangle_count([copy])})
            except Exception:
                bpy.data.objects.remove(copy, do_unlink=True)
    return created


def run(target: str = "web", overrides=None, lod: bool = False) -> dict:
    """Applique le profil complet. Chaque etape rapporte ce qu'elle a fait."""
    cfg = profile(target)
    cfg.update({k: v for k, v in (overrides or {}).items() if v is not None})
    report = {"profile": target, "before": {"triangles": triangle_count(),
                                            "objects": len(_meshes()),
                                            "materials": len(bpy.data.materials)}}
    if cfg.get("remove_hidden"):
        report["hidden_removed"] = remove_hidden()
    if cfg.get("apply_transforms"):
        report["transforms_applied"] = apply_transforms()
    if cfg.get("merge_distance"):
        report["vertices_merged"] = merge_vertices(cfg["merge_distance"])
    if cfg.get("fix_normals"):
        report["normals_fixed"] = fix_normals()
    if cfg.get("join_meshes"):
        report["joined_into"] = join_meshes()
    if cfg.get("max_triangles"):
        report["decimate"] = decimate_to(int(cfg["max_triangles"]))
    if cfg.get("texture_max"):
        report["textures_resized"] = resize_textures(int(cfg["texture_max"]))
    if cfg.get("remove_unused_materials"):
        report["materials_removed"] = remove_unused_materials()
    if lod:
        report["lod"] = make_lod()
    report["after"] = {"triangles": triangle_count(), "objects": len(_meshes()),
                       "materials": len(bpy.data.materials)}
    report["draw_calls_estimate"] = sum(max(1, len(o.data.materials)) for o in _meshes())
    return report
