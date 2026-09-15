"""Script d'opération : envoyer un fichier 3D (GLB/GLTF/OBJ/STL/FBX) et l'optimiser."""
from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, __file__.rsplit("\\", 1)[0] + "\\lib")
sys.path.insert(0, __file__.rsplit("/", 1)[0] + "/lib")

import bpy  # noqa: E402

try:
    from common import boot, settings, out, progress, finish, fail  # noqa
    from export import import_file, open_blend, save_blend, export_glb, export_obj  # noqa
    from geometry import mesh_stats, clear_scene, active_or_first, scene_objects_summary  # noqa
    from materials import apply_to_object  # noqa
    from camera import add_camera  # noqa
    from lights import default_lights  # noqa
    from render import render_still  # noqa
    from scene import add_floor  # noqa

    st = boot()
    progress(0.05, "Configuration chargée", "initialisation")
    src = st.get("source") or st.get("file")
    if not src or not os.path.exists(src):
        raise RuntimeError(f"Fichier introuvable : {src}")

    if st.get("blend_in") and os.path.exists(str(st["blend_in"])):
        progress(0.15, "Ouverture du modèle projet…", "import")
        open_blend(st["blend_in"])

    progress(0.3, f"Import {os.path.basename(src)}…", "import")
    imported = import_file(src)
    if not imported:
        raise RuntimeError("Aucun objet importé.")
    obj = imported[0] if len(imported) == 1 else active_or_first()

    if st.get("recolor"):
        progress(0.4, "Application du matériau…", "matériau")
        apply_to_object(obj, color=st["recolor"],
                        metallic=float(st.get("metallic", 0.0)),
                        roughness=float(st.get("roughness", 0.5)),
                        name="Mat_JARVIS")
    if st.get("scale") is not None:
        s = st["scale"]
        obj.scale = [float(s)] * 3 if isinstance(s, (int, float)) else [float(x) for x in s]

    if st.get("floor") is not False and not any(o.name == "Sol_JARVIS" for o in bpy.data.objects):
        add_floor()
    add_camera(st.get("angle", "three_quarter"))
    default_lights()

    if st.get("preview") is not False:
        progress(0.7, "Rendu preview…", "rendu")
        render_still(out("preview.png"), engine=st.get("render_engine", "cycles"),
                     samples=int(st.get("samples", 64)),
                     use_gpu=bool(st.get("use_gpu", True)))

    progress(0.85, "Sauvegarde .blend…", "export")
    base = "modele_importe"
    save_blend(out(f"{base}.blend"))
    exports = {}
    for fmt in st.get("export_formats") or ["glb"]:
        try:
            if fmt == "obj":
                exports[fmt] = export_obj(out(f"{base}.obj"))
            else:
                exports[fmt] = export_glb(out(f"{base}.glb"))
        except Exception as exc:
            exports[fmt] = f"export_failed:{exc}"

    finish({
        "source": src,
        "imported": [mesh_stats(o) for o in imported if o.type == "MESH"],
        "objects": scene_objects_summary(),
        "exports": exports,
        "files": [out(f"{base}.blend")] + [p for p in exports.values() if isinstance(p, str) and not p.startswith("export_failed")],
    }, message=f"{len(imported)} objet(s) importé(s)")
except Exception:
    try:
        fail(f"import_model : {traceback.format_exc()}")
    except Exception:
        pass
    raise