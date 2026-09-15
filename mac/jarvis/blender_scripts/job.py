"""Dispatcheur principal : execute l'operation demandee puis finalise les metadonnees.

Ce script est lance par jarvis/blender.py (JOB_SCRIPT). Il :
    1. charge la configuration (boot),
    2. route vers le script d'operation (create_model, render_model, ...),
    3. normalise les fichiers produits (model.blend, model.glb, model.obj...),
    4. VERIFIE reellement les exports (parse du GLB) et compile les metadonnees
       attendues par le manager (verified / polycount / rig / animations / files).
"""
from __future__ import annotations

import glob
import importlib.util
import json
import os
import sys
import traceback

sys.path.insert(0, __file__.rsplit("\\", 1)[0] + "\\lib")
sys.path.insert(0, __file__.rsplit("/", 1)[0] + "/lib")

ACTION_TO_SCRIPT = {
    "create_model": "create_model.py",
    "create": "create_model.py",
    "modify_model": "modify_model.py",
    "modify": "modify_model.py",
    "import_model": "import_model.py",
    "import": "import_model.py",
    "export_model": "export_model.py",
    "export": "export_model.py",
    "convert_model": "convert_model.py",
    "convert": "convert_model.py",
    "render_model": "render_model.py",
    "render": "render_model.py",
    "animate_model": "animate_model.py",
    "animate": "animate_model.py",
    "rig_model": "rig_model.py",
    "rig": "rig_model.py",
    "material_model": "material_model.py",
    "material": "material_model.py",
    "texture_model": "texture_model.py",
    "texture": "texture_model.py",
    "optimize_model": "optimize_model.py",
    "optimize": "optimize_model.py",
    "inspect_model": "inspect_model.py",
    "inspect": "inspect_model.py",
    "generate_preview": "generate_preview.py",
    "preview": "generate_preview.py",
    "run_script": "run_wrapper.py",
    "update_avatar": "update_avatar.py",
    "render_avatar_view": "render_avatar_views.py",
    "avatar_engine": "avatar_engine_job.py",
}

_CANONICAL = {".blend": "model.blend", ".glb": "model.glb", ".gltf": "model.gltf",
              ".obj": "model.obj", ".stl": "model.stl", ".fbx": "model.fbx"}


def normalize_outputs(output_dir: str) -> list[str]:
    """Rename les fichiers en noms canoniques s'ils ne le sont pas deja."""
    files = [f for f in glob.glob(os.path.join(output_dir, "*")) if os.path.isfile(f)]
    for path in files:
        name = os.path.basename(path)
        if name in {"error.txt", "metadata.json", "preview.png", "render.png",
                    "inspection.json"}:
            continue
        suffix = os.path.splitext(name)[1].lower()
        canonical = _CANONICAL.get(suffix)
        if canonical and name != canonical:
            try:
                os.replace(path, os.path.join(output_dir, canonical))
            except OSError:
                pass
    return sorted(os.listdir(output_dir))


def finalize(output_dir: str, action: str) -> dict:
    from common import polycount, rig_summary, animations, verify_glb, settings, write_metadata

    meta_path = os.path.join(output_dir, "metadata.json")
    meta = {}
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh) or {}
    except Exception:
        meta = {}

    names = normalize_outputs(output_dir)
    verified = dict(meta.get("verified") or {})
    verified.setdefault("exports_failed", [])

    glb_path = os.path.join(output_dir, "model.glb")
    if os.path.exists(glb_path):
        verified["glb"] = verify_glb(glb_path)
        meta["glb"] = {"name": "model.glb", "size": int(os.path.getsize(glb_path))}

    exports_raw = meta.pop("exports", None)
    failures = []
    if isinstance(exports_raw, dict):
        failures = [f"{k}: {str(v)[:120]}" for k, v in exports_raw.items()
                    if "export_failed" in str(v)]
    elif isinstance(exports_raw, list):
        failures = [str(e)[:120] for e in exports_raw if "export_failed" in str(e)]
    if failures:
        verified.setdefault("exports_failed", []).extend(failures)
    summary = []
    for suffix, canonical in _CANONICAL.items():
        if canonical == "model.blend" or canonical not in names:
            continue
        path = os.path.join(output_dir, canonical)
        if os.path.isfile(path):
            summary.append({"name": canonical, "ok": True,
                            "size": int(os.path.getsize(path))})
    if summary:
        meta["exports"] = summary

    meta.pop("image", None)
    files = [n for n in names if n != "metadata.json"]
    meta["files"] = files
    meta["verified"] = verified
    if "render.png" in files:
        meta["image"] = "render.png"
    elif "preview.png" in files:
        meta["image"] = "preview.png"
    meta["polycount"] = int(meta.get("polycount") or 0) or int(polycount())
    if action in {"animate", "create_model", "modify_model", "import_model",
                  "material_model", "optimize_model"}:
        rig = rig_summary()
        if rig.get("rigged"):
            meta["rig"] = rig
        anims = animations()
        if anims:
            declared = [str(a) for a in (meta.get("animations") or [])]
            meta["animations"] = list(dict.fromkeys(declared + [str(a) for a in anims]))
    write_metadata(meta)
    return meta


def main() -> int:
    from common import boot, settings, progress, finish, fail

    st = boot()
    action = str(st.get("__action") or st.get("action") or "") or "inspect_model"
    script = ACTION_TO_SCRIPT.get(action)
    if not script:
        raise RuntimeError(f"Action inconnue : {action}")

    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), script)
    if not os.path.exists(script_path):
        raise RuntimeError(f"Script operation introuvable : {script}")

    progress(0.02, f"Operation {action}", "preparing")
    try:
        spec = importlib.util.spec_from_file_location("jarvis_op_script", script_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception:
        fail(f"{action} : {traceback.format_exc()}")
        raise

    meta = finalize(str(st.get("output_dir") or "."), action)
    progress(1.0, "Job termine", "finalizing")
    finish(meta, message=f"Job {action} terminé")
    return 0


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        try:
            from common import fail
            fail(f"job : {exc}")
        except Exception:
            print(f"JARVIS_SIGNAL  job_error  {exc}", file=sys.stderr)
        sys.exit(1)