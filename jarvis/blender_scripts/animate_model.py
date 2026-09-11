"""Script d'opération : animer un objet (rotation, rebond, orbite, pulsation)."""
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
    from geometry import active_or_first, mesh_stats, all_meshes  # noqa
    from anim import animate  # noqa
    from camera import add_camera  # noqa
    from lights import default_lights  # noqa
    from render import render_still  # noqa

    st = boot()
    progress(0.05, "Configuration chargée", "initialisation")

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
    target = st.get("target")
    if target and target in bpy.data.objects:
        obj = bpy.data.objects[target]
    if obj is None:
        meshes = [o for o in all_meshes() if o.name != "Sol_JARVIS"]
        obj = meshes[0] if meshes else None
    if obj is None:
        raise RuntimeError("Aucun objet à animer.")

    clips = st.get("clips") or [st.get("clip")] or ["spin"]
    clips = [str(c).strip().lower() for c in clips if str(c).strip()]
    SUPPORTED = {"spin", "rotate", "turn", "twirl", "float", "pulse", "bounce", "orbit"}
    unknown = [c for c in clips if c not in SUPPORTED]
    if unknown:
        raise RuntimeError(
            "Animation non implémentée : " + ", ".join(unknown)
            + ". Animation d'objet disponible uniquement : " + ", ".join(sorted(SUPPORTED))
            + ". Les clips de personnage (idle, walk, wave...) ne sont pas encore pris en charge.")
    primary = clips[0]
    kind_map = {"spin": "rotate", "rotate": "rotate", "turn": "rotate",
                "twirl": "rotate", "float": "pulse", "pulse": "pulse",
                "bounce": "bounce", "orbit": "orbit"}
    kind = kind_map.get(primary, "rotate")
    fps = max(1, int(st.get("fps") or 30))
    duration = max(1.0, float(st.get("duration") or 0) or 5.0)
    frames = (1, max(2, int(duration * fps)))
    axis = str(st.get("axis") or "Z").upper()

    progress(0.4, f"Animation {kind}…", "animation")
    animate(obj, kind=kind, axis=axis,
            degrees=float(st.get("degrees", 360.0)),
            frames=frames,
            amplitude=float(st.get("amplitude", 0.4)),
            loops=int(st.get("loops", 1)))

    if not any(o.type == "CAMERA" for o in bpy.data.objects):
        add_camera(st.get("angle", "three_quarter"))
    if not any(o.type == "LIGHT" for o in bpy.data.objects):
        default_lights()
    if st.get("preview") is not False:
        progress(0.66, "Rendu preview…", "rendering")
        render_still(out("preview.png"),
                     engine=str(st.get("render_engine") or st.get("engine") or "cycles"),
                     samples=int(st.get("samples", 64)),
                     use_gpu=bool(st.get("use_gpu", True)))

    progress(0.85, "Sauvegarde + export…", "exporting")
    save_blend(out("model.blend"))
    exports = {}
    try:
        exports["glb"] = export_glb(out("model.glb"))
    except Exception as exc:
        exports["glb"] = f"export_failed:{exc}"

    finish({
        "object": obj.name,
        "kind": kind,
        "clips": clips,
        "frames": list(frames),
        "animations": [primary, f"{kind}_{axis}"],
        "stats": mesh_stats(obj),
        "exports": exports,
    }, message=f"Animation {kind} appliquée")
except Exception:
    try:
        fail(f"animate_model : {traceback.format_exc()}")
    except Exception:
        pass
    raise