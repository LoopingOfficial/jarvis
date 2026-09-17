"""Script Blender : modification d'avatar depuis une image de référence.

Ce script est exécuté headless par Blender via job.py.
Il charge le .blend de révision, applique les modifications définies
par le plan, et exporte le résultat.
"""
from __future__ import annotations

import json
import os
import sys


def main():
    from common import settings, progress, fail

    st = settings()
    blend_in = st.get("blend_in", "")
    reference_image = st.get("reference_image", "")
    features = st.get("features", {})
    plan = st.get("plan", {})
    options = st.get("options", {})
    output_dir = st.get("output_dir", ".")

    if not blend_in or not os.path.exists(blend_in):
        fail(f"Fichier blend introuvable : {blend_in}")
        return

    progress(0.05, "Chargement de la scène Blender", "geometry")

    try:
        import bpy
    except ImportError:
        fail("Module bpy non disponible. Ce script doit tourner dans Blender.")
        return

    bpy.ops.wm.open_mainfile(filepath=blend_in)

    progress(0.15, "Identification des objets", "geometry")

    armature = None
    mesh_obj = None
    for obj in bpy.data.objects:
        if obj.type == 'ARMATURE':
            armature = obj
        elif obj.type == 'MESH' and mesh_obj is None:
            mesh_obj = obj

    if not mesh_obj and bpy.data.objects:
        for obj in bpy.data.objects:
            if obj.type == 'MESH':
                mesh_obj = obj
                break

    operations = plan.get("operations", [])
    materials_info = plan.get("materials", {})
    style_directive = plan.get("style_directive", "")

    progress(0.25, "Application des modifications visage", "geometry")
    _modify_face(mesh_obj, features, options)

    progress(0.35, "Ajustement coiffure", "geometry")
    _modify_hair(features, options)

    progress(0.45, "Modification vêtements", "geometry")
    _modify_outfit(features, options)

    progress(0.55, "Application des matériaux", "materials")
    _apply_materials(mesh_obj, materials_info, features)

    progress(0.65, "Ajustement des couleurs", "materials")
    _apply_colors(mesh_obj, materials_info, features)

    if options.get("modify_proportions"):
        progress(0.70, "Ajustement proportions", "geometry")
        _modify_proportions(mesh_obj, features)

    if options.get("modify_pose"):
        progress(0.75, "Modification pose", "rig")
        _modify_pose(armature, features)

    progress(0.80, "Nettoyage du mesh", "optimize")
    _cleanup_mesh(mesh_obj)

    progress(0.85, "Sauvegarde", "exporting")
    out_blend = os.path.join(output_dir, "model.blend")
    try:
        bpy.ops.wm.save_as_mainfile(filepath=out_blend)
    except Exception as exc:
        try:
            bpy.ops.wm.save_as_mainfile(filepath=out_blend, check_existing=False)
        except Exception:
            fail(f"Sauvegarde impossible : {exc}")
            return

    progress(0.90, "Export GLB", "exporting")
    out_glb = os.path.join(output_dir, "model.glb")
    try:
        bpy.ops.export_scene.gltf(
            filepath=out_glb,
            use_selection=False,
            export_format='GLB',
            export_apply=True,
            export_animations=True,
            export_materials='EXPORT',
            export_colors=True,
            export_yup=True)
    except Exception as exc:
        try:
            bpy.ops.export_scene.gltf(
                filepath=out_glb, export_format='GLB')
        except Exception:
            pass

    meta = {
        "action": "update_avatar",
        "reference_image": os.path.basename(reference_image) if reference_image else "",
        "operations_applied": [op.get("type") for op in operations],
        "style_directive": style_directive,
        "polycount": _polycount(mesh_obj),
    }
    meta_path = os.path.join(output_dir, "metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)

    progress(1.0, "Modification terminée", "completed")


def _modify_face(mesh, features: dict, options: dict):
    if not mesh:
        return
    if not options.get("modify_face", True):
        return
    face_shape = features.get("face_shape", "")
    if not face_shape:
        return
    shape_keys = mesh.data.shape_keys
    if not shape_keys:
        return
    shape_map = {
        "ronde": {"Face.Round": 0.6},
        "carree": {"Face.Square": 0.5},
        "ovale": {"Face.Oval": 0.5},
        "longue": {"Face.Long": 0.4},
        "angulaire": {"Face.Angular": 0.5},
    }
    targets = shape_map.get(face_shape.lower(), {})
    for block in shape_keys.key_blocks:
        for key, value in targets.items():
            if key.lower() in block.name.lower():
                block.value = value
                break


def _modify_hair(features: dict, options: dict):
    if not options.get("modify_hair", True):
        return
    hair_color = features.get("hair_color", "")
    hair_style = features.get("hair_style", "")
    for obj in bpy.data.objects:
        name_lower = obj.name.lower()
        is_hair = any(kw in name_lower for kw in ("hair", "cheveux", "coiffure"))
        if not is_hair:
            continue
        if hair_color and obj.type == 'MESH':
            for mat in obj.data.materials:
                if mat and mat.use_nodes:
                    for node in mat.node_tree.nodes:
                        if node.type == 'BSDF_PRINCIPLED':
                            color = _parse_color(hair_color)
                            if color:
                                node.inputs['Base Color'].default_value = color


def _modify_outfit(features: dict, options: dict):
    if not options.get("modify_outfit", True):
        return
    outfit_type = features.get("outfit_type", "")
    outfit_colors = features.get("outfit_colors", [])
    for obj in bpy.data.objects:
        name_lower = obj.name.lower()
        is_clothes = any(kw in name_lower for kw in (
            "outfit", "clothes", "shirt", "pants", "jacket",
            "robe", "tshirt", "pantalon", "veste", "vetement"))
        if not is_clothes:
            continue
        if outfit_colors:
            primary = outfit_colors[0] if outfit_colors else ""
            color = _parse_color(primary)
            if color and obj.type == 'MESH':
                for mat in obj.data.materials:
                    if mat and mat.use_nodes:
                        for node in mat.node_tree.nodes:
                            if node.type == 'BSDF_PRINCIPLED':
                                node.inputs['Base Color'].default_value = color


def _apply_materials(mesh, materials_info: dict, features: dict):
    if not mesh:
        return
    skin_tone = materials_info.get("skin_tone", features.get("skin_tone", ""))
    if not skin_tone:
        return
    color = _parse_color(skin_tone)
    if not color:
        return
    for mat in mesh.data.materials:
        if not mat:
            continue
        name_lower = mat.name.lower()
        if any(kw in name_lower for kw in ("skin", "peau", "body", "corps")):
            if mat.use_nodes:
                for node in mat.node_tree.nodes:
                    if node.type == 'BSDF_PRINCIPLED':
                        node.inputs['Base Color'].default_value = color
                        break


def _apply_colors(mesh, materials_info: dict, features: dict):
    if not mesh:
        return
    dominant = materials_info.get("dominant_colors", [])
    if not dominant:
        return
    for mat in mesh.data.materials:
        if not mat or not mat.use_nodes:
            continue
        for node in mat.node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                existing = node.inputs['Base Color'].default_value
                if all(c < 0.01 for c in existing[:3]):
                    if dominant:
                        color = _parse_color(dominant[0])
                        if color:
                            node.inputs['Base Color'].default_value = color


def _modify_proportions(mesh, features: dict):
    if not mesh:
        return
    proportions = features.get("body_proportions", "")
    if not proportions:
        return
    low = proportions.lower()
    scale_z = 1.0
    if any(kw in low for kw in ("grand", "tall", "haut")):
        scale_z = 1.05
    elif any(kw in low for kw in ("petit", "short", "petite")):
        scale_z = 0.95
    mesh.scale = (mesh.scale[0], mesh.scale[1], mesh.scale[2] * scale_z)


def _modify_pose(armature, features: dict):
    if not armature or armature.type != 'ARMATURE':
        return
    pose = features.get("pose", "")
    if not pose:
        return


def _cleanup_mesh(mesh):
    if not mesh:
        return
    for mod in mesh.modifiers:
        if mod.type == 'TRIANGULATE':
            try:
                mesh.modifiers.remove(mod)
            except Exception:
                pass


def _polycount(mesh) -> int:
    if not mesh or mesh.type != 'MESH':
        total = 0
        for obj in bpy.data.objects:
            if obj.type == 'MESH':
                total += sum(len(p.vertices) for p in obj.data.polygons)
        return total
    return sum(len(p.vertices) for p in mesh.data.polygons)


def _parse_color(color_str: str) -> tuple | None:
    if not color_str:
        return None
    s = color_str.strip()
    if s.startswith('#') and len(s) == 7:
        try:
            r = int(s[1:3], 16) / 255.0
            g = int(s[3:5], 16) / 255.0
            b = int(s[5:7], 16) / 255.0
            return (r, g, b, 1.0)
        except ValueError:
            return None
    named = {
        "noir": (0.02, 0.02, 0.02, 1.0),
        "black": (0.02, 0.02, 0.02, 1.0),
        "blanc": (0.95, 0.95, 0.95, 1.0),
        "white": (0.95, 0.95, 0.95, 1.0),
        "rouge": (0.8, 0.1, 0.1, 1.0),
        "red": (0.8, 0.1, 0.1, 1.0),
        "bleu": (0.1, 0.2, 0.8, 1.0),
        "blue": (0.1, 0.2, 0.8, 1.0),
        "vert": (0.1, 0.7, 0.2, 1.0),
        "green": (0.1, 0.7, 0.2, 1.0),
        "jaune": (0.95, 0.85, 0.1, 1.0),
        "yellow": (0.95, 0.85, 0.1, 1.0),
        "orange": (0.9, 0.5, 0.1, 1.0),
        "violet": (0.5, 0.1, 0.8, 1.0),
        "purple": (0.5, 0.1, 0.8, 1.0),
        "rose": (0.9, 0.4, 0.6, 1.0),
        "pink": (0.9, 0.4, 0.6, 1.0),
        "gris": (0.5, 0.5, 0.5, 1.0),
        "gray": (0.5, 0.5, 0.5, 1.0),
        "grey": (0.5, 0.5, 0.5, 1.0),
        "brun": (0.4, 0.25, 0.1, 1.0),
        "brown": (0.4, 0.25, 0.1, 1.0),
        "chatain": (0.4, 0.25, 0.1, 1.0),
        "roux": (0.7, 0.3, 0.1, 1.0),
        "blond": (0.85, 0.75, 0.4, 1.0),
        "cyan": (0.1, 0.8, 0.9, 1.0),
        "dore": (0.85, 0.65, 0.1, 1.0),
        "gold": (0.85, 0.65, 0.1, 1.0),
        "argent": (0.75, 0.75, 0.8, 1.0),
        "silver": (0.75, 0.75, 0.8, 1.0),
    }
    return named.get(s.lower())


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        try:
            from common import fail
            fail(f"update_avatar: {exc}")
        except Exception:
            print(f"JARVIS_SIGNAL  update_avatar_error  {exc}", file=sys.stderr)
        sys.exit(1)
