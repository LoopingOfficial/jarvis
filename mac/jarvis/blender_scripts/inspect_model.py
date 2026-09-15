"""Script d'opération : inventaire factuel du modèle courant (meta.inventory)."""
from __future__ import annotations

import glob
import json
import os
import sys
import traceback

sys.path.insert(0, __file__.rsplit("\\", 1)[0] + "\\lib")
sys.path.insert(0, __file__.rsplit("/", 1)[0] + "/lib")

try:
    from common import boot, settings, out, progress, finish, fail, rig_summary, animations, polycount  # noqa
    from export import open_blend  # noqa
    from geometry import scene_objects_summary, mesh_stats  # noqa

    st = boot()
    progress(0.05, "Configuration chargée", "initialisation")

    src = st.get("source") or st.get("blend_in")
    if src and os.path.exists(src):
        progress(0.25, f"Ouverture {os.path.basename(src)}…", "import")
        open_blend(src)
    else:
        candidates = sorted(glob.glob(os.path.join(str(settings().get("output_dir") or "."), "model.blend")))
        if candidates:
            progress(0.25, "Ouverture du modèle…", "import")
            open_blend(candidates[-1])

    progress(0.5, "Analyse de la scène…", "lecture")
    import bpy  # noqa: F401

    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    stats = [mesh_stats(o) for o in meshes]
    materials = []
    for obj in meshes:
        for m in obj.data.materials:
            if m and m.name not in [x["name"] for x in materials]:
                materials.append({"name": m.name, "color": list(m.diffuse_color)[:3] if hasattr(m, "diffuse_color") else []})
    textures = sorted(t.name for t in bpy.data.textures)
    lights = [{"name": o.name, "type": o.type} for o in bpy.data.objects if o.type == "LIGHT"]
    anims = animations()
    rig = rig_summary()
    dims = []
    if meshes:
        import mathutils
        box = mathutils.Vector((0, 0, 0)), mathutils.Vector((0, 0, 0))
        mins = mathutils.Vector((float("inf"),) * 3)
        maxs = mathutils.Vector((-float("inf"),) * 3)
        for o in meshes:
            for v in o.bound_box:
                world = o.matrix_world @ mathutils.Vector(v)
                for i in range(3):
                    mins[i] = min(mins[i], world[i])
                    maxs[i] = max(maxs[i], world[i])
        dims = [round(float(maxs[i] - mins[i]), 3) for i in range(3)]

    inventory = {
        "counts": {"objects": len(bpy.data.objects), "meshes": len(meshes),
                   "triangles": polycount(), "vertices": sum(s.get("vertices", 0) for s in stats)},
        "materials": materials,
        "textures": [{"name": t} for t in textures],
        "lights": lights,
        "animations": [{"name": a} for a in anims],
        "rig": {"rigged": rig.get("rigged", False), "bones": len(rig.get("skinned_meshes", [])),
                "skinned_meshes": rig.get("skinned_meshes", [])},
        "dimensions": dims,
        "objects": scene_objects_summary(),
    }
    progress(0.7, "Sauvegarde du rapport…", "écriture")
    try:
        with open(out("inspection.json"), "w", encoding="utf-8") as fh:
            json.dump(inventory, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass

    finish({
        "inventory": inventory,
    }, message=f"Scène analysée : {len(meshes)} mesh(s), {inventory['counts']['triangles']} triangles, {len(materials)} matériau(x)")
except Exception:
    try:
        fail(f"inspect_model : {traceback.format_exc()}")
    except Exception:
        pass
    raise