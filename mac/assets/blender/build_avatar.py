"""Construit l'avatar 3D définitif de JARVIS et l'exporte en GLB.

Exécution :
    blender --background --python assets/blender/build_avatar.py -- --out ui/assets/avatar/jarvis.glb

Le script est entièrement déterministe : relancé, il produit exactement le
même modèle. Il crée le maillage, le squelette complet, les poids de skinning,
les blendshapes faciaux, les visèmes, les matériaux et toutes les animations.
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import bpy
import bmesh
from mathutils import Euler, Matrix, Vector

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import animations as anim              # noqa: E402
import body                            # noqa: E402
import shapekeys                       # noqa: E402
from mesh_lib import merge             # noqa: E402
from rig import BONES, FINGER_CHAINS, IK_CHAINS  # noqa: E402


# ---------------------------------------------------------------------------
# Utilitaires scène
# ---------------------------------------------------------------------------
def reset_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.fps = anim.FPS
    scene.unit_settings.system = "METRIC"


def new_mesh(name: str, verts, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([list(v) for v in verts], [], [list(f) for f in faces])
    mesh.validate(verbose=False)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    clean_mesh(obj)
    for poly in obj.data.polygons:
        poly.use_smooth = True
    return obj


def marker(name: str, role: str, parent=None):
    """Crée un repère de scène non rendu pour les contrôleurs haut niveau."""
    obj = bpy.data.objects.new(name, None)
    bpy.context.collection.objects.link(obj)
    obj.empty_display_type = "PLAIN_AXES"
    obj["avatar_role"] = role
    if parent is not None:
        obj.parent = parent
    return obj


def clean_mesh(obj, merge_distance: float = 0.0004) -> None:
    """Supprime les faces dégénérées produites par le sculpt analytique.

    Deux sommets voisins qui se croisent donnent une face d'aire nulle : sa
    normale est indéfinie et elle scintille sous le contre-jour. On la retire,
    sans jamais souder la topologie (nez, paupières et oreilles sont des
    volumes distincts qui doivent le rester).
    """
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    # PAS de remove_doubles global : il souderait le nez, les paupières et les
    # oreilles au crâne. On se limite aux faces réellement dégénérées, dont la
    # normale est indéfinie et qui scintillent au rendu.
    degenerate = [f for f in bm.faces if f.calc_area() < 1e-10]
    if degenerate:
        bmesh.ops.delete(bm, geom=degenerate, context="FACES")
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def smooth(obj, levels: int = 1):
    """Subdivision appliquée AVANT les shape keys et le skinning.

    Les blendshapes étant calculés analytiquement (par position, pas par
    index), ils restent valides sur le maillage subdivisé.
    """
    if levels <= 0:
        return obj
    bpy.context.view_layer.objects.active = obj
    modifier = obj.modifiers.new("Subdivision", "SUBSURF")
    modifier.levels = levels
    modifier.render_levels = levels
    modifier.use_limit_surface = False
    bpy.ops.object.modifier_apply(modifier="Subdivision")
    clean_mesh(obj, merge_distance=0.0002)
    for poly in obj.data.polygons:
        poly.use_smooth = True
    return obj


def material(name: str, color, *, roughness=0.72, metallic=0.0, emission=None,
             emission_strength=0.0, specular=0.5):
    mat = bpy.data.materials.new(name)
    if not mat.node_tree:
        mat.use_nodes = True
    # Le nom du nœud est localisé/variable selon la version : on cible le type.
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = mat.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
        output = next((n for n in mat.node_tree.nodes if n.type == "OUTPUT_MATERIAL"), None)
        if output is not None:
            mat.node_tree.links.new(bsdf.outputs[0], output.inputs[0])
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    for key in ("Specular IOR Level", "Specular"):
        if key in bsdf.inputs:
            bsdf.inputs[key].default_value = specular
            break
    if emission is not None:
        for key in ("Emission Color", "Emission"):
            if key in bsdf.inputs:
                bsdf.inputs[key].default_value = (*emission, 1.0)
                break
        if "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Strength"].default_value = emission_strength
    return mat


# ---------------------------------------------------------------------------
# Squelette
# ---------------------------------------------------------------------------
def build_armature():
    arm_data = bpy.data.armatures.new("JARVIS_Armature")
    arm_obj = bpy.data.objects.new("JARVIS_Armature", arm_data)
    bpy.context.collection.objects.link(arm_obj)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")

    created = {}
    for name, head, tail, parent, roll in BONES:
        bone = arm_data.edit_bones.new(name)
        bone.head = Vector(head)
        bone.tail = Vector(tail)
        bone.roll = roll
        if parent:
            bone.parent = created[parent]
            bone.use_connect = False
        created[name] = bone

    bpy.ops.object.mode_set(mode="OBJECT")
    for pb in arm_obj.pose.bones:
        pb.rotation_mode = "QUATERNION"
    return arm_obj


# ---------------------------------------------------------------------------
# Skinning analytique
# ---------------------------------------------------------------------------
def bone_segments(arm_obj):
    segs = {}
    for bone in arm_obj.data.bones:
        segs[bone.name] = (Vector(bone.head_local), Vector(bone.tail_local))
    return segs


def _distance_to_segment(p: Vector, a: Vector, b: Vector) -> float:
    ab = b - a
    length2 = ab.length_squared
    if length2 < 1e-9:
        return (p - a).length
    t = max(0.0, min(1.0, (p - a).dot(ab) / length2))
    return (p - (a + ab * t)).length


def skin(obj, arm_obj, candidates: list[str], *, power: float = 4.0,
         influences: int = 3) -> None:
    """Assigne des poids lisses aux `candidates` les plus proches de chaque sommet."""
    segs = bone_segments(arm_obj)
    groups = {name: obj.vertex_groups.new(name=name) for name in candidates}
    for v in obj.data.vertices:
        p = Vector(v.co)
        distances = sorted(
            ((_distance_to_segment(p, *segs[name]), name) for name in candidates),
            key=lambda x: x[0])[:influences]
        weights = []
        for d, name in distances:
            weights.append((name, 1.0 / max(d, 1e-4) ** power))
        total = sum(w for _, w in weights) or 1.0
        for name, w in weights:
            value = w / total
            if value > 0.001:
                groups[name].add([v.index], value, "REPLACE")

    modifier = obj.modifiers.new("Armature", "ARMATURE")
    modifier.object = arm_obj
    obj.parent = arm_obj


def skin_by_side(obj, arm_obj, left_bone: str, right_bone: str) -> None:
    """Skinning strictement latéral : chaque sommet suit l'os de son côté."""
    groups = {name: obj.vertex_groups.new(name=name) for name in (left_bone, right_bone)}
    for v in obj.data.vertices:
        target = left_bone if v.co.x >= 0 else right_bone
        groups[target].add([v.index], 1.0, "REPLACE")
    modifier = obj.modifiers.new("Armature", "ARMATURE")
    modifier.object = arm_obj
    obj.parent = arm_obj


