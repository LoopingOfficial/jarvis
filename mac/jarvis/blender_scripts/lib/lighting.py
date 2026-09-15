"""Eclairage : studio 3 points, HDRI, lampes ponctuelles, monde."""
from __future__ import annotations

import os

import bpy

from common import rgba


def clear_lights() -> None:
    for obj in [o for o in bpy.data.objects if o.type == "LIGHT"]:
        bpy.data.objects.remove(obj, do_unlink=True)


def world_background(color="0b0d14", strength: float = 0.6) -> None:
    world = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is None:
        bg = world.node_tree.nodes.new("ShaderNodeBackground")
        out = world.node_tree.nodes.get("World Output")
        if out:
            world.node_tree.links.new(bg.outputs["Background"], out.inputs["Surface"])
    bg.inputs["Color"].default_value = rgba(color, 1.0)
    bg.inputs["Strength"].default_value = float(strength)


def world_hdri(path: str, strength: float = 1.0) -> bool:
    """Branche une vraie HDRI. Retourne False si le fichier est absent."""
    if not path or not os.path.isfile(path):
        return False
    world = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    env = nt.nodes.new("ShaderNodeTexEnvironment")
    env.image = bpy.data.images.load(path, check_existing=True)
    bg.inputs["Strength"].default_value = float(strength)
    nt.links.new(env.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    return True


def _aim(obj, target) -> None:
    from mathutils import Vector

    direction = Vector(target) - obj.location
    if direction.length == 0:
        return
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def add_light(kind: str = "AREA", location=(3.0, -3.0, 4.0), energy: float = 400.0,
              color="ffffff", size: float = 2.0, name: str = "Light",
              target=(0.0, 0.0, 0.0)) -> bpy.types.Object:
    data = bpy.data.lights.new(name=name, type=str(kind).upper())
    data.energy = float(energy)
    data.color = rgba(color, 1.0)[:3]
    if hasattr(data, "size"):
        data.size = float(size)
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.location = location
    _aim(obj, target)
    return obj


def studio(radius: float = 4.0, height: float = 3.2, energy: float = 350.0,
           key_color="ffffff", fill_color="9ecbff", rim_color="00d2d3",
           target=(0.0, 0.0, 0.0)) -> list:
    """Eclairage 3 points reel, dimensionne autour de la cible."""
    clear_lights()
    r = max(1.0, float(radius))
    lights = [
        add_light("AREA", (r, -r, height), energy, key_color, r * 0.7, "Key", target),
        add_light("AREA", (-r * 1.1, -r * 0.5, height * 0.6), energy * 0.35, fill_color,
                  r * 0.9, "Fill", target),
        add_light("AREA", (0.0, r * 1.15, height * 0.8), energy * 0.6, rim_color,
                  r * 0.6, "Rim", target),
    ]
    world_background("0b0d14", 0.35)
    return lights


def add_accent(color="00d2d3", location=(0.0, 0.0, 1.0), energy: float = 120.0,
               name: str = "Accent") -> bpy.types.Object:
    """Lampe d'accent coloree (demande du type : ajoute une lumiere cyan)."""
    return add_light("POINT", location, energy, color, 0.25, name, (0.0, 0.0, 0.0))


def light_summary() -> list:
    out = []
    for o in bpy.data.objects:
        if o.type != "LIGHT":
            continue
        out.append({"name": o.name, "type": o.data.type,
                    "energy": round(float(o.data.energy), 2),
                    "color": [round(float(c), 3) for c in o.data.color]})
    return out


def has_light() -> bool:
    return any(o.type == "LIGHT" for o in bpy.data.objects)
