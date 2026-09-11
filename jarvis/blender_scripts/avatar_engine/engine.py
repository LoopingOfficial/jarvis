"""LEGACY_PROTOTYPE: ancien générateur loft/primitive conservé en archive.

Les jobs officiels sont routés vers ``avatar_engine_v2_job.py`` et doivent
échouer si MPFB2 n'est pas disponible. Ce module n'est plus un fallback.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(SCRIPTS))
ASSETS = os.path.join(ROOT, "assets", "blender")
for path in (HERE, SCRIPTS, os.path.join(SCRIPTS, "lib"), ASSETS):
    if path not in sys.path:
        sys.path.insert(0, path)

import bpy  # noqa: E402

LEGACY_PROTOTYPE = True

FORBIDDEN_BODY_OPS = (
    "primitive_cylinder_add", "primitive_cube_add",
    "primitive_uv_sphere_add", "primitive_cone_add",
)

REQUIRED = (
    "JARVIS_Head", "JARVIS_Skin", "JARVIS_Hands", "JARVIS_Hair",
    "JARVIS_Eyes", "JARVIS_Iris", "JARVIS_Pupils", "JARVIS_FaceParts",
    "JARVIS_Mouth", "JARVIS_Brows", "JARVIS_Shirt", "JARVIS_Jacket",
    "JARVIS_Pants", "JARVIS_Belt", "JARVIS_Shoes",
)

BODY_OBJECTS = ("JARVIS_Skin", "JARVIS_Head", "JARVIS_Hands")
FACE_OBJECTS = ("JARVIS_Head", "JARVIS_FaceParts", "JARVIS_Brows", "JARVIS_Mouth")
EYE_OBJECTS = ("JARVIS_Eyes", "JARVIS_Iris", "JARVIS_Pupils")
HAIR_VOLUMES = {
    "front": (0.0, -0.08, 1.72, 0.10),
    "top": (0.0, 0.02, 1.78, 0.12),
    "side_left": (0.07, 0.0, 1.64, 0.08),
    "side_right": (-0.07, 0.0, 1.64, 0.08),
    "back": (0.0, 0.08, 1.64, 0.10),
}


def _obj(name):
    return bpy.data.objects.get(name)


def _hex(color, fallback=(0.35, 0.2, 0.12, 1.0)):
    s = str(color or "").strip()
    if s.startswith("#") and len(s) == 7:
        try:
            return (int(s[1:3], 16) / 255.0, int(s[3:5], 16) / 255.0,
                    int(s[5:7], 16) / 255.0, 1.0)
        except ValueError:
            return fallback
    return fallback


def _set_bsdf(mat, **values):
    if not mat or not mat.use_nodes:
        return
    node = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if node is None:
        return
    for key, value in values.items():
        if key in node.inputs:
            node.inputs[key].default_value = value


def _scale_around(obj, factor, pivot=None):
    if obj is None or not hasattr(factor, "__len__"):
        return
    from mathutils import Vector
    mesh = obj.data
    if pivot is None:
        pivot = Vector(obj.location)
    else:
        pivot = Vector(pivot)
    sx, sy, sz = factor if len(factor) == 3 else (factor[0], factor[0], factor[0])
    for vert in mesh.vertices:
        delta = vert.co - pivot
        vert.co = pivot + Vector((delta.x * sx, delta.y * sy, delta.z * sz))
    mesh.update()


def _shape(obj, name, value):
    if obj is None or not obj.data.shape_keys:
        return False
    block = obj.data.shape_keys.key_blocks.get(name)
    if block is None:
        return False
    block.value = max(0.0, min(1.0, float(value)))
    return True


def generate_base(blend_out):
    """Génère le base mesh loft (body.py / mesh_lib), JAMAIS de primitives corps."""
    import build_avatar
    from pathlib import Path
    glb_tmp = os.path.join(os.path.dirname(blend_out or ".") or ".", "_base_tmp.glb")
    stats = build_avatar.build(Path(glb_tmp), Path(blend_out) if blend_out else None)
    return stats


def load_base(settings):
    blend_in = str(settings.get("blend_in") or "")
    blend_out = str(settings.get("blend_out") or "")
    live = str(settings.get("live_glb") or "")
    if live and os.path.normpath(blend_out) == os.path.normpath(live):
        raise RuntimeError("Refus : écriture sur l'avatar live interdite.")
    if blend_in and os.path.isfile(blend_in):
        bpy.ops.wm.open_mainfile(filepath=blend_in)
        missing = [n for n in REQUIRED if _obj(n) is None]
        total = sum(len(o.data.vertices) for o in bpy.data.objects if o.type == "MESH")
        if missing or total < 4000:
            generate_base(blend_out)
    else:
        generate_base(blend_out)
        if blend_out and os.path.isfile(blend_out):
            bpy.ops.wm.open_mainfile(filepath=blend_out)
    return True


def set_body_proportions(params):
    skin = _obj("JARVIS_Skin")
    head = _obj("JARVIS_Head")
    body_factor = (params.get("shoulder_width", 1.0),
                   params.get("chest_width", 1.0),
                   params.get("leg_length", 1.0))
    if skin is not None:
        _scale_around(skin, body_factor, (0.0, 0.0, 0.9))
    # Les vêtements restent dans le même espace de proportions que le corps.
    # Les modifier ensemble évite les intersections visibles dès qu'une
    # référence demande une silhouette plus fine ou des jambes plus longues.
    for name in ("JARVIS_Shirt", "JARVIS_Jacket", "JARVIS_Pants", "JARVIS_Belt"):
        garment = _obj(name)
        if garment is not None:
            _scale_around(garment, body_factor, (0.0, 0.0, 0.9))
    if head is not None:
        scale = float(params.get("head_scale", 1.0))
        _scale_around(head, (scale, scale, scale), (0.0, 0.0, 1.52))
        for name in FACE_OBJECTS + EYE_OBJECTS + ("JARVIS_Hair",):
            extra = _obj(name)
            if extra is not None and extra is not head:
                _scale_around(extra, (scale, scale, scale), (0.0, 0.0, 1.52))
    hands = _obj("JARVIS_Hands")
    if hands is not None:
        hs = float(params.get("hand_scale", 1.0))
        _scale_around(hands, (hs, hs, hs))
    return True


def set_face_morphs(params):
    head = _obj("JARVIS_Head")
    mapping = {
        "eye_size": ("eyeWide", None),
        "smile_base": ("smile", "mouthSmile"),
        "mouth_width": ("mouthWide", None),
        "jaw_roundness": ("jawOpen", None),
    }
    applied = []
    for key, names in mapping.items():
        value = params.get(key)
        if value is None:
            continue
        amount = max(0.0, min(1.0, abs(float(value) - 1.0) * 2.0 if key != "smile_base" else float(value)))
        for name in names:
            if name and _shape(head, name, amount):
                applied.append(name)
    if head is not None:
        fw = float(params.get("face_width", 1.0))
        fh = float(params.get("face_height", 1.0))
        _scale_around(head, (fw, 1.0, fh), (0.0, 0.0, 1.62))
    eyes_factor = float(params.get("eye_size", 1.0))
    if abs(eyes_factor - 1.0) > 0.01:
        pivot = (0.0, -0.08, 1.63)
        for name in EYE_OBJECTS:
            _scale_around(_obj(name), (eyes_factor, 1.0, eyes_factor), pivot)
    spacing = float(params.get("eye_spacing", 1.0))
    if abs(spacing - 1.0) > 0.01:
        for name in EYE_OBJECTS:
            eye = _obj(name)
            if eye is None:
                continue
            for vertex in eye.data.vertices:
                vertex.co.x *= spacing
    height = float(params.get("eye_height", 1.0))
    depth = float(params.get("eye_depth", 1.0))
    for name in EYE_OBJECTS:
        eye = _obj(name)
        if eye is not None:
            for vertex in eye.data.vertices:
                vertex.co.z = 1.634 + (vertex.co.z - 1.634) * height
                vertex.co.y = -0.078 + (vertex.co.y + 0.078) * depth
    return True


def set_eyes(params):
    color = _hex((params.get("eyes") or {}).get("color") if isinstance(params.get("eyes"), dict)
                 else params.get("eye_color"), (0.38, 0.24, 0.13, 1.0))
    iris = bpy.data.materials.get("Iris")
    _set_bsdf(iris, **{"Base Color": color, "Roughness": 0.14})
    return True


def set_hair(params):
    hair = _obj("JARVIS_Hair")
    if hair is None:
        return False
    hair_cfg = params.get("hair") if isinstance(params.get("hair"), dict) else {}
    volume = float(hair_cfg.get("volume") or params.get("hair_volume") or 1.18)
    _scale_around(hair, (1.0 + (volume - 1.0) * 0.4, 1.0 + (volume - 1.0) * 0.55,
                         1.0 + (volume - 1.0) * 0.5), (0.0, 0.02, 1.62))
    color = _hex(hair_cfg.get("color"), (0.35, 0.19, 0.11, 1.0))
    mat = bpy.data.materials.get("Hair")
    _set_bsdf(mat, **{"Base Color": color, "Roughness": 0.42})
    return True


def set_outfit(params):
    outfit = params.get("outfit") if isinstance(params.get("outfit"), dict) else {}
    colors = {
        "Shirt": (outfit.get("shirt") or {}).get("color", "#f4f4ee"),
        "Jacket": (outfit.get("overshirt") or {}).get("color", "#909eb0"),
        "Pants": (outfit.get("trousers") or outfit.get("pants") or {}).get("color", "#4c5566"),
        "Belt": (outfit.get("belt") or {}).get("color", "#3a2a1c"),
        "Shoes": (outfit.get("shoes") or {}).get("color", "#f5f5f5"),
    }
    material_names = {
        "Shirt": ("cotton_shirt", "Shirt"),
        "Jacket": ("grey_overshirt", "Jacket"),
        "Pants": ("trousers", "Pants"),
        "Belt": ("leather_belt", "Belt"),
        "Shoes": ("white_sneakers", "Shoes"),
    }
    for name, hexcol in colors.items():
        material = next((bpy.data.materials.get(candidate)
                         for candidate in material_names[name] if bpy.data.materials.get(candidate)), None)
        _set_bsdf(material, **{"Base Color": _hex(hexcol)})
    shoes = next((bpy.data.materials.get(candidate)
                  for candidate in material_names["Shoes"] if bpy.data.materials.get(candidate)), None)
    _set_bsdf(shoes, **{"Base Color": _hex("#f5f5f5"), "Roughness": 0.45})
    return True


def set_materials(params):
    skin = _hex((params.get("skin") or {}).get("color") if isinstance(params.get("skin"), dict)
                else "#e0b498", (0.88, 0.71, 0.60, 1.0))
    roughness = 0.58
    if isinstance(params.get("skin"), dict):
        try:
            roughness = float(params["skin"].get("roughness") or 0.58)
        except (TypeError, ValueError):
            roughness = 0.58
    for name in ("Skin", "SkinFace", "SkinBody", "SkinHands"):
        mat = bpy.data.materials.get(name)
        kwargs = {"Base Color": skin, "Roughness": roughness}
        if mat and mat.use_nodes:
            node = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
            if node and "Subsurface Weight" in node.inputs:
                kwargs["Subsurface Weight"] = 0.12
            elif node and "Subsurface" in node.inputs:
                kwargs["Subsurface"] = 0.08
        _set_bsdf(mat, **kwargs)
    set_hair(params)
    set_outfit(params)
    set_eyes(params)
    return True


def ensure_rig(_params=None):
    arm = next((o for o in bpy.data.objects if o.type == "ARMATURE"), None)
    if arm is None:
        return False
    try:
        from rig import BONE_NAMES
        names = {b.name for b in arm.data.bones}
        return all(name in names for name in BONE_NAMES)
    except Exception:
        return len(arm.data.bones) >= 20


def ensure_face_rig(_params=None):
    head = _obj("JARVIS_Head")
    if not (head and head.data.shape_keys):
        return False
    keys = {key.name for key in head.data.shape_keys.key_blocks}
    return len(keys) > 1 and any(name in keys for name in
                                  ("blink_L", "blinkLeft", "smile", "mouth_open", "jawOpen"))


def create_visemes(_params=None):
    head = _obj("JARVIS_Head")
    if head is None:
        return False
    if not head.data.shape_keys:
        head.shape_key_add(name="Basis")
    existing = {k.name for k in head.data.shape_keys.key_blocks}
    # Les visèmes ne sont pas des placeholders : lorsqu'un master ancien ne
    # les possède pas, on reconstruit leurs deltas depuis la bibliothèque
    # analytique stable. Les aliases canoniques gardent le contrat frontend
    # indépendant de l'ancien naming ARKit-like.
    try:
        import shapekeys
        base = head.data.shape_keys.key_blocks.get("Basis")
        for name, fn in shapekeys.ALL_KEYS.items():
            if name in existing or base is None:
                continue
            target = fn([tuple(v.co) for v in base.data])
            block = head.shape_key_add(name=name)
            for index, co in enumerate(target):
                block.data[index].co = co
            existing.add(name)
    except Exception:
        pass
    aliases = {
        "blink_L": "blinkLeft", "blink_R": "blinkRight",
        "brow_up": "browUp", "brow_down": "browDown",
        "brow_inner_up": "browUp", "mouth_open": "jawOpen",
        "mouth_close": "Basis", "smile": "smile", "frown": "frown",
    }
    for name, source in aliases.items():
        if name in existing:
            continue
        source_block = head.data.shape_keys.key_blocks.get(source)
        block = head.shape_key_add(name=name)
        if source_block is not None:
            for index, point in enumerate(source_block.data):
                block.data[index].co = point.co
        existing.add(name)
    return True


def create_animations(_params=None):
    arm = next((o for o in bpy.data.objects if o.type == "ARMATURE"), None)
    if arm is None:
        return False
    required = {"idle", "breathing", "blink", "look_around", "head_nod",
                "head_tilt", "thinking", "talking", "typing", "wave",
                "point", "explain", "walk"}
    available = {action.name for action in bpy.data.actions}
    aliases = {
        "idle": any(name.startswith("idle_") for name in available),
        "breathing": any(name.startswith("idle_") for name in available),
        "blink": any("blink" in name.lower() for name in available),
        "look_around": any("turn" in name.lower() or "look" in name.lower() for name in available),
        "head_nod": any("acknowledge" in name.lower() or "agree" in name.lower() for name in available),
        "head_tilt": any("thinking" in name.lower() for name in available),
        "thinking": any("thinking" in name.lower() for name in available),
        "talking": any("explain" in name.lower() or "talk" in name.lower() for name in available),
        "typing": any("typing" in name.lower() for name in available),
        "wave": any("welcome" in name.lower() or "wave" in name.lower() for name in available),
        "point": any("point" in name.lower() for name in available),
        "explain": any("explain" in name.lower() for name in available),
        "walk": any("walk" in name.lower() for name in available),
    }
    return bool(available) and all(aliases[name] for name in required)


def validate(_params=None, settings=None):
    report = {
        "required_parts": {},
        "vertex_count": 0,
        "primitive_body": False,
        "rigged": False,
        "shape_keys": 0,
        "face_rig": False,
        "visemes": [],
        "outfit_meshes": {},
        "animation_clips": [],
        "mesh_count": 0,
        "object_count": 0,
        "triangle_count": 0,
        "bone_count": 0,
        "ok": True,
        "failures": [],
    }
    report["object_count"] = len(bpy.data.objects)
    report["mesh_count"] = sum(1 for obj in bpy.data.objects if obj.type == "MESH")
    report["triangle_count"] = sum(len(obj.data.polygons) for obj in bpy.data.objects
                                    if obj.type == "MESH")
    for name in REQUIRED:
        obj = _obj(name)
        report["required_parts"][name] = bool(obj)
        if obj is None:
            report["ok"] = False
            report["failures"].append(f"missing:{name}")
        else:
            report["vertex_count"] += len(obj.data.vertices)
    if report["vertex_count"] < 4000:
        report["ok"] = False
        report["failures"].append("low_density")
        report["primitive_body"] = True
    armature = next((obj for obj in bpy.data.objects if obj.type == "ARMATURE"), None)
    report["bone_count"] = len(armature.data.bones) if armature else 0
    report["rigged"] = ensure_rig()
    if not report["rigged"]:
        report["failures"].append("rig_incomplete")
        report["ok"] = False
    head = _obj("JARVIS_Head")
    if head and head.data.shape_keys:
        report["shape_keys"] = len(head.data.shape_keys.key_blocks)
        keys = {key.name for key in head.data.shape_keys.key_blocks}
        report["face_rig"] = any(k in keys for k in ("blink_L", "blinkLeft")) and any(
            k in keys for k in ("smile", "mouth_open", "jawOpen"))
        report["visemes"] = sorted(k for k in keys if k.startswith("viseme_"))
        if not report["face_rig"]:
            report["failures"].append("face_rig_missing")
            report["ok"] = False
        missing_visemes = [f"viseme_{name}" for name in
                          ("REST", "A", "E", "I", "O", "U", "MBP", "FV", "L")
                          if f"viseme_{name}" not in keys]
        if missing_visemes:
            report["failures"].append("visemes_missing:" + ",".join(missing_visemes))
            report["ok"] = False
    else:
        report["failures"].append("face_shape_keys_missing")
        report["ok"] = False
    for key, object_name in (("shirt", "JARVIS_Shirt"), ("overshirt", "JARVIS_Jacket"),
                             ("trousers", "JARVIS_Pants"), ("belt", "JARVIS_Belt"),
                             ("shoes", "JARVIS_Shoes")):
        report["outfit_meshes"][key] = bool(_obj(object_name))
    if not all(report["outfit_meshes"].values()):
        report["failures"].append("outfit_mesh_missing")
        report["ok"] = False
    report["animation_clips"] = sorted(a.name for a in bpy.data.actions)
    milestone = str((settings or {}).get("milestone") or "")
    if milestone in {"animation", "integration"} and not create_animations():
        report["failures"].append("animation_clips_missing")
        report["ok"] = False
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        if obj.name in BODY_OBJECTS and len(obj.data.vertices) < 200:
            report["primitive_body"] = True
            report["ok"] = False
            report["failures"].append(f"primitive_like:{obj.name}")
    return report


def export_glb(settings):
    glb_out = str(settings.get("glb_out") or "")
    blend_out = str(settings.get("blend_out") or "")
    live = os.path.normpath(str(settings.get("live_glb") or ""))
    if live and glb_out and os.path.normpath(glb_out) == live:
        raise RuntimeError("Refus : export vers l'avatar live interdit.")
    if blend_out:
        os.makedirs(os.path.dirname(blend_out), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=blend_out)
    if glb_out:
        os.makedirs(os.path.dirname(glb_out), exist_ok=True)
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.export_scene.gltf(
            filepath=glb_out, export_format="GLB", export_apply=False,
            export_skins=True, export_morph=True, export_animations=True,
            export_yup=True, export_cameras=False, export_lights=False)
    return True


OPS = {
    "load_base": lambda s, p: load_base(s),
    "set_body_proportions": lambda s, p: set_body_proportions(p),
    "set_face_morphs": lambda s, p: set_face_morphs(p),
    "set_eyes": lambda s, p: set_eyes(p),
    "set_hair": lambda s, p: set_hair(p),
    "set_outfit": lambda s, p: set_outfit(p),
    "set_materials": lambda s, p: set_materials(p),
    "ensure_rig": lambda s, p: ensure_rig(p),
    "ensure_face_rig": lambda s, p: ensure_face_rig(p),
    "create_visemes": lambda s, p: create_visemes(p),
    "create_animations": lambda s, p: create_animations(p),
    "validate": lambda s, p: validate(p, s),
    "export_glb": lambda s, p: export_glb(s),
    "modify_face": lambda s, p: set_face_morphs(p),
    "modify_body": lambda s, p: set_body_proportions(p),
    "inspect": lambda s, p: validate(p),
}


def run(settings):
    params = settings.get("parameters") or {}
    operation = str(settings.get("operation") or "load_base")
    chain = settings.get("chain") or [operation]
    if operation == "build" and not settings.get("chain"):
        chain = ["load_base", "set_body_proportions", "set_face_morphs", "set_eyes",
                 "set_hair", "set_outfit", "set_materials", "validate", "export_glb"]
    results = []
    validation = {}
    # Toute opération est exécutée sur une copie du master/base. Même
    # `inspect` doit ouvrir la scène réelle : inspecter une scène vide était
    # la cause de rapports « 0 objet » malgré un avatar présent.
    if "load_base" not in chain:
        load_base(settings)
    for step in chain:
        fn = OPS.get(step)
        if fn is None:
            results.append({"step": step, "ok": False, "error": "unknown"})
            continue
        outcome = fn(settings, params)
        if step == "validate" or step == "inspect":
            validation = outcome if isinstance(outcome, dict) else {}
            results.append({"step": step, "ok": bool(validation.get("ok", True)),
                            "report": validation})
        else:
            results.append({"step": step, "ok": bool(outcome)})
    if "validate" not in chain:
        validation = validate(params, settings)
    if "export_glb" not in chain and settings.get("glb_out"):
        export_glb(settings)
    return {"ok": all(r.get("ok", True) for r in results) and validation.get("ok", True),
            "steps": results, "validation": validation}
