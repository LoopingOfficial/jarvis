"""Constructeur declaratif : une liste de `parts` devient une VRAIE geometrie.

Une part est un dict :
    {"shape": "cube", "name": "base", "size": 0.4, "scale": [1, 1, 0.2],
     "location": [0, 0, 0.1], "rotation": [0, 0, 45],
     "material": {"preset": "metal", "color": "#00d2d3"},
     "modifiers": [{"type": "bevel", "width": 0.01}],
     "smooth": true}

Champs particuliers :
    "text"       : contenu d'un objet texte 3D (shape "text")
    "cut_from"   : nom d'une autre part -> booleen DIFFERENCE reel
    "union_with" : nom d'une autre part -> booleen UNION reel
"""
from __future__ import annotations

import math

import bpy
from mathutils import Vector

import materials as mat_lib
import modifiers as mod_lib

SHAPE_OPS = {
    "cube": ("primitive_cube_add", {"size": 2.0}),
    "box": ("primitive_cube_add", {"size": 2.0}),
    "sphere": ("primitive_uv_sphere_add", {"radius": 1.0, "segments": 48, "ring_count": 24}),
    "ball": ("primitive_uv_sphere_add", {"radius": 1.0, "segments": 48, "ring_count": 24}),
    "icosphere": ("primitive_ico_sphere_add", {"radius": 1.0, "subdivisions": 3}),
    "cylinder": ("primitive_cylinder_add", {"radius": 1.0, "depth": 2.0, "vertices": 48}),
    "tube": ("primitive_cylinder_add", {"radius": 1.0, "depth": 2.0, "vertices": 48}),
    "cone": ("primitive_cone_add", {"radius1": 1.0, "radius2": 0.0, "depth": 2.0,
                                    "vertices": 48}),
    "torus": ("primitive_torus_add", {"major_radius": 1.0, "minor_radius": 0.28,
                                      "major_segments": 56, "minor_segments": 20}),
    "plane": ("primitive_plane_add", {"size": 2.0}),
    "circle": ("primitive_circle_add", {"radius": 1.0, "vertices": 64, "fill_type": "NGON"}),
    "grid": ("primitive_grid_add", {"size": 2.0, "x_subdivisions": 12, "y_subdivisions": 12}),
    "monkey": ("primitive_monkey_add", {"size": 2.0}),
}


def _deg(values):
    return [math.radians(float(v)) for v in (list(values) + [0, 0, 0])[:3]]


def _vec3(values, default=(0.0, 0.0, 0.0)):
    if values is None:
        return list(default)
    if isinstance(values, (int, float)):
        return [float(values)] * 3
    out = [float(v) for v in list(values)[:3]]
    while len(out) < 3:
        out.append(float(default[len(out)]))
    return out


def create_text(part) -> bpy.types.Object:
    bpy.ops.object.text_add(location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.data.body = str(part.get("text") or "JARVIS")
    obj.data.size = float(part.get("size") or 1.0)
    obj.data.extrude = float(part.get("extrude", 0.12))
    obj.data.bevel_depth = float(part.get("bevel", 0.012))
    obj.data.bevel_resolution = 3
    obj.data.align_x = "CENTER"
    try:
        obj.data.align_y = "CENTER"
    except Exception:
        pass
    if part.get("convert_to_mesh", True):
        bpy.ops.object.convert(target="MESH")
        obj = bpy.context.active_object
    return obj


def create_capsule(part) -> bpy.types.Object:
    """Capsule reelle : cylindre + deux calottes, fusionnes."""
    radius = float(part.get("radius", part.get("size", 0.5)))
    height = float(part.get("height", 1.0))
    bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=height, vertices=40,
                                        location=(0, 0, 0))
    body = bpy.context.active_object
    for sign in (1, -1):
        bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, segments=40, ring_count=20,
                                             location=(0, 0, sign * height / 2.0))
        cap = bpy.context.active_object
        mod_lib.boolean(body, cap, "UNION", apply=True, delete_target=True)
    return body


