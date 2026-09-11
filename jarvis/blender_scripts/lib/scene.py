"""Assemblage de scène : plan de sol, purge des datablocks orphelins."""
from __future__ import annotations

import bpy

from materials import apply_to_object
from geometry import create_primitive


def add_floor(color="#252530", size: float = 20.0, y=None) -> bpy.types.Object:
    plane = create_primitive("plane", scale=None, size=1.0,
                             location=(0.0, float(y or -2.8), 0.0),
                             name="Sol_JARVIS")
    plane.scale = (float(size) / 2.0, float(size) / 2.0, 1.0)
    apply_to_object(plane, color=color, roughness=0.9)
    return plane


def purge() -> None:
    bpy.ops.outliner.orphans_purge()


def goto_configured_view() -> None:
    try:
        for area in bpy.context.screen.areas:
            if area.type == "VIEW_3D":
                for region in area.regions:
                    if region.type == "WINDOW":
                        space = area.spaces.active
                        if hasattr(space, "shading"):
                            space.shading.type = "MATERIAL"
                        region3d = space.region_3d
                        if region3d:
                            region3d.view_perspective = "PERSP"
                        break
                break
    except Exception:
        pass