# Jeux d'os candidats par pièce : évite qu'un doigt soit tiré par le torse.
SPINE_CHAIN = ["pelvis", "spine_01", "spine_02", "chest", "neck", "head"]
ARM_CHAIN = [f"{b}_{s}" for s in ("L", "R")
             for b in ("clavicle", "upperArm", "lowerArm", "hand")]
LEG_CHAIN = [f"{b}_{s}" for s in ("L", "R")
             for b in ("upperLeg", "lowerLeg", "foot", "toe")]
FINGER_BONES = [b for chain in FINGER_CHAINS.values() for b in chain]


# ---------------------------------------------------------------------------
# Blendshapes
# ---------------------------------------------------------------------------
def add_shape_keys(obj) -> list[str]:
    base_verts = [tuple(v.co) for v in obj.data.vertices]
    obj.shape_key_add(name="Basis", from_mix=False)
    names = []
    for key_name, fn in shapekeys.ALL_KEYS.items():
        target = fn(base_verts)
        block = obj.shape_key_add(name=key_name, from_mix=False)
        for i, co in enumerate(target):
            block.data[i].co = Vector(co)
        block.slider_min = 0.0
        block.slider_max = 1.0
        names.append(key_name)
    return names


# ---------------------------------------------------------------------------
# Animations
# ---------------------------------------------------------------------------
def _world_rotation_to_local(pbone, degrees: tuple[float, float, float]):
    """Convertit une rotation lisible (pitch, tilt, yaw) en quaternion local d'os.

    Les poses sont écrites dans le repère du monde, bien plus intuitif que le
    repère d'os (dont l'axe Y suit l'os). La conversion garde l'écriture des
    animations lisible sans sacrifier la justesse du résultat.
    """
    rot = Euler([math.radians(d) for d in degrees], "XYZ").to_matrix()
    rest = pbone.bone.matrix_local.to_3x3()
    local = rest.inverted() @ rot @ rest
    return local.to_quaternion()


