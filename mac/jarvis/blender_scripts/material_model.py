"""Script d'opération : appliquer un matériau (preset ou paramètres PBR)."""
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
    from materials import resolve_merge, apply_to_object, apply_material, object_materials  # noqa
    from camera import add_camera  # noqa
    from lights import default_lights  # noqa
    from render import render_still  # noqa

    st = boot()
    progress(0.05, "Configuration chargée", "initialisation")

    spec = st.get("material") or {}
    if isinstance(spec, str):
        spec = {"preset": spec}
    if st.get("preset"):
        spec["preset"] = st["preset"]
    merged = resolve_merge({**dict(spec)})

    src = st.get("source") or st.get("blend_in")
    if src and os.path.exists(src):
        progress(0.18, f"Ouverture {os.path.basename(src)}…", "import")
        open_blend(src)
    else:
        candidates = sorted(glob.glob(os.path.join(str(settings().get("output_dir") or "."), "model.blend")))
        if candidates:
            progress(0.18, "Ouverture du modèle…", "import")
            open_blend(candidates[-1])

    meshes = [o for o in all_meshes() if o.name != "Sol_JARVIS"]
    target = st.get("target") or st.get("targets") or []
    targets = target if isinstance(target, list) else [target]
    if targets:
        named = [o for o in all_meshes() if o.name in targets]
        if named:
            meshes = named
    if not meshes:
        raise RuntimeError("Aucun maillage à teindre.")

    progress(0.5, f"Application du matériau…", "materials")
    preset_name = str(st.get("preset") or (spec.get("preset") if isinstance(spec, dict) else "") or "").strip().lower()
    mat_name = f"Mat_JARVIS_{preset_name}" if preset_name else "Mat_JARVIS"
    mat_block = None
    for obj in meshes:
        if mat_block is None:
            mat_block = apply_to_object(obj, color=merged.get("color", "6e5f7d"),
                        metallic=float(merged.get("metallic", 0.0)),
                        roughness=float(merged.get("roughness", 0.5)),
                        emission_strength=float(merged.get("emission_strength", 0.0)),
                        emission_color=merged.get("emission_color", merged.get("color")),
                        alpha=float(merged.get("alpha", 1.0)),
                        name=mat_name)
        else:
            apply_material(obj, mat_block)

    if not any(o.type == "CAMERA" for o in bpy.data.objects):
        add_camera("three_quarter")
    if not any(o.type == "LIGHT" for o in bpy.data.objects):
        default_lights()
    if st.get("preview") is not False:
        progress(0.7, "Rendu preview…", "rendering")
        render_still(out("preview.png"),
                     engine=str(st.get("render_engine") or st.get("engine") or "cycles"),
                     samples=int(st.get("samples", 64)),
                     use_gpu=bool(st.get("use_gpu", True)))

    progress(0.86, "Sauvegarde + export…", "exporting")
    save_blend(out("model.blend"))
    exports = {}
    try:
        exports["glb"] = export_glb(out("model.glb"))
    except Exception as exc:
        exports["glb"] = f"export_failed:{exc}"

    finish({
        "objects": [mesh_stats(o) for o in meshes],
        "materials": sorted({m for o in meshes for m in object_materials(o)}),
        "material": merged.get("preset") or merged.get("color", "matériau"),
        "exports": exports,
    }, message=f"Matériau appliqué sur {len(meshes)} objet(s)")
except Exception:
    try:
        fail(f"material_model : {traceback.format_exc()}")
    except Exception:
        pass
    raise