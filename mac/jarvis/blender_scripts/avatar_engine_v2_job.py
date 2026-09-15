"""Real Blender runtime for MPFB_V1 static milestone.

This file intentionally contains no anatomical primitive construction. MPFB's
HumanService owns the basemesh; JARVIS only maps the preset, equips assets,
lights the scene, and renders the visual QA views.
"""
from __future__ import annotations

import math
import os
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
from common import boot, fail, finish, out, progress, settings, write_metadata  # noqa: E402

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402


MPFB_MODULE = "bl_ext.user_default.mpfb"
REQUIRED_VIEWS = ("front", "three_quarter", "side", "back", "face_closeup")


def _enable_mpfb():
    import addon_utils
    module = addon_utils.enable(MPFB_MODULE, default_set=False, persistent=False)
    if module is None or MPFB_MODULE not in bpy.context.preferences.addons:
        raise RuntimeError("MPFB2 extension is not installed or could not be enabled")
    from importlib import import_module
    return import_module(MPFB_MODULE)


def _asset(location_service, kind: str, name: str, suffix: str) -> str:
    root = Path(location_service.get_user_data(kind))
    exact = root / name / f"{name}{suffix}"
    if exact.is_file():
        return str(exact)
    matches = list(root.rglob(f"{name}{suffix}"))
    if not matches:
        raise RuntimeError(f"MPFB asset missing: {kind}/{name}{suffix}")
    return str(matches[0])


def _clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for datablocks in (bpy.data.meshes, bpy.data.curves, bpy.data.materials,
                       bpy.data.cameras, bpy.data.lights):
        for block in list(datablocks):
            if block.users == 0:
                datablocks.remove(block)


def _material(name: str, color: tuple[float, float, float, float], roughness=0.45):
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.diffuse_color = color
    material.use_nodes = True
    bsdf = next((n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Roughness"].default_value = roughness
    return material


def _add_floor():
    mesh = bpy.data.meshes.new("JARVIS_Studio_Floor_Mesh")
    mesh.from_pydata([(-4, -4, 0), (4, -4, 0), (4, 4, 0), (-4, 4, 0)], [], [(0, 1, 2, 3)])
    mesh.update()
    floor = bpy.data.objects.new("JARVIS_Studio_Floor", mesh)
    bpy.context.collection.objects.link(floor)
    floor.data.materials.append(_material("JARVIS_Studio_Floor_Material", (0.025, 0.035, 0.055, 1)))


def _bounds():
    points = []
    base = bpy.data.objects.get("JARVIS_MPFBA_Basemesh")
    candidates = [base] if base is not None else list(bpy.context.scene.objects)
    for obj in candidates:
        if obj is None:
            continue
        if obj.type != "MESH":
            continue
        if obj.name.startswith("JARVIS_Studio_Floor") or getattr(obj.data, "name", "").startswith("JARVIS_Studio_Floor"):
            continue
        points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    if not points:
        raise RuntimeError("MPFB produced no mesh")
    low = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    high = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return low, high


def _look_at(obj, target):
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()


def _studio():
    scene = bpy.context.scene
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except TypeError:
        scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 1024
    scene.render.resolution_y = 1024
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.world.color = (0.008, 0.012, 0.025)

    camera_data = bpy.data.cameras.new("JARVIS_Studio_Camera")
    camera = bpy.data.objects.new("JARVIS_Studio_Camera", camera_data)
    bpy.context.collection.objects.link(camera)
    scene.camera = camera
    for name, energy, size, loc in (
        ("Key", 1300, 4.0, (-2.7, -3.6, 3.3)),
        ("Fill", 850, 3.0, (2.8, -2.0, 2.4)),
        ("Rim", 1100, 2.5, (0.0, 2.8, 3.0)),
    ):
        data = bpy.data.lights.new(f"JARVIS_Studio_{name}", "AREA")
        data.energy = energy
        data.shape = "DISK"
        data.size = size
        light = bpy.data.objects.new(f"JARVIS_Studio_{name}", data)
        light.location = loc
        bpy.context.collection.objects.link(light)
    return camera


def _render_views(output_dir: str, low: Vector, high: Vector):
    scene = bpy.context.scene
    camera = scene.camera
    center = (low + high) * 0.5
    height = max(high.z - low.z, 1.0)
    # Keep the MPFB human as the subject.  Do not derive the camera distance
    # from accessory meshes: some MPFB imports retain stale bound-box data.
    distance = max(height * 1.7, 1.8)
    positions = {
        "front": (0, -distance, center.z),
        "three_quarter": (distance * 0.72, -distance * 0.72, center.z + height * 0.02),
        "side": (distance, 0, center.z),
        "back": (0, distance, center.z),
        "face_closeup": (0, -max(height * 0.80, 1.0), low.z + height * 0.82),
    }
    for view, position in positions.items():
        camera.location = position
        camera.data.lens = 65 if view == "face_closeup" else 52
        target = center if view != "face_closeup" else Vector((center.x, center.y, low.z + height * 0.82))
        _look_at(camera, target)
        path = os.path.join(output_dir, f"{view}.png")
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)


def _export_glb(path: str):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in bpy.context.scene.objects:
        if obj.type == "MESH" and not obj.name.startswith("JARVIS_Studio_Floor"):
            obj.select_set(True)
    bpy.context.view_layer.objects.active = next(
        (obj for obj in bpy.context.scene.objects if obj.type == "MESH" and not obj.name.startswith("JARVIS_Studio_Floor")), None)
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", export_apply=False,
                              export_skins=False, export_morph=False,
                              export_animations=False, export_cameras=False, export_lights=False)


