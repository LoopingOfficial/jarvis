"""Rigging : armature humanoide reelle, skin weights, IK bras/jambes.

Convention Blender : Z = haut, le personnage regarde vers -Y. Les noms de
bones suivent la convention glTF/Three.js (mixamo-like, suffixes _L / _R) pour
qu'un export GLB reste exploitable dans un viewer web.
"""
from __future__ import annotations

import math

import bpy
from mathutils import Vector

# Proportions d'un humanoide de 1.78 m (metres).
Z_ANKLE, Z_KNEE, Z_HIP = 0.095, 0.475, 0.935
Z_SPINE01, Z_SPINE02, Z_CHEST = 1.010, 1.115, 1.235
Z_NECK, Z_HEAD, Z_HEAD_TOP = 1.430, 1.520, 1.775
Z_SHOULDER, Z_ELBOW, Z_WRIST = 1.395, 1.115, 0.855
X_LEG, X_SHOULDER, X_ARM, X_ELBOW, X_WRIST = 0.098, 0.052, 0.185, 0.212, 0.228


def humanoid_bones(scale: float = 1.0) -> list:
    """(nom, tete, queue, parent) d'un squelette humain complet."""
    s = float(scale)

    def v(x, y, z):
        return (x * s, y * s, z * s)

    bones = [
        ("root", v(0, 0, 0), v(0, 0, 0.12), None),
        ("pelvis", v(0, 0, Z_HIP), v(0, 0, Z_SPINE01), "root"),
        ("spine_01", v(0, 0, Z_SPINE01), v(0, 0, Z_SPINE02), "pelvis"),
        ("spine_02", v(0, 0, Z_SPINE02), v(0, 0, Z_CHEST), "spine_01"),
        ("chest", v(0, 0, Z_CHEST), v(0, 0, Z_NECK), "spine_02"),
        ("neck", v(0, 0, Z_NECK), v(0, -0.012, Z_HEAD), "chest"),
        ("head", v(0, -0.012, Z_HEAD), v(0, -0.012, Z_HEAD_TOP), "neck"),
    ]
    for side, x in (("L", 1.0), ("R", -1.0)):
        bones += [
            ("clavicle_" + side, v(x * X_SHOULDER, -0.005, Z_CHEST + 0.105),
             v(x * X_ARM, -0.005, Z_SHOULDER), "chest"),
            ("upperArm_" + side, v(x * X_ARM, -0.005, Z_SHOULDER),
             v(x * X_ELBOW, -0.005, Z_ELBOW), "clavicle_" + side),
            ("lowerArm_" + side, v(x * X_ELBOW, -0.005, Z_ELBOW),
             v(x * X_WRIST, -0.005, Z_WRIST), "upperArm_" + side),
            ("hand_" + side, v(x * X_WRIST, -0.005, Z_WRIST),
             v(x * (X_WRIST + 0.008), -0.005, Z_WRIST - 0.082), "lowerArm_" + side),
            ("thigh_" + side, v(x * X_LEG, 0, Z_HIP), v(x * X_LEG, 0.004, Z_KNEE), "pelvis"),
            ("shin_" + side, v(x * X_LEG, 0.004, Z_KNEE), v(x * X_LEG, 0, Z_ANKLE),
             "thigh_" + side),
            ("foot_" + side, v(x * X_LEG, 0, Z_ANKLE), v(x * X_LEG, -0.155, 0.022),
             "shin_" + side),
            ("toe_" + side, v(x * X_LEG, -0.155, 0.022), v(x * X_LEG, -0.215, 0.018),
             "foot_" + side),
        ]
    return bones


def build_armature(name: str = "JARVIS_Rig", scale: float = 1.0,
                   bones=None) -> bpy.types.Object:
    """Cree une VRAIE armature Blender depuis la table de bones."""
    data = bpy.data.armatures.new(name)
    rig = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    created = {}
    for bone_name, head, tail, parent in (bones or humanoid_bones(scale)):
        bone = data.edit_bones.new(bone_name)
        bone.head = Vector(head)
        bone.tail = Vector(tail)
        if parent and parent in created:
            bone.parent = created[parent]
            bone.use_connect = False
        created[bone_name] = bone
    bpy.ops.object.mode_set(mode="OBJECT")
    return rig


def add_ik(rig, chain: str = "arm", side: str = "L", chain_length: int = 2) -> str:
    """Ajoute une vraie contrainte IK + son controleur. Retourne le nom du target."""
    tip = ("lowerArm_" if chain == "arm" else "shin_") + side
    if tip not in rig.pose.bones:
        return ""
    target_name = ("IK_hand_" if chain == "arm" else "IK_foot_") + side
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    data = rig.data
    src = data.edit_bones.get(tip)
    ctrl = data.edit_bones.new(target_name)
    ctrl.head = src.tail.copy()
    ctrl.tail = src.tail + Vector((0.0, 0.0, 0.09))
    ctrl.parent = data.edit_bones.get("root")
    bpy.ops.object.mode_set(mode="POSE")
    pbone = rig.pose.bones[tip]
    con = pbone.constraints.new("IK")
    con.target = rig
    con.subtarget = target_name
    con.chain_count = max(1, int(chain_length))
    bpy.ops.object.mode_set(mode="OBJECT")
    return target_name


def deform_bones(rig) -> list:
    """Bones qui deforment reellement le maillage (hors controleurs IK et root)."""
    out = []
    for bone in rig.data.bones:
        if bone.name.startswith("IK_") or bone.name == "root":
            continue
        out.append(bone)
    return out


