"""Script d'opération : rendre un modèle .blend sauvegardé en image PNG."""
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
    from export import open_blend, save_blend  # noqa
    from camera import add_camera  # noqa
    from lights import default_lights  # noqa
    from render import render_still  # noqa
    from scene import add_floor  # noqa

    st = boot()
    progress(0.05, "Configuration chargée", "initialisation")

    src = st.get("source") or st.get("blend_in")
    if not src or not os.path.exists(src):
        candidates = sorted(glob.glob(os.path.join(str(settings().get("output_dir") or "."), "model.blend")))
        candidates += sorted(glob.glob(os.path.join(str(settings().get("work_dir") or "."), "**", "*.blend"), recursive=True))
        candidates = sorted(set(candidates))
        if not candidates:
            raise RuntimeError("Aucun .blend à rendre.")
        src = candidates[-1]

    progress(0.3, f"Ouverture {os.path.basename(src)}…", "import")
    open_blend(src)

    if not any(o.type == "CAMERA" for o in bpy.data.objects):
        progress(0.45, "Caméra par défaut…", "caméra")
        add_camera(st.get("angle", "three_quarter"))
    if not any(o.name == "Sol_JARVIS" for o in bpy.data.objects) and st.get("floor") is not False:
        progress(0.5, "Plan de sol…", "scène")
        add_floor()
    if not any(o.type == "LIGHT" for o in bpy.data.objects):
        progress(0.55, "Lumières studio…", "lumières")
        default_lights()

    engine = str(st.get("engine") or st.get("render_engine") or "cycles").lower()
    progress(0.7, f"Rendu {engine}…", "rendu")
    render_still(out("render.png"),
                 width=int(st.get("width", 640)),
                 height=int(st.get("height", 640)),
                 engine=engine,
                 samples=int(st.get("samples", 64)),
                 use_gpu=bool(st.get("use_gpu", True)))
    progress(0.95, "Sauvegarde…", "export")
    try:
        save_blend(src)
    except Exception:
        pass

    finish({
        "source": src,
        "engine": engine,
        "files": [out("render.png")],
    }, message="Rendu terminé")
except Exception:
    try:
        fail(f"render_model : {traceback.format_exc()}")
    except Exception:
        pass
    raise