def build_action(arm_obj, spec: dict):
    name = spec["name"]
    action = bpy.data.actions.new(name)
    action.use_fake_user = True
    arm_obj.animation_data_create()
    arm_obj.animation_data.action = action

    bones_used = set()
    for pose in spec["keys"].values():
        bones_used.update(b for b in pose if not b.startswith("__"))

    for frame, pose in sorted(spec["keys"].items()):
        for bone_name in bones_used:
            pbone = arm_obj.pose.bones.get(bone_name)
            if pbone is None:
                continue
            degrees = pose.get(bone_name, (0.0, 0.0, 0.0))
            pbone.rotation_quaternion = _world_rotation_to_local(pbone, degrees)
            pbone.keyframe_insert("rotation_quaternion", frame=frame)
        loc = pose.get("__loc_root")
        root = arm_obj.pose.bones.get("root")
        if root is not None:
            root.location = Vector(loc) if loc else Vector((0.0, 0.0, 0.0))
            root.keyframe_insert("location", frame=frame)

    for fcurve in _action_fcurves(action):
        for kp in fcurve.keyframe_points:
            kp.interpolation = "BEZIER"
            kp.handle_left_type = "AUTO_CLAMPED"
            kp.handle_right_type = "AUTO_CLAMPED"
        fcurve.update()
    return action


def _action_fcurves(action):
    """Blender ≤ 4.3 expose action.fcurves ; 4.4+ passe par layers/slots."""
    if hasattr(action, "fcurves"):
        return list(action.fcurves)
    out = []
    for layer in getattr(action, "layers", []):
        for strip in getattr(layer, "strips", []):
            for bag in getattr(strip, "channelbags", []):
                out.extend(bag.fcurves)
    return out


def reset_pose(arm_obj) -> None:
    for pb in arm_obj.pose.bones:
        pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        pb.location = (0.0, 0.0, 0.0)
        pb.scale = (1.0, 1.0, 1.0)


