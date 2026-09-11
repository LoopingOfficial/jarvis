"""Rendus de contrôle de l'avatar (auto-revue visuelle).

    blender --background assets/blender/jarvis_avatar.blend --python assets/blender/preview.py -- --out <dir>
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy
from mathutils import Euler, Vector


def look_at(obj, target: Vector) -> None:
    direction = target - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def setup_lighting() -> None:
    world = bpy.data.worlds.new("PreviewWorld")
    bpy.context.scene.world = world
    world.use_nodes = True
    bg = next((n for n in world.node_tree.nodes if n.type == "BACKGROUND"), None)
    if bg is None:
        bg = world.node_tree.nodes.new("ShaderNodeBackground")
        out = next((n for n in world.node_tree.nodes if n.type == "OUTPUT_WORLD"), None)
        if out is not None:
            world.node_tree.links.new(bg.outputs[0], out.inputs[0])
    bg.inputs[0].default_value = (0.05, 0.07, 0.11, 1.0)
    bg.inputs[1].default_value = 1.1

    def lamp(name, kind, energy, location, color=(1, 1, 1)):
        data = bpy.data.lights.new(name, kind)
        data.energy = energy
        data.color = color
        if kind == "AREA":
            data.size = 2.0
        obj = bpy.data.objects.new(name, data)
        obj.location = location
        bpy.context.collection.objects.link(obj)
        look_at(obj, Vector((0, 0, 1.35)))
        return obj

    lamp("Key", "AREA", 140, (1.5, -2.3, 2.3), (1.0, 0.96, 0.92))
    lamp("Fill", "AREA", 45, (-2.2, -1.4, 1.6), (0.65, 0.78, 1.0))
    lamp("Rim", "AREA", 90, (-0.8, 2.4, 2.2), (0.35, 0.85, 1.0))


def render(name: str, cam_loc, target, out_dir: Path, res=(700, 900), lens=55.0) -> None:
    scene = bpy.context.scene
    cam_data = bpy.data.cameras.new("Cam_" + name)
    cam_data.lens = lens
    cam = bpy.data.objects.new("Cam_" + name, cam_data)
    cam.location = Vector(cam_loc)
    bpy.context.collection.objects.link(cam)
    look_at(cam, Vector(target))
    scene.camera = cam
    scene.render.resolution_x, scene.render.resolution_y = res
    scene.render.filepath = str(out_dir / f"{name}.png")
    bpy.ops.render.render(write_still=True)
    bpy.data.objects.remove(cam, do_unlink=True)


def apply_pose(action_name: str, frame: int) -> None:
    arm = bpy.data.objects.get("JARVIS_Armature")
    action = bpy.data.actions.get(action_name)
    if not arm or not action:
        return
    arm.animation_data_create()
    arm.animation_data.action = action
    bpy.context.scene.frame_set(frame)


def set_morph(name: str, value: float) -> None:
    head = bpy.data.objects.get("JARVIS_Head")
    if not head or not head.data.shape_keys:
        return
    key = head.data.shape_keys.key_blocks.get(name)
    if key:
        key.value = value


def clear_morphs() -> None:
    head = bpy.data.objects.get("JARVIS_Head")
    if not head or not head.data.shape_keys:
        return
    for key in head.data.shape_keys.key_blocks:
        if key.name != "Basis":
            key.value = 0.0


def main() -> None:
    argv = sys.argv
    args = argv[argv.index("--") + 1:] if "--" in argv else []
    out_dir = Path(args[args.index("--out") + 1]) if "--out" in args else Path("preview")
    out_dir.mkdir(parents=True, exist_ok=True)

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"
    # Filmic délave les couleurs : on juge le modèle sur ses vraies teintes.
    try:
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
    except Exception:
        pass
    try:
        scene.eevee.taa_render_samples = 32
        scene.eevee.use_gtao = True
    except Exception:
        pass
    setup_lighting()

    only = args[args.index("--only") + 1] if "--only" in args else ""

    if not only or only == "body":
        render("01_full_body", (0.0, -4.2, 1.15), (0, 0, 0.95), out_dir, (600, 950), 50)
        render("02_face", (0.10, -0.85, 1.63), (0, 0, 1.61), out_dir, (700, 800), 85)
        render("03_bust", (0.0, -2.0, 1.45), (0, 0, 1.40), out_dir, (700, 800), 60)
        render("04_profile", (2.6, -1.6, 1.45), (0, 0, 1.30), out_dir, (600, 900), 55)
        render("05_hands", (0.55, -1.0, 0.95), (0.30, 0, 0.85), out_dir, (700, 700), 80)

    if not only or only == "anim":
        apply_pose("walk_forward", 8)
        render("10_walk_a", (0.0, -4.0, 1.15), (0, 0, 0.95), out_dir, (600, 950), 50)
        apply_pose("walk_forward", 24)
        render("11_walk_b", (0.0, -4.0, 1.15), (0, 0, 0.95), out_dir, (600, 950), 50)
        apply_pose("gesture_explain", 18)
        render("12_explain", (0.0, -2.4, 1.40), (0, 0, 1.30), out_dir, (700, 800), 55)
        apply_pose("gesture_welcome", 20)
        render("13_welcome", (0.0, -3.0, 1.30), (0, 0, 1.15), out_dir, (700, 900), 50)
        apply_pose("idle_arms_crossed", 0)
        render("14_arms_crossed", (0.0, -3.0, 1.35), (0, 0, 1.20), out_dir, (700, 900), 50)
        apply_pose("idle_neutral", 0)

    if not only or only == "face":
        for label, keys in (
            ("20_smile", {"smile": 1.0, "browUp": 0.25}),
            ("21_blink", {"blinkLeft": 1.0, "blinkRight": 1.0}),
            ("22_viseme_A", {"viseme_A": 1.0}),
            ("23_viseme_O", {"viseme_O": 1.0}),
            ("24_viseme_MBP", {"viseme_MBP": 1.0}),
            ("25_viseme_I", {"viseme_I": 1.0}),
        ):
            clear_morphs()
            for key, value in keys.items():
                set_morph(key, value)
            render(label, (0.10, -0.80, 1.60), (0, 0, 1.58), out_dir, (620, 700), 85)
        clear_morphs()

    print("PREVIEW_DONE " + str(out_dir), flush=True)


if __name__ == "__main__":
    main()
