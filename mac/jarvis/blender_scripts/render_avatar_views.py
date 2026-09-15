"""Script Blender : rendu multi-vues d'un avatar (front, profil, 3/4, full body).

Ce script est exécuté headless par Blender via job.py.
Il charge un .blend, place une caméra pour la vue demandée, rend l'image.
"""
from __future__ import annotations

import os
import sys


VIEWS_CONFIG = {
    "front": {
        "location": (0, -2.5, 1.4),
        "rotation": (1.5708, 0, 0),
        "focal": 50,
    },
    "side": {
        "location": (-2.5, 0, 1.4),
        "rotation": (1.5708, 0, 1.5708),
        "focal": 50,
    },
    "34": {
        "location": (-1.8, -1.8, 1.5),
        "rotation": (1.52, 0, 0.78),
        "focal": 50,
    },
    "full": {
        "location": (0, -3.5, 1.0),
        "rotation": (1.48, 0, 0),
        "focal": 35,
    },
}


def main():
    from common import settings, progress, fail

    st = settings()
    blend_in = st.get("blend_in", "")
    view = st.get("view", "front")
    output_path = st.get("output_path", "")
    engine = st.get("engine", "eevee")
    samples = int(st.get("samples", 64))
    width = int(st.get("width", 512))
    height = int(st.get("height", 512))

    if not blend_in or not os.path.exists(blend_in):
        fail(f"Fichier blend introuvable : {blend_in}")
        return

    if not output_path:
        output_dir = st.get("output_dir", ".")
        output_path = os.path.join(output_dir, f"preview_{view}.png")

    try:
        import bpy
    except ImportError:
        fail("Module bpy non disponible.")
        return

    progress(0.1, f"Chargement scène pour vue {view}", "geometry")
    bpy.ops.wm.open_mainfile(filepath=blend_in)

    config = VIEWS_CONFIG.get(view, VIEWS_CONFIG["front"])

    progress(0.3, f"Placement caméra vue {view}", "geometry")
    cam_data = bpy.data.cameras.new(f"Camera_{view}")
    cam_data.lens = config["focal"]
    cam_obj = bpy.data.objects.new(f"Camera_{view}", cam_data)
    bpy.context.collection.objects.link(cam_obj)
    cam_obj.location = config["location"]
    cam_obj.rotation_euler = config["rotation"]
    bpy.context.scene.camera = cam_obj

    progress(0.5, "Configuration rendu", "rendering")
    scene = bpy.context.scene
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100

    if engine.lower() == "cycles":
        scene.render.engine = 'CYCLES'
        scene.cycles.samples = samples
        scene.cycles.use_denoising = True
    else:
        scene.render.engine = 'BLENDER_EEVEE'
        scene.eevee.taa_render_samples = samples

    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
    scene.render.filepath = output_path

    progress(0.7, f"Rendu vue {view} ({engine})", "rendering")
    bpy.ops.render.render(write_still=True)

    progress(1.0, f"Rendu {view} terminé", "completed")

    out_dir = st.get("output_dir", ".")
    meta_path = os.path.join(out_dir, "metadata.json")
    import json
    meta = {"render_view": view, "render_path": output_path}
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        try:
            from common import fail
            fail(f"render_avatar_view: {exc}")
        except Exception:
            print(f"JARVIS_SIGNAL  render_avatar_view_error  {exc}", file=sys.stderr)
        sys.exit(1)