# ---------------------------------------------------------------------------
# Construction complète
# ---------------------------------------------------------------------------
def build(out_path: Path, blend_path: Path | None = None) -> dict:
    reset_scene()
    arm_obj = build_armature()

    # ---- pièces ----------------------------------------------------------
    head_obj = new_mesh("JARVIS_Head", *body.head_mesh())
    face_obj = new_mesh("JARVIS_FaceParts", *body.face_parts_mesh())
    mouth_obj = new_mesh("JARVIS_Mouth", *body.mouth_cavity_mesh())
    hair_obj = new_mesh("JARVIS_Hair", *body.hair_mesh())
    brows_obj = new_mesh("JARVIS_Brows", *merge(body.brow(1.0), body.brow(-1.0)))

    eye_parts_l = body.eyeball(1.0)
    eye_parts_r = body.eyeball(-1.0)
    eyes_obj = new_mesh("JARVIS_Eyes", *merge(eye_parts_l[0], eye_parts_r[0]))
    iris_obj = new_mesh("JARVIS_Iris", *merge(eye_parts_l[1], eye_parts_r[1]))
    pupil_obj = new_mesh("JARVIS_Pupils", *merge(eye_parts_l[2], eye_parts_r[2]))
    # Contrat de scène explicite : les contrôleurs peuvent viser Eye.L/Eye.R
    # sans deviner la moitié gauche depuis un mesh fusionné.
    eye_l = marker("Eye.L", "eye_controller")
    eye_r = marker("Eye.R", "eye_controller")
    eye_l.location = (body.EYE_X, body.EYE_Y, body.FACE_Z_EYE)
    eye_r.location = (-body.EYE_X, body.EYE_Y, body.FACE_Z_EYE)
    for eye_marker in (eye_l, eye_r):
        eye_marker["sclera"] = "JARVIS_Eyes"
        eye_marker["iris"] = "JARVIS_Iris"
        eye_marker["pupil"] = "JARVIS_Pupils"

    skin_obj = new_mesh("JARVIS_Skin", *merge(
        body.neck_mesh(), body.torso_skin_mesh(),
        body.arm_mesh(1.0), body.arm_mesh(-1.0),
        body.leg_skin_mesh(1.0), body.leg_skin_mesh(-1.0)))
    hands_obj = new_mesh("JARVIS_Hands", *merge(body.hand_mesh(1.0), body.hand_mesh(-1.0)))
    shirt_obj = new_mesh("JARVIS_Shirt", *body.shirt_mesh())
    jacket_obj = new_mesh("JARVIS_Jacket", *body.jacket_mesh())
    belt_obj = new_mesh("JARVIS_Belt", *body.belt_mesh())
    pants_obj = new_mesh("JARVIS_Pants", *body.pants_mesh())
    shoes_obj = new_mesh("JARVIS_Shoes", *body.shoes_mesh())
    accent_obj = new_mesh("JARVIS_Accent", *body.accent_mesh())

    # Lissage : la silhouette doit être organique, pas facettée.
    # La tête est déjà dense : la subdiviser la rétrécirait et effacerait
    # les orbites, la fente des lèvres et le relief des arcades.
    for obj, levels in ((hair_obj, 1), (skin_obj, 1), (hands_obj, 1),
                        (shirt_obj, 1), (jacket_obj, 1), (pants_obj, 1), (shoes_obj, 1),
                        (eyes_obj, 1), (iris_obj, 1)):
        smooth(obj, levels)

    # ---- matériaux -------------------------------------------------------
    SKIN = (0.878, 0.706, 0.596)
    mats = {
        head_obj: material("Skin", SKIN, roughness=0.62, specular=0.42),
        face_obj: material("SkinFace", SKIN, roughness=0.62, specular=0.42),
        mouth_obj: material("Mouth", (0.118, 0.055, 0.062), roughness=0.55, specular=0.35),
        skin_obj: material("SkinBody", SKIN, roughness=0.64, specular=0.40),
        hands_obj: material("SkinHands", SKIN, roughness=0.60, specular=0.42),
        hair_obj: material("Hair", (0.239, 0.157, 0.098), roughness=0.42, specular=0.55),
        brows_obj: material("Brows", (0.196, 0.125, 0.078), roughness=0.70),
        eyes_obj: material("Sclera", (0.945, 0.941, 0.937), roughness=0.18, specular=0.85),
        iris_obj: material("Iris", (0.376, 0.243, 0.129), roughness=0.14, specular=0.95),
        pupil_obj: material("Pupil", (0.020, 0.016, 0.014), roughness=0.10, specular=1.0),
        shirt_obj: material("cotton_shirt", (0.93, 0.93, 0.89), roughness=0.80),
        jacket_obj: material("grey_overshirt", (0.52, 0.57, 0.64), roughness=0.86),
        belt_obj: material("leather_belt", (0.259, 0.176, 0.118), roughness=0.60, specular=0.40),
        pants_obj: material("trousers", (0.235, 0.259, 0.310), roughness=0.88),
        shoes_obj: material("white_sneakers", (0.93, 0.93, 0.92), roughness=0.45, specular=0.50),
        accent_obj: material("Accent", (0.133, 0.827, 0.933), roughness=0.35,
                             emission=(0.133, 0.827, 0.933), emission_strength=0.55),
    }
    for obj, mat in mats.items():
        obj.data.materials.append(mat)

    hair_root = marker("Hair", "hair_system")
    for volume_name in ("front", "top", "side_left", "side_right", "back"):
        volume = marker(f"Hair.{volume_name}", "hair_volume", hair_root)
        volume["mesh"] = "JARVIS_Hair"
        volume["preset"] = "side_swept_voluminous"
    for garment, preset in ((shirt_obj, "cotton_shirt"), (jacket_obj, "grey_overshirt"),
                            (pants_obj, "trousers"), (belt_obj, "leather_belt"),
                            (shoes_obj, "white_sneakers")):
        garment["garment_preset"] = preset

    # ---- skinning --------------------------------------------------------
    head_only = ["head", "neck"]
    skin(head_obj, arm_obj, head_only, power=6.0, influences=2)
    skin(face_obj, arm_obj, ["head"], influences=1)
    skin(mouth_obj, arm_obj, ["head"], influences=1)
    skin(hair_obj, arm_obj, ["head"], influences=1)
    skin(brows_obj, arm_obj, ["head"], influences=1)
    # Chaque globe suit SON os : c'est ce qui autorise saccades et convergence.
    for obj in (eyes_obj, iris_obj, pupil_obj):
        skin_by_side(obj, arm_obj, "eye_L", "eye_R")

    skin(skin_obj, arm_obj, SPINE_CHAIN + ARM_CHAIN + LEG_CHAIN, power=4.0, influences=3)
    skin(hands_obj, arm_obj,
         ["hand_L", "hand_R", "lowerArm_L", "lowerArm_R"] + FINGER_BONES,
         power=6.0, influences=2)
    skin(shirt_obj, arm_obj, SPINE_CHAIN, power=3.5, influences=3)
    skin(jacket_obj, arm_obj, SPINE_CHAIN + ARM_CHAIN, power=3.5, influences=3)
    skin(belt_obj, arm_obj, ["pelvis", "spine_01"], influences=2)
    skin(pants_obj, arm_obj, ["pelvis", "spine_01"] + LEG_CHAIN, power=4.0, influences=3)
    skin(shoes_obj, arm_obj, ["foot_L", "foot_R", "toe_L", "toe_R", "lowerLeg_L",
                              "lowerLeg_R"], power=6.0, influences=2)
    skin(accent_obj, arm_obj, ["chest", "neck"], influences=2)

    # ---- blendshapes (sur la tête uniquement) ----------------------------
    morph_names = add_shape_keys(head_obj)

    # ---- animations ------------------------------------------------------
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="POSE")
    clips = []
    for spec in anim.ALL_ACTIONS:
        reset_pose(arm_obj)
        build_action(arm_obj, spec)
        clips.append({"name": spec["name"], "kind": spec["kind"],
                      "loop": spec["loop"], "frames": spec["frames"]})
    reset_pose(arm_obj)
    arm_obj.animation_data.action = None
    bpy.ops.object.mode_set(mode="OBJECT")

    # ---- métadonnées exportées dans les extras glTF ----------------------
    meta = {
        **anim.METADATA,
        "clips": clips,
        "morphTargets": morph_names,
        "visemes": [k for k in morph_names if k.startswith("viseme_")],
        "expressions": [k for k in morph_names if not k.startswith("viseme_")],
        "fingerChains": FINGER_CHAINS,
        "ikChains": IK_CHAINS,
        "eyeBones": ["eye_L", "eye_R"],
        "height": 1.78,
    }
    arm_obj["jarvis"] = json.dumps(meta)

    # ---- export ----------------------------------------------------------
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.gltf(
        filepath=str(out_path),
        export_format="GLB",
        export_apply=False,
        export_skins=True,
        export_morph=True,
        export_morph_normal=False,
        export_animations=True,
        export_animation_mode="ACTIONS",
        export_nla_strips=False,
        export_bake_animation=False,
        export_optimize_animation_size=False,
        export_extras=True,
        export_yup=True,
        export_cameras=False,
        export_lights=False,
        export_texcoords=False,
        export_normals=True,
        export_tangents=False,
    )

    if blend_path is not None:
        blend_path.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))

    stats = {
        "output": str(out_path),
        "size_kb": round(out_path.stat().st_size / 1024, 1),
        "objects": len(mats),
        "bones": len(arm_obj.data.bones),
        "clips": len(clips),
        "morphTargets": len(morph_names),
        "triangles": sum(len(o.data.polygons) for o in mats),
    }
    return stats


def main() -> None:
    argv = sys.argv
    args = argv[argv.index("--") + 1:] if "--" in argv else []
    out = Path("ui/assets/avatar/jarvis.glb")
    blend = None
    for i, a in enumerate(args):
        if a == "--out" and i + 1 < len(args):
            out = Path(args[i + 1])
        if a == "--blend" and i + 1 < len(args):
            blend = Path(args[i + 1])
    stats = build(out.resolve(), blend.resolve() if blend else None)
    print("JARVIS_AVATAR_STATS " + json.dumps(stats), flush=True)


if __name__ == "__main__":
    main()
