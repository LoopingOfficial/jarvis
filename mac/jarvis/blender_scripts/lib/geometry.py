"""Géométrie : primitives, texte, transformations, statistiques."""
from __future__ import annotations

import bpy
from mathutils import Vector

_PRIM_OPS = {
    "cube": "primitive_cube_add",
    "box": "primitive_cube_add",
    "sphere": "primitive_uv_sphere_add",
    "ball": "primitive_uv_sphere_add",
    "cylinder": "primitive_cylinder_add",
    "cap": "primitive_cylinder_add",
    "tube": "primitive_cylinder_add",
    "cone": "primitive_cone_add",
    "icosphere": "primitive_ico_sphere_add",
    "torus": "primitive_torus_add",
    "plane": "primitive_plane_add",
    "monkey": "primitive_monkey_add",
    "circle": "primitive_circle_add",
    "grid": "primitive_grid_add",
}

_EXTRA_KWDS = {
    "cube": {"size": 2.0},
    "sphere": {"radius": 1.0, "segments": 48, "ring_count": 24},
    "cylinder": {"radius": 1.0, "depth": 2.0, "vertices": 48},
    "cone": {"radius1": 1.0, "depth": 2.0, "vertices": 48},
    "icosphere": {"radius": 1.0, "subdivisions": 2},
    "torus": {"major_radius": 1.0, "minor_radius": 0.35, "major_segments": 48,
              "minor_segments": 24},
    "circle": {"radius": 1.0, "vertices": 64},
    "grid": {"x_subdivisions": 10, "y_subdivisions": 10, "size": 2.0},
    "plane": {"size": 2.0},
    "monkey": {"size": 1.2},
}


def create_primitive(code: str, scale=(1.0, 1.0, 1.0) or None, size=1.0,
                     location=(0.0, 0.0, 0.0), name: str = "") -> bpy.types.Object:
    """Crée une primitive réelle, dimensionnée et nommée."""
    key = str(code or "cube").lower()
    op_name = _PRIM_OPS.get(key, "primitive_cube_add")
    kw = {"location": location}
    kw.update(_EXTRA_KWDS.get(key, {"size": 2.0}))
    if key == "cube":
        kw["size"] = float(size or 1.0) * 2.0
    elif "radius" in kw:
        kw["radius"] = float(kw.get("radius", 1.0)) * float(size or 1.0)
    getattr(bpy.ops.mesh, op_name)(**kw)
    obj = bpy.context.active_object
    obj.name = (name or f"{key.title()}_JARVIS")
    if scale:
        try:
            obj.scale = Vector([float(s) for s in scale])
        except Exception:
            pass
    return obj


def add_text(text: str, size: float = 0.3, extrude: float = 0.03,
             location=(0.0, 0.0, 0.0), name: str = "Texte_JARVIS") -> bpy.types.Object:
    bpy.ops.object.text_add(location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.data.body = str(text or "")
    obj.data.size = float(size)
    obj.data.extrude = float(extrude)
    obj.data.align_x = "CENTER"
    try:
        obj.data.align_y = "CENTER"
    except Exception:
        pass
    return obj


def clear_scene() -> None:
    """Supprime tous les objets du view layer."""
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def select(obj: bpy.types.Object) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def active_or_first() -> bpy.types.Object | None:
    active = bpy.context.active_object
    if active and active.type == "MESH":
        return active
    for obj in bpy.data.objects:
        if obj.type == "MESH":
            return obj
    return None


def all_meshes() -> list[bpy.types.Object]:
    return [o for o in bpy.data.objects if o.type == "MESH"]


def join_objects(names: list[str]) -> bpy.types.Object | None:
    objs = [bpy.data.objects[n] for n in names if n in bpy.data.objects]
    objs = [o for o in objs if o.type == "MESH"]
    if len(objs) < 2:
        return objs[0] if objs else None
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.join()
    return bpy.context.active_object


def mesh_stats(obj: bpy.types.Object) -> dict:
    """Comptage réel : verts, faces, triangles (évalués, jamais inventés)."""
    if obj.type != "MESH":
        return {}
    mesh = obj.data
    try:
        mesh.calc_loop_triangles()
        triangles = len(mesh.loop_triangles)
    except Exception:
        triangles = 0
    return {
        "name": obj.name,
        "vertices": len(mesh.vertices),
        "edges": len(mesh.edges),
        "faces": len(mesh.polygons),
        "triangles": triangles,
        "dimensions": [round(float(v), 4) for v in obj.dimensions],
        "location": [round(float(v), 4) for v in obj.location],
    }


def has_armature() -> bool:
    return any(o.type == "ARMATURE" for o in bpy.data.objects)


def scene_objects_summary() -> list[dict]:
    out = []
    for obj in bpy.data.objects:
        entry = {"name": obj.name, "type": obj.type,
                 "location": [round(float(v), 3) for v in obj.location]}
        if obj.type == "MESH":
            entry["stats"] = mesh_stats(obj)
        if obj.type == "MESH" and obj.data.materials:
            entry["materials"] = [m.name for m in obj.data.materials if m]
        out.append(entry)
    return out