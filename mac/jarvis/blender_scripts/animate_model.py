"""Script d'opération : animer un objet (rotation, rebond, orbite, pulsation)
ou un personnage riggé (idle, walk, run, wave, talk, sit, jump...)."""
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
    from animation import RIG_CLIPS, animate_rig, animation_summary  # noqa
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

    # Cibles : armature préférée pour les clips de personnage, mesh sinon.
    rig = next((o for o in bpy.data.objects if o.type == "ARMATURE"), None)
    obj = None
    target = st.get("target")
    if target and target in bpy.data.objects:
        obj = bpy.data.objects[target]
        if obj.type == "ARMATURE":
            rig = obj
            obj = None
    if obj is None:
        meshes = [o for o in all_meshes() if o.name != "Sol_JARVIS"]
        obj = meshes[0] if meshes else None
    if rig is None and obj is None:
        raise RuntimeError("Aucun objet ni armature à animer.")

    clips = st.get("clips") or [st.get("clip")] or ["spin"]
    clips = [str(c).strip().lower() for c in clips if str(c).strip()]
    OBJECT_CLIPS = {"spin", "rotate", "turn", "twirl", "float", "pulse", "bounce", "orbit"}
    SUPPORTED = set(RIG_CLIPS) | OBJECT_CLIPS
    unknown = [c for c in clips if c not in SUPPORTED]
    if unknown:
        raise RuntimeError(
            "Animation non disponible : " + ", ".join(unknown)
            + ". Clips d'objet : " + ", ".join(sorted(OBJECT_CLIPS - {"turn"}))
            + ". Clips de personnage (nécessitent un rig) : " + ", ".join(sorted(RIG_CLIPS)))

    fps = max(1, int(st.get("fps") or 30))
    duration = max(1.0, float(st.get("duration") or 0) or 5.0)
    loops = max(1, int(st.get("loops", 1)))
    options = dict(st.get("options") or {})
    axis = str(st.get("axis") or "Z").upper()
    amplitude = float(st.get("amplitude", 1.0))
    degrees = float(st.get("degrees", 360.0))

    kind_map = {"spin": "rotate", "rotate": "rotate", "turn": "rotate",
                "twirl": "rotate", "float": "pulse", "pulse": "pulse",
                "bounce": "bounce", "orbit": "orbit"}
    frames = (1, max(2, int(duration * fps)))
    created: list[dict] = []
    progress(0.4, "Animation…", "animation")
    for clip in clips:
        if clip in RIG_CLIPS:
            if rig is None:
                raise RuntimeError(
                    f"Le clip de personnage '{clip}' nécessite un rig : lance d'abord "
                    "l'animation avec un personnage riggé (blender.make_rig ou import avatar riggé).")
            res = animate_rig(rig, clip=clip, duration=duration, fps=fps,
                              options=options, loop=loops >= 1 and clip in {"idle", "walk", "run", "talk"})
            created.append(res)
        else:
            if obj is None:
                raise RuntimeError(
                    f"Le clip d'objet '{clip}' nécessite un objet : précise un cible ou importe un modèle.")
            kind = kind_map.get(clip, "rotate")
            animate(obj, kind=kind, axis=axis, degrees=degrees,
                    frames=frames, amplitude=amplitude, loops=loops)
            created.append({"created": True, "name": f"{kind}_{axis}", "target": obj.name,
                            "kind": "object", "clip": clip})
    kind = "rig" if (rig and clips[0] in RIG_CLIPS) else kind_map.get(clips[0], "rotate")

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

    summary = animation_summary()
    animations = [a["name"] for a in created if a.get("created")] or summary or clips
    finish({
        "object": (obj.name if obj else "") or (rig.name if rig else ""),
        "armature": rig.name if rig else None,
        "kind": kind,
        "clips": clips,
        "frames": list(frames),
        "animations": animations,
        "stats": mesh_stats(obj) if obj else {"meshes": 0},
        "exports": exports,
    }, message="Animation appliquée")
except Exception:
    try:
        fail(f"animate_model : {traceback.format_exc()}")
    except Exception:
        pass
    raise