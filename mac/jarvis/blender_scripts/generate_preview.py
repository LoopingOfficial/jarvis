"""Script d'opération : preview rapide d'un modèle sauvé (fond sombre, Cam_JARVIS auto)."""
from __future__ import annotations

import glob
import os
import sys
import traceback

sys.path.insert(0, __file__.rsplit("\\", 1)[0] + "\\lib")
sys.path.insert(0, __file__.rsplit("/", 1)[0] + "/lib")

try:
    from common import boot, settings, out, progress, finish, fail  # noqa
    from export import open_blend  # noqa
    from camera import add_camera  # noqa
    from lights import default_lights  # noqa
    from render import render_still  # noqa
    from scene import add_floor  # noqa

    st = boot()
    progress(0.05, "Configuration chargée", "initialisation")

    src = st.get("source") or st.get("blend_in")
    if not src or not os.path.exists(src):
        candidates = sorted(glob.glob(os.path.join(str(settings().get("output_dir") or "."), "model.blend")))
        if candidates:
            src = candidates[-1]
    if not src or not os.path.exists(src):
        raise RuntimeError("Aucun .blend pour preview.")

    progress(0.3, f"Ouverture {os.path.basename(src)}…", "import")
    open_blend(src)

    import bpy
    if not any(o.type == "CAMERA" for o in bpy.data.objects):
        add_camera(st.get("angle", "three_quarter"))
    if not any(o.type == "LIGHT" for o in bpy.data.objects):
        default_lights()
    if not any(o.name == "Sol_JARVIS" for o in bpy.data.objects) and st.get("floor") is not False:
        add_floor()

    scene = bpy.context.scene
    try:
        scene.world = None
    except Exception:
        pass
    try:
        scene.view_settings.view_transform = "Standard"
    except Exception:
        pass

    progress(0.65, "Rendu preview…", "rendu")
    psize = int(st.get("preview_size") or st.get("width") or 640)
    pengine = str(st.get("preview_engine") or st.get("engine") or st.get("render_engine") or "eevee").lower()
    render_still(out("preview.png"),
                 width=psize,
                 height=psize,
                 engine=pengine,
                 samples=int(st.get("samples") or (16 if pengine in {"eevee", "blender_eevee", "blender_workbench"} else 48)),
                 use_gpu=bool(st.get("use_gpu", True)))

    finish({
        "source": src,
        "image": out("preview.png"),
        "files": [out("preview.png")],
    }, message="Preview créée")
except Exception:
    try:
        fail(f"generate_preview : {traceback.format_exc()}")
    except Exception:
        pass
    raise