def create_part(part) -> bpy.types.Object:
    """Cree UNE part reelle et applique transformation, materiau, modificateurs."""
    part = dict(part or {})
    shape = str(part.get("shape") or part.get("type") or "cube").lower()

    if shape in {"text", "text3d", "logo"}:
        obj = create_text(part)
    elif shape == "capsule":
        obj = create_capsule(part)
    else:
        op_name, defaults = SHAPE_OPS.get(shape, SHAPE_OPS["cube"])
        kwargs = dict(defaults)
        size = float(part.get("size", 1.0))
        if "size" in kwargs:
            kwargs["size"] = 2.0 * size
        for key in ("radius", "radius1", "major_radius"):
            if key in kwargs:
                kwargs[key] = float(part.get("radius", kwargs[key])) * size
        if "radius2" in kwargs:
            kwargs["radius2"] = float(part.get("radius2", 0.0)) * size
        if "minor_radius" in kwargs:
            kwargs["minor_radius"] = float(part.get("minor_radius",
                                                    kwargs["minor_radius"])) * size
        if "depth" in kwargs:
            kwargs["depth"] = float(part.get("height", kwargs["depth"])) * size
        if "vertices" in kwargs and part.get("segments"):
            kwargs["vertices"] = max(3, int(part["segments"]))
        kwargs["location"] = (0, 0, 0)
        getattr(bpy.ops.mesh, op_name)(**kwargs)
        obj = bpy.context.active_object

    obj.name = str(part.get("name") or shape)
    obj.scale = Vector(_vec3(part.get("scale"), (1.0, 1.0, 1.0)))
    obj.rotation_euler = _deg(part.get("rotation") or part.get("rotation_deg") or [0, 0, 0])
    obj.location = Vector(_vec3(part.get("location") or part.get("position")))

    if part.get("material"):
        mat_lib.apply_spec(obj, part["material"], "Mat_" + obj.name)
    if part.get("modifiers"):
        mod_lib.apply_spec(obj, part["modifiers"])
    if part.get("smooth"):
        mod_lib.shade_smooth(obj, float(part.get("smooth_angle", 35.0)))
    obj["jarvis_part"] = True
    return obj


def build(parts, progress_cb=None) -> dict:
    """Construit toutes les parts puis resout les booleens declares."""
    created = {}
    order = []
    booleans = []
    total = max(1, len(parts or []))
    for index, part in enumerate(parts or []):
        if not isinstance(part, dict):
            continue
        try:
            obj = create_part(part)
        except Exception as exc:
            created["__error_%d" % index] = str(exc)[:200]
            continue
        created[obj.name] = obj
        order.append(obj.name)
        for key, op in (("cut_from", "DIFFERENCE"), ("union_with", "UNION"),
                        ("intersect_with", "INTERSECT")):
            if part.get(key):
                booleans.append((str(part[key]), obj.name, op))
        if progress_cb:
            progress_cb((index + 1) / float(total))

    applied = []
    for target_name, tool_name, op in booleans:
        target = created.get(target_name) or bpy.data.objects.get(target_name)
        tool = created.get(tool_name) or bpy.data.objects.get(tool_name)
        if target is None or tool is None:
            continue
        try:
            mod_lib.boolean(target, tool, op, apply=True, delete_target=True)
            created.pop(tool_name, None)
            if tool_name in order:
                order.remove(tool_name)
            applied.append({"target": target_name, "tool": tool_name, "op": op})
        except Exception:
            pass

    objects = [o for o in (bpy.data.objects.get(n) for n in order) if o is not None]
    return {"objects": objects, "names": [o.name for o in objects], "booleans": applied,
            "errors": [v for k, v in created.items() if k.startswith("__error_")]}


def join_all(names=None, name: str = "Model"):
    """Fusionne les parts en un seul mesh (moins de draw calls)."""
    targets = [bpy.data.objects[n] for n in (names or [])
               if n in bpy.data.objects and bpy.data.objects[n].type == "MESH"]
    if not targets:
        targets = [o for o in bpy.data.objects if o.type == "MESH"]
    if len(targets) < 2:
        if targets:
            targets[0].name = name
            return targets[0]
        return None
    bpy.ops.object.select_all(action="DESELECT")
    for obj in targets:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = targets[0]
    try:
        bpy.ops.object.join()
    except Exception:
        return targets[0]
    merged = bpy.context.active_object
    merged.name = name
    return merged
