"""Script d'opération : optimiser un modèle (decimate, remove_doubles, triangulate)."""
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
    from geometry import active_or_first, mesh_stats  # noqa
    from modifiers import decimate, remove_doubles, triangulate  # noqa
    from camera import add_camera  # noqa
    from lights import default_lights  # noqa
    from render import render_still  # noqa

    st = boot()
    progress(0.05, "Configuration chargée", "initialisation")
    profile = str(st.get("target_profile") or st.get("target") or "web").lower()
    overrides = st.get("overrides") or {}

    src = st.get("source") or st.get("blend_in")
    if src and os.path.exists(src):
        progress(0.18, f"Ouverture {os.path.basename(src)}…", "import")
        open_blend(src)
    else:
        candidates = sorted(glob.glob(os.path.join(str(settings().get("output_dir") or "."), "model.blend")))
        if candidates:
            progress(0.18, "Ouverture du modèle…", "import")
            open_blend(candidates[-1])

    obj = None
    if st.get("target") and st["target"] in bpy.data.objects:
        obj = bpy.data.objects[st["target"]]
    if obj is None:
        obj = active_or_first()
    if obj is None:
        raise RuntimeError("Aucun objet à optimiser.")
    if obj.type != "MESH":
        raise RuntimeError("L'optimisation ne concerne que les maillages.")

    before = mesh_stats(obj)
    steps = []
    if st.get("remove_doubles", True):
        progress(0.35, "Suppression des doublons…", "optimisation")
        threshold = float(overrides.get("merge_distance") or st.get("merge_threshold", 0.0001))
        remove_doubles(obj, threshold)
        steps.append(f"merge_doubles:{threshold:.4f}")
    if profile != "none" and st.get("decimate", True) and before.get("triangles", 0) > 2000:
        max_tri = int(overrides.get("max_triangles") or
                      (4000 if profile == "jarvis_avatar" else 2500))
        ratio = max(0.05, min(0.9, max_tri / max(1, before.get("triangles", 1))))
        progress(0.5, f"Decimation → {int(ratio * 100)}%…", "optimisation")
        decimate(obj, ratio)
        steps.append(f"decimate:{ratio:.2f}")
    if st.get("triangulate", False):
        progress(0.6, "Triangulation…", "optimisation")
        triangulate(obj)
        steps.append("triangulate")
    after = mesh_stats(obj)

    if not any(o.type == "CAMERA" for o in bpy.data.objects):
        add_camera("three_quarter")
    if not any(o.type == "LIGHT" for o in bpy.data.objects):
        default_lights()
    if st.get("preview") is not False:
        progress(0.72, "Rendu preview…", "rendu")
        render_still(out("preview.png"), engine=str(st.get("engine") or st.get("render_engine") or "cycles"),
                     samples=int(st.get("samples", 64)),
                     use_gpu=bool(st.get("use_gpu", True)))

    progress(0.88, "Sauvegarde + export…", "export")
    base = obj.name.replace(" ", "_")
    save_blend(out(f"{base}_opti.blend"))
    exports = {}
    try:
        exports["glb"] = export_glb(out(f"{base}_opti.glb"))
    except Exception as exc:
        exports["glb"] = f"export_failed:{exc}"

    finish({
        "object": obj.name,
        "steps": steps,
        "target_profile": profile,
        "optimize": {
            "profile": profile,
            "before": {k: before.get(k) for k in ("vertices", "triangles", "faces")},
            "after": {k: after.get(k) for k in ("vertices", "triangles", "faces")},
            "draw_calls_estimate": max(1, len(bpy.data.objects)),
        },
        "exports": exports,
    }, message=f"Optimisation ({', '.join(steps) or 'aucune étape'})")
except Exception:
    try:
        fail(f"optimize_model : {traceback.format_exc()}")
    except Exception:
        pass
    raise