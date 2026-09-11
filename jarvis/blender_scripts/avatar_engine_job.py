"""Compatibility entry point: all new avatar jobs use MPFB_V1."""
from __future__ import annotations

import os
import shutil
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import boot, progress, fail, write_metadata  # noqa
from avatar_engine_v2_job import run  # noqa

st = boot()
progress(0.05, "AvatarEngine", "preparing")
live = os.path.normpath(str(st.get("live_glb") or ""))
out_dir = str(st.get("output_dir") or ".")
if not st.get("blend_out"):
    st["blend_out"] = os.path.join(out_dir, "model.blend")
if not st.get("glb_out"):
    st["glb_out"] = os.path.join(out_dir, "model.glb")
for key in ("glb_out", "blend_out"):
    target = os.path.normpath(str(st.get(key) or ""))
    if live and target and target == live:
        fail("Refus : AvatarEngine n'écrase jamais l'avatar live.")
        raise RuntimeError("live avatar protected")
try:
    progress(0.15, "Exécution déterministe MPFB_V1", "geometry")
    result = {"ok": bool(run()), "validation": {}}
    # The runtime always writes canonical job artifacts in output_dir.  The
    # host may additionally request protected candidate destinations.
    for source_name, target_key in (("model.glb", "glb_out"), ("model.blend", "blend_out")):
        source = os.path.join(out_dir, source_name)
        target = os.path.normpath(str(st.get(target_key) or ""))
        if target and os.path.isfile(source) and os.path.normpath(source) != target:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(source, target)
    try:
        import json
        with open(os.path.join(out_dir, "metadata.json"), "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        result["validation"] = meta.get("avatar_validation") or {}
    except Exception:
        pass
    progress(0.85, "Validation", "finalizing")
    validation = result.get("validation") or {}
    write_metadata({
        "avatar_engine": True,
        "avatar_validation": validation,
        "steps": [{"step": "mpfb_static", "ok": result.get("ok", False)}],
        "inventory": {
            "counts": {
                "objects": validation.get("mesh_objects", 0),
                "meshes": validation.get("mesh_objects", 0),
                "vertices": validation.get("basemesh_vertices", 0),
                "triangles": 0,
            },
            "rig": {"rigged": False, "bones": 0},
            "face_rig": False,
            "visemes": [],
            "outfit_meshes": {},
            "animation_clips": [],
            "renders": validation.get("renders", {}),
            "quality_gate": "USER_APPROVAL",
        },
    })
    if not result.get("ok"):
        fail("Quality gate AvatarEngine MPFB_V1 : runtime failed")
except Exception as exc:
    traceback.print_exc()
    fail(f"avatar_engine: {exc}")
    raise
