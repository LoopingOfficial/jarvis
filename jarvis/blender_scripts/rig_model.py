"""Script d'opération : rigger un personnage avec le squelette JARVIS."""
from __future__ import annotations

import glob
import os
import sys
import traceback

sys.path.insert(0, __file__.rsplit("\\", 1)[0] + "\\lib")
sys.path.insert(0, __file__.rsplit("/", 1)[0] + "/lib")

import bpy  # noqa: E402

try:
    from common import boot, settings, out, progress, finish, fail  # noqa
    from export import open_blend, save_blend, export_glb  # noqa
    from geometry import active_or_first, all_meshes, scene_objects_summary  # noqa
    from rigify import build_human_rig, bind_mesh  # noqa
    from camera import add_camera  # noqa
    from lights import default_lights  # noqa
    from render import render_still  # noqa

    st = boot()
    progress(0.05, "Configuration chargée", "initialisation")

    src = st.get("source") or st.get("blend_in")
    if src and os.path.exists(src):
        progress(0.2, f"Ouverture {os.path.basename(src)}…", "import")
        open_blend(src)
    else:
        candidates = sorted(glob.glob(os.path.join(str(settings().get("output_dir") or "."), "*.blend")))
        if candidates:
            progress(0.2, f"Ouverture {os.path.basename(candidates[-1])}…", "import")
            open_blend(candidates[-1])

    mesh = None
    target = st.get("target")
    if target and target in bpy.data.objects and bpy.data.objects[target].type == "MESH":
        mesh = bpy.data.objects[target]
    if mesh is None:
        mesh = active_or_first()
    if mesh is None:
        raise RuntimeError("Aucun maillage à rigger.")

    progress(0.4, "Construction du squelette…", "rigging")
    rig_spec = st.get("rig_module")
    arm = build_human_rig(rig_spec, scale=float(st.get("scale", 1.0)),
                          name=st.get("rig_name", "AR_JARVIS"))
    progress(0.55, "Liaison du maillage…", "skinning")
    binding = bind_mesh(mesh, arm)

    if not any(o.type == "CAMERA" for o in bpy.data.objects):
        add_camera("three_quarter")
    if not any(o.type == "LIGHT" for o in bpy.data.objects):
        default_lights()
    if st.get("preview") is not False:
        progress(0.75, "Rendu preview…", "rendu")
        render_still(out("preview.png"), engine=st.get("render_engine", "cycles"),
                     samples=int(st.get("samples", 64)),
                     use_gpu=bool(st.get("use_gpu", True)))

    progress(0.88, "Sauvegarde + export…", "export")
    base = mesh.name.replace(" ", "_") if mesh.name else "rigged"
    save_blend(out(f"{base}_rig.blend"))
    exports = {}
    try:
        exports["glb"] = export_glb(out(f"{base}_rig.glb"))
    except Exception as exc:
        exports["glb"] = f"export_failed:{exc}"

    finish({
        "mesh": mesh.name,
        "armature": arm.name,
        "bones": len(arm.data.bones),
        "binding": binding,
        "exports": exports,
        "files": [out(f"{base}_rig.blend"), out(f"{base}_rig.glb"), out("preview.png")],
    }, message=f"Personnage riggé ({arm.name}, {len(arm.data.bones)} os)")
except Exception:
    try:
        fail(f"rig_model : {traceback.format_exc()}")
    except Exception:
        pass
    raise