"""Rendu : moteurs, GPU Cycles, rendu d'une vue en image PNG."""
from __future__ import annotations

import bpy

ENGINE_CANDIDATES = {
    "cycles": ("CYCLES",),
    "workbench": ("BLENDER_WORKBENCH",),
    "eevee": ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"),
}


def set_engine(engine: str) -> str:
    scene = bpy.context.scene
    wanted = str(engine or "cycles").lower()
    candidates = ENGINE_CANDIDATES.get(wanted, ENGINE_CANDIDATES["cycles"])
    for name in candidates:
        try:
            scene.render.engine = name
            return scene.render.engine
        except Exception:
            continue
    try:
        scene.render.engine = "BLENDER_WORKBENCH"
        return scene.render.engine
    except Exception:
        return ""


def prefer_gpu() -> None:
    """Active le GPU Cycles s'il existe (OPTIX, CUDA, HIP, METAL)."""
    scene = bpy.context.scene
    if scene.render.engine != "CYCLES":
        return
    addon = bpy.context.preferences.addons.get("cycles")
    if not addon:
        return
    prefs = addon.preferences
    try:
        items = getattr(prefs.bl_rna.properties.get("compute_device_type"),
                        "enum_items", None)
        types = [i.identifier for i in items] if items else []
    except Exception:
        types = []
    for device_type in ("OPTIX", "CUDA", "HIP", "METAL"):
        if device_type in types:
            try:
                prefs.compute_device_type = device_type
                break
            except Exception:
                continue
    try:
        scene.cycles.device = "GPU"
    except Exception:
        pass


def render_still(path: str, width: int = 512, height: int = 512,
                 engine: str = "cycles", samples: int = 0,
                 use_gpu: bool = True) -> str:
    scene = bpy.context.scene
    set_engine(engine)
    scene.render.image_settings.file_format = "PNG"
    scene.render.resolution_x = max(64, int(width or 512))
    scene.render.resolution_y = max(64, int(height or 512))
    scene.render.resolution_percentage = 100
    scene.render.filepath = path
    if scene.render.engine == "CYCLES":
        try:
            scene.cycles.samples = max(8, int(samples or 64))
        except Exception:
            pass
        if use_gpu:
            prefer_gpu()
    bpy.ops.render.render(write_still=True)
    return path