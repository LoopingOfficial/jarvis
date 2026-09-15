"""Script d'opération : modifier le modèle courant (operations explicites)."""
from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, __file__.rsplit("\\", 1)[0] + "\\lib")
sys.path.insert(0, __file__.rsplit("/", 1)[0] + "/lib")

try:
    from common import boot, settings, out, progress, finish, fail  # noqa
    from export import open_blend, save_blend, export_scene  # noqa
    from geometry import active_or_first, all_meshes, join_objects, mesh_stats, add_text  # noqa
    from materials import resolve_merge, apply_to_object, object_materials, light_color  # noqa
    from lights import add_light  # noqa
    from blueprints import build_parts  # noqa
    from camera import add_camera  # noqa
    from lights import default_lights  # noqa
    from render import render_still  # noqa
    from scene import add_floor  # noqa
    from mathutils import Vector
    import bpy  # noqa: E402
    import math

    st = boot()
    progress(0.05, "Configuration chargée", "initialisation")

    src = st.get("source") or st.get("blend_in")
    if src and os.path.exists(src):
        progress(0.15, "Ouverture du modèle projet…", "import")
        open_blend(src)

    objs = [o for o in all_meshes() if o.name != "Sol_JARVIS"]
    target = st.get("target")
    if target:
        t = next((o for o in objs if o.name == target), None)
        if t:
            objs = [t]
    if not objs:
        if active_or_first() and active_or_first().name != "Sol_JARVIS":
            objs = [active_or_first()]
    if not objs:
        raise RuntimeError("Aucun modèle à modifier.")

    primary = objs[0]
    ops = st.get("operations") or []
    instructions = []

    def _apply_one(obj):
        if st.get("rename"):
            obj.name = str(st["rename"])[:60]
        if st.get("scale") is not None:
            s = st["scale"]
            obj.scale = (Vector([float(s)] * 3) if isinstance(s, (int, float))
                         else Vector([float(x) for x in s]))
        if st.get("position") is not None:
            obj.location = Vector([float(x) for x in st["position"]])
        if st.get("rotation") is not None:
            r = st["rotation"]
            rot = (math.radians(r) if isinstance(r, (int, float)) else
                   [math.radians(float(x)) for x in r])
            obj.rotation_euler = rot

    for op in ops or []:
        op_type = str(op.get("op") or op.get("type") or "").lower()
        if op_type == "scale":
            factor = float(op.get("factor", 1.0))
            axis = str(op.get("axis") or "all").lower()
            for obj in objs:
                sc = list(obj.scale)
                if axis in ("all", ""):
                    sc = [s * factor for s in sc]
                elif axis == "z":
                    sc[2] *= factor
                elif axis == "xy":
                    sc[0] *= factor
                    sc[1] *= factor
                elif axis == "y":
                    sc[1] *= factor
                else:
                    sc[0] *= factor
                obj.scale = sc
            instructions.append(f"échelle {factor} ({axis})")
        elif op_type == "move" or op_type == "position":
            offset = [float(x) for x in op.get("offset") or op.get("position") or (0, 0, 0)]
            for obj in objs:
                obj.location = Vector((float(a) + b for a, b in zip(obj.location, offset)))
            instructions.append(f"déplacement {offset}")
        elif op_type == "rotate":
            angles = [float(x) for x in op.get("angles") or (0, 0, 0)]
            for obj in objs:
                base = list(obj.rotation_euler)
                obj.rotation_euler = [a + math.radians(b) for a, b in zip(base, angles)]
            instructions.append("rotation")
        elif op_type == "material":
            spec = op.get("material") or op
            merged = resolve_merge(spec if isinstance(spec, dict) else {"preset": spec})
            for obj in objs:
                apply_to_object(obj, color=merged.get("color", "6e5f7d"),
                                metallic=float(merged.get("metallic", 0.0)),
                                roughness=float(merged.get("roughness", 0.5)),
                                emission_strength=float(merged.get("emission_strength", 0.0)),
                                emission_color=merged.get("emission_color", merged.get("color")),
                                name="Mat_JARVIS")
            instructions.append(merged.get("preset") or merged.get("color", "matériau"))
        elif op_type == "light":
            _light_color = op.get("color") or "white"
            add_light("AREA", float(op.get("energy") or 250.0),
                      tuple(float(x) for x in (op.get("location") or (3.2, -4.0, 3.6))),
                      color=_light_color, name="Light_JARVIS_extra")
            instructions.append(f"éclairage {_light_color}")
        elif op_type == "delete":
            name = str(op.get("target") or "").strip()
            to_delete = [o for o in bpy.data.objects if o.name == name]
            if not to_delete and name:
                to_delete = [o for o in objs if name.lower() in o.name.lower()][:1]
            for obj in to_delete:
                bpy.data.objects.remove(obj, do_unlink=True)
            instructions.append(f"suppression {name}")
        elif op_type == "add_parts":
            for part in op.get("parts") or []:
                build_parts([part])
            instructions.append("ajout de pièces")
        elif op_type == "rename" and op.get("name"):
            primary.name = str(op["name"])[:60]
            instructions.append(f"renommage {op['name']}")
        elif op_type == "inherit" and st.get("blend_in"):
            from export import open_blend
            open_blend(st["blend_in"])
            instructions.append("reprise du modèle")

    for obj in objs:
        _apply_one(obj)

    if st.get("color"):
        merged = resolve_merge({"color": st["color"]})
        for obj in objs:
            apply_to_object(obj, color=merged.get("color", st["color"]),
                            metallic=float(st.get("metallic", 0.0)),
                            roughness=float(st.get("roughness", 0.5)),
                            name="Mat_JARVIS")

    instruction = st.get("instruction") or ", ".join(instructions) or "modification"
    if not any(o.type == "CAMERA" for o in bpy.data.objects):
        progress(0.5, "Caméra…", "camera")
        add_camera(st.get("angle", "three_quarter"))
    if not any(o.type == "LIGHT" for o in bpy.data.objects):
        progress(0.55, "Lumières…", "lights")
        default_lights()
    if not any(o.name == "Sol_JARVIS" for o in bpy.data.objects) and st.get("floor") is not False:
        add_floor()

    if st.get("preview") is not False:
        progress(0.7, "Rendu preview…", "rendering")
        render_still(out("preview.png"),
                     engine=str(st.get("render_engine") or st.get("engine") or "cycles"),
                     samples=int(st.get("samples", 64)),
                     use_gpu=bool(st.get("use_gpu", True)))

    progress(0.86, "Sauvegarde…", "exporting")
    save_blend(out("model.blend"))
    exports = {}
    for fmt in st.get("export_formats") or ["glb"]:
        try:
            exports[fmt] = export_scene(out(f"model.{fmt}"), fmt)
        except Exception as exc:
            exports[fmt] = f"export_failed:{exc}"

    finish({
        "instruction": str(instruction)[:600],
        "objects": [mesh_stats(o) for o in objs],
        "materials": sorted({m for o in objs for m in object_materials(o)}),
        "exports": exports,
    }, message=f"Modèle modifié : {instruction}"[:200])
except Exception:
    try:
        fail(f"modify_model : {traceback.format_exc()}")
    except Exception:
        pass
    raise