def run():
    st = settings()
    stale_error = Path(out("error.txt"))
    if stale_error.exists():
        stale_error.unlink()
    _enable_mpfb()
    from importlib import import_module
    HumanService = import_module(f"{MPFB_MODULE}.services.humanservice").HumanService
    LocationService = import_module(f"{MPFB_MODULE}.services.locationservice").LocationService

    preset = st.get("parameters") or {}
    macro = st.get("mpfb_macro_details") or {
        "gender": 0.15, "age": 0.18, "muscle": 0.34, "weight": 0.4,
        "proportions": 0.45, "height": 0.5, "cupsize": 0.5, "firmness": 0.62,
        "race": {"asian": 0.08, "caucasian": 0.84, "african": 0.08},
    }
    _clear_scene()
    progress(0.12, "MPFB: création du basemesh humain", "geometry")
    base = HumanService.create_human(scale=0.1, feet_on_ground=True, macro_detail_dict=macro)
    if base is None or len(base.data.vertices) < 10000:
        raise RuntimeError("MPFB basemesh missing or unexpectedly low density")
    base.name = "JARVIS_MPFBA_Basemesh"

    progress(0.28, "MPFB: peau, yeux, cheveux et tenue", "materials")
    skin = _asset(LocationService, "skins", str(preset.get("skin", "young_caucasian_male")), ".mhmat")
    HumanService.set_character_skin(skin, base, skin_type="MAKESKIN", material_instances=False)
    eye_asset = _asset(LocationService, "eyes", "high-poly", ".mhclo")
    # Blender 5.0 changed several procedural node socket names used by the
    # MPFB procedural-eye graph.  The shipped high-poly eye material is the
    # stable MPFB path for this static milestone and avoids a false eye render.
    HumanService.add_mhclo_asset(eye_asset, base, asset_type="Eyes", material_type="MAKESKIN",
                                 set_up_rigging=False, interpolate_weights=False, import_subrig=False,
                                 import_weights=False)
    hair = _asset(LocationService, "hair", str(preset.get("hair", "short03")), ".mhclo")
    HumanService.add_mhclo_asset(hair, base, asset_type="Hair", material_type="MAKESKIN",
                                 set_up_rigging=False, interpolate_weights=False, import_subrig=False,
                                 import_weights=False)
    outfit = _asset(LocationService, "clothes", str(preset.get("outfit", "male_casualsuit03")), ".mhclo")
    HumanService.add_mhclo_asset(outfit, base, asset_type="Clothes", material_type="MAKESKIN",
                                 set_up_rigging=False, interpolate_weights=False, import_subrig=False,
                                 import_weights=False)
    shoes = _asset(LocationService, "clothes", "shoes03", ".mhclo")
    HumanService.add_mhclo_asset(shoes, base, asset_type="Clothes", material_type="MAKESKIN",
                                 set_up_rigging=False, interpolate_weights=False, import_subrig=False,
                                 import_weights=False)

    camera = _studio()
    low, high = _bounds()
    progress(0.55, "Visual QA: front / 3/4 / side / back / close-up", "rendering")
    _render_views(str(st.get("output_dir") or "."), low, high)
    blend_path = out("model.blend")
    glb_path = out("model.glb")
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    _export_glb(glb_path)
    renders = {view: os.path.join(str(st.get("output_dir") or "."), f"{view}.png")
               for view in REQUIRED_VIEWS}
    report = {
        "build_id": "JARVIS_MPFBA_V1_20260911_A",
        "preset_id": st.get("preset_id", "jarvis_v1"),
        "mpfb_runtime": True,
        "mpfb_module": MPFB_MODULE,
        "basemesh": base.name,
        "basemesh_vertices": len(base.data.vertices),
        "mesh_objects": sum(1 for o in bpy.data.objects if o.type == "MESH"),
        "armatures": sum(1 for o in bpy.data.objects if o.type == "ARMATURE"),
        "rigged": False,
        "primitive_anatomy": False,
        "renders": renders,
        "milestone": "static",
        "quality_gate": "USER_APPROVAL",
        "score": None,
        "legacy_prototype_used": False,
        "model_blend": blend_path,
        "model_glb": glb_path,
    }
    write_metadata({"avatar_engine_v2": True, "avatar_validation": report,
                    "renders": renders, "preset": preset})
    if stale_error.exists():
        stale_error.unlink()
    progress(1.0, "Candidat MPFB statique prêt pour validation utilisateur", "completed")
    return report


if __name__ == "__main__":
    boot()
    try:
        run()
    except Exception as exc:
        traceback.print_exc()
        fail(f"avatar_engine_v2: {exc}")
        raise