def mark_deform_bones(rig) -> None:
    """Les controleurs IK ne doivent pas creer de groupes de sommets."""
    for bone in rig.data.bones:
        bone.use_deform = not (bone.name.startswith("IK_") or bone.name == "root")


def has_real_weights(mesh, minimum: float = 0.0001) -> bool:
    """Verifie que le skinning porte de VRAIS poids, pas des groupes vides."""
    if mesh is None or mesh.type != "MESH" or not mesh.vertex_groups:
        return False
    for vertex in mesh.data.vertices:
        for group in vertex.groups:
            if group.weight > minimum:
                return True
    return False


def _distance_to_bone(point, head, tail) -> float:
    """Distance d'un point au SEGMENT de l'os (et non a son centre)."""
    axis = tail - head
    length_sq = axis.length_squared
    if length_sq < 1e-9:
        return (point - head).length
    t = max(0.0, min(1.0, (point - head).dot(axis) / length_sq))
    return (point - (head + axis * t)).length


def assign_weights_by_proximity(mesh, rig, influences: int = 2,
                                falloff: float = 2.0) -> int:
    """Skinning deterministe par proximite aux os.

    Le heat weighting de Blender echoue sur un maillage compose d'ilots
    disjoints (bras, jambes, torse separes) : il cree alors des groupes VIDES
    et le GLB sort sans skin. Cette methode pondere chaque sommet par la
    distance inverse aux os les plus proches ; elle fonctionne toujours.
    Retourne le nombre de sommets reellement pondere.
    """
    if mesh is None or rig is None or mesh.type != "MESH":
        return 0
    bones = deform_bones(rig)
    if not bones:
        return 0
    rig_matrix = rig.matrix_world
    segments = [(b.name, rig_matrix @ b.head_local, rig_matrix @ b.tail_local)
                for b in bones]

    for name, _, _ in segments:
        if name not in mesh.vertex_groups:
            mesh.vertex_groups.new(name=name)
    for group in mesh.vertex_groups:
        group.remove(range(len(mesh.data.vertices)))

    mesh_matrix = mesh.matrix_world
    count = max(1, int(influences))
    weighted = 0
    for index, vertex in enumerate(mesh.data.vertices):
        point = mesh_matrix @ vertex.co
        distances = [(name, _distance_to_bone(point, head, tail))
                     for name, head, tail in segments]
        distances.sort(key=lambda item: item[1])
        nearest = distances[:count]
        raw = [(name, 1.0 / max(1e-4, distance) ** float(falloff))
               for name, distance in nearest]
        total = sum(value for _, value in raw)
        if total <= 0:
            continue
        for name, value in raw:
            mesh.vertex_groups[name].add([index], value / total, "REPLACE")
        weighted += 1
    return weighted


def parent_with_weights(mesh, rig) -> bool:
    """Skinning reel. Poids automatiques si possible, proximite sinon.

    Ne renvoie True que si des poids NON NULS existent : des groupes de
    sommets vides produiraient un GLB sans skin, donc un personnage qui ne
    se deforme pas dans le viewer.
    """
    if mesh is None or rig is None or mesh.type != "MESH":
        return False
    mark_deform_bones(rig)
    try:
        bpy.ops.object.select_all(action="DESELECT")
        mesh.select_set(True)
        rig.select_set(True)
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.parent_set(type="ARMATURE_AUTO")
    except Exception:
        pass
    if not any(m.type == "ARMATURE" for m in mesh.modifiers):
        mesh.parent = rig
        mesh.matrix_parent_inverse = rig.matrix_world.inverted()
        modifier = mesh.modifiers.new("Armature", "ARMATURE")
        modifier.object = rig
    if has_real_weights(mesh):
        return True
    # Repli deterministe : le heat weighting a echoue (ilots disjoints).
    return assign_weights_by_proximity(mesh, rig) > 0


def find_rig():
    return next((o for o in bpy.data.objects if o.type == "ARMATURE"), None)


def rig_summary(rig=None) -> dict:
    rig = rig or find_rig()
    if rig is None:
        return {"rigged": False, "bones": 0, "bone_names": [], "ik": []}
    names = [b.name for b in rig.data.bones]
    ik = []
    for pbone in rig.pose.bones:
        for con in pbone.constraints:
            if con.type == "IK":
                ik.append({"bone": pbone.name, "target": getattr(con, "subtarget", "")})
    skinned = [o.name for o in bpy.data.objects
               if o.type == "MESH" and any(m.type == "ARMATURE" for m in o.modifiers)]
    weighted = [name for name in skinned if has_real_weights(bpy.data.objects[name])]
    return {"rigged": True, "armature": rig.name, "bones": len(names),
            "bone_names": names, "ik": ik, "skinned_meshes": skinned,
            "weighted_meshes": weighted, "has_weights": bool(weighted)}


def add_shape_key(mesh, name: str, deform=None) -> bool:
    """Morph target reel (visemes, expressions). `deform` : {index: offset}."""
    if mesh is None or mesh.type != "MESH":
        return False
    if not mesh.data.shape_keys:
        mesh.shape_key_add(name="Basis", from_mix=False)
    key = mesh.shape_key_add(name=str(name), from_mix=False)
    for idx, offset in (deform or {}).items():
        try:
            key.data[int(idx)].co += Vector(offset)
        except Exception:
            continue
    return True
