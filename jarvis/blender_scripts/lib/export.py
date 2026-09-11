"""Export / import : GLB, GLTF, OBJ, STL, FBX, sauvegarde .blend."""
from __future__ import annotations

import os

import bpy


def save_blend(path: str) -> str:
    bpy.ops.wm.save_as_mainfile(filepath=path)
    return path


def open_blend(path: str) -> None:
    bpy.ops.wm.open_mainfile(filepath=path)


def export_glb(path: str) -> str:
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB")
    return path


def _ensure_addon(module: str) -> bool:
    try:
        addons = {a.module for a in bpy.context.preferences.addons}
        if module not in addons:
            bpy.ops.preferences.addon_enable(module=module)
        return True
    except Exception:
        return False


def export_obj(path: str) -> str:
    if hasattr(bpy.ops.wm, "obj_export"):
        bpy.ops.wm.obj_export(filepath=path)
    else:
        _ensure_addon("io_scene_obj")
        if not hasattr(bpy.ops.export_scene, "obj"):
            raise RuntimeError("Exporteur OBJ indisponible (io_scene_obj manquant).")
        bpy.ops.export_scene.obj(filepath=path)
    return path


def export_stl(path: str) -> str:
    if hasattr(bpy.ops.wm, "stl_export"):
        bpy.ops.wm.stl_export(filepath=path)
        return path
    if not hasattr(bpy.ops.export_mesh, "stl"):
        _ensure_addon("io_mesh_stl")
    if hasattr(bpy.ops.export_mesh, "stl"):
        bpy.ops.export_mesh.stl(filepath=path, use_selection=False)
    elif hasattr(bpy.ops.wm, "stl_export"):
        bpy.ops.wm.stl_export(filepath=path)
    else:
        raise RuntimeError("Exporteur STL indisponible (module io_mesh_stl manquant).")
    return path


def export_fbx(path: str) -> str:
    if not hasattr(bpy.ops.export_scene, "fbx"):
        _ensure_addon("io_scene_fbx")
    if not hasattr(bpy.ops.export_scene, "fbx"):
        raise RuntimeError("Exporteur FBX indisponible (io_scene_fbx manquant).")
    bpy.ops.export_scene.fbx(filepath=path, add_leaf_bones=False)
    return path


def export_scene(path: str, fmt: str) -> str:
    fmt = str(fmt or "glb").lower().lstrip(".")
    if fmt in {"glb", "gltf"}:
        if fmt == "gltf":
            bpy.ops.export_scene.gltf(filepath=path, export_format="GLTF_SEPARATE")
        else:
            export_glb(path)
    elif fmt == "obj":
        export_obj(path)
    elif fmt == "stl":
        export_stl(path)
    elif fmt == "fbx":
        export_fbx(path)
    else:
        raise ValueError(f"Format d'export non supporté : {fmt}")
    return path


def import_file(path: str) -> list[bpy.types.Object]:
    ext = os.path.splitext(path)[1].lower()
    before = set(bpy.data.objects)
    if ext == ".obj":
        if hasattr(bpy.ops.wm, "obj_import"):
            bpy.ops.wm.obj_import(filepath=path)
        else:
            bpy.ops.import_scene.obj(filepath=path)
    elif ext in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".stl":
        bpy.ops.import_mesh.stl(filepath=path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    else:
        raise ValueError(f"Format d'import non supporté : {ext}")
    return [o for o in bpy.data.objects if o not in before]