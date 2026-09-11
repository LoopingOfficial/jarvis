"""Sonde d'environnement : Python, moteurs de rendu, GPU réels — sans faire de rendu.

Sorties parsées par le manager Blender (jarvis/blender.py) :
    JARVIS_PY <json>    version Python embarquée
    JARVIS_GPU <json>   GPU détectable via Cycles (API + device + available)
    JARVIS_ENV <json>   moteurs et exportateurs réellement présents
"""
from __future__ import annotations

import json
import os
import sys


def main() -> None:
    import bpy

    py = sys.version.split()[0]

    gpu = {"available": False, "api": "", "name": "", "devices": []}
    try:
        addons = bpy.context.preferences.addons
        prefs = addons.get("cycles")
        if prefs is not None:
            dev_prefs = prefs.preferences
            devices = getattr(dev_prefs, "devices", None)
            names = []
            if devices:
                for d in devices:
                    name = getattr(d, "name", "")
                    if name:
                        names.append(name)
                kernel = {}
                for d in devices:
                    t = getattr(d, "type", "") or ""
                    if t and t not in kernel:
                        kernel[t] = getattr(d, "name", "")
                if names:
                    gpu["available"] = True
                    gpu["devices"] = sorted(names)
                    gpu["api"] = next(iter(kernel), "") if kernel else ""
                    gpu["name"] = ", ".join(kernel.values()) or names[0]
    except Exception:
        pass

    engines = []
    try:
        items = bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items
        engines = [i.identifier for i in items]
    except Exception:
        engines = []

    cylivial = False
    try:
        bpy.context.scene.render.engine = "CYCLES"
        cylivial = True
    except Exception:
        cylivial = False
    try:
        bpy.ops.preferences.addon_enable(module="cycles")
    except Exception:
        pass

    env = {
        "engines": engines,
        "eevee": any(e in {"BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"} for e in engines),
        "cycles": cylivial,
        "gltf_exporter": hasattr(bpy.ops.export_scene, "gltf"),
        "fbx_exporter": hasattr(bpy.ops.export_scene, "fbx"),
        "obj_export": hasattr(bpy.ops.wm, "obj_export") or hasattr(bpy.ops.export_scene, "obj"),
    }

    for key, value in (("PY", py), ("GPU", gpu), ("ENV", env)):
        print(f"JARVIS_{key}  {json.dumps(value)}")

    try:
        path = os.environ.get("JARVIS_BLENDER_SETTINGS", "")
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            data.setdefault("environment", {"blender": bpy.app.version_string,
                                            "python": py, "engines": engines, "gpu": gpu})
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass
    print(f"JARVIS_SIGNAL  probe_ok  {bpy.app.version_string}")


try:
    main()
except Exception as exc:
    print(f"JARVIS_SIGNAL  probe_error  {exc}")
    sys.exit(1)