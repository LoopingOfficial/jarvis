"""Script d'opération : appliquer de vraies textures image sur le modèle."""
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
    from geometry import mesh_stats, all_meshes  # noqa
    from materials import apply_texture, object_materials  # noqa
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

    meshes = [o for o in all_meshes() if o.name != "Sol_JARVIS"]
    target = st.get("target")
    if target:
        named = next((o for o in meshes if o.name == target), None)
        if named:
            meshes = [named]
    if not meshes:
        raise RuntimeError("Aucun maillage à texturer.")

    base_dir = st.get("texture_dir") or st.get("work_dir") or str(settings().get("output_dir") or ".")

    def _resolve(path):
        return path if os.path.isabs(path) else os.path.join(str(base_dir), path)

    maps = {}
    slotted = st.get("textures") or {}
    if isinstance(slotted, dict):
        for k, v in slotted.items():
            maps.setdefault(str(k), _resolve(str(v)))
    for slot in ("base_color", "roughness", "metallic", "normal", "emission", "alpha"):
        if st.get(slot):
            maps[slot] = _resolve(st[slot])
    for key in ("baseColor", "albedo"):
        if st.get(key):
            maps.setdefault("base_color", _resolve(st[key]))
    if st.get("images"):
        mapped = st["images"]
        if isinstance(mapped, dict):
            for k, v in mapped.items():
                maps.setdefault(str(k), _resolve(str(v)))
    if not maps:
        albedo = st.get("albedo") or (st.get("material") or {}).get("base_color")
        if albedo:
            maps["base_color"] = _resolve(str(albedo))
    if not maps:
        raise RuntimeError("Aucune texture fournie (clefs textures/base_color/images/albedo attendues).")

    progress(0.4, f"Application de {len(maps)} texture(s)…", "textures")
    applied = []
    for obj in meshes:
        try:
            apply_texture(obj, maps, scale=float(st.get("scale") or 1.0))
            applied.append(obj.name)
        except Exception as exc:
            progress(0.0, f"Texture échouée sur {obj.name} : {exc}", "échec")

    if not applied:
        raise RuntimeError("Échec d'application des textures.")

    if not any(o.type == "CAMERA" for o in bpy.data.objects):
        add_camera("three_quarter")
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
        "objects": applied,
        "maps": {k: os.path.basename(v) for k, v in maps.items()},
        "materials": sorted({m for o in meshes for m in object_materials(o)}),
        "exports": exports,
    }, message=f"Textures appliquées ({len(applied)} objet(s))")
except Exception:
    try:
        fail(f"texture_model : {traceback.format_exc()}")
    except Exception:
        pass
    raise