"""LEGACY — NE PAS UTILISER POUR L'AVATAR FINAL.

Ce script construit un mannequin à partir de primitives Blender
(`primitive_cylinder_add`, etc.). AvatarEngine s'appuie sur le base mesh loft
(`assets/blender/build_avatar.py` + `body.py`) et n'importe JAMAIS ce fichier.

Le mannequin live actuel n'est pas écrasé tant qu'un candidat n'est pas accepté.
"""
LEGACY_PRIMITIVE_AVATAR = True

import bpy
import math
from mathutils import Vector

# ---------------------------------------------------------------------------
# Paramètres de proportions (d'après assets/blender/rig.py)
# ---------------------------------------------------------------------------
H = 1.78
SHOULDER_W = 0.42
Z_ANKLE, Z_KNEE, Z_HIP = 0.095, 0.475, 0.935
Z_SPINE01, Z_SPINE02, Z_CHEST = 1.010, 1.115, 1.235
Z_NECK, Z_HEAD_BASE, Z_TOP = 1.430, 1.520, 1.775
Z_SHOULDER, Z_ELBOW, Z_WRIST = 1.395, 1.115, 0.855
X_LEG, X_ARM, X_ELBOW, X_WRIST = 0.098, 0.185, 0.212, 0.228
REST_OFFSET = 0.005  # léger écart latéral des mains/bras

OUT = r"C:\Users\jerom\Desktop\jarvis-windows\jarvis-windows\ui\assets\avatar\jarvis_avatar.glb"

# ---------------------------------------------------------------------------
# Utilitaires Blender
# ---------------------------------------------------------------------------
def mesh_new(name, verts=None, faces=None, mat=None):
    me = bpy.data.meshes.new(name)
    if verts is not None:
        me.from_pydata(list(verts), [], list(faces))
        me.update()
    ob = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(ob)
    if mat is not None:
        me.materials.append(mat)
    return ob

def new_primitive(op, name, mat, location=(0, 0, 0), scale=(1, 1, 1),
                  rotation=(0, 0, 0), apply=True):
    """Ajoute une primitive, la met à l'échelle / position et fige la géométrie."""
    getattr(bpy.ops.mesh, op)()
    ob = bpy.context.active_object
    ob.name = name
    ob.location = location
    ob.scale = scale
    ob.rotation_euler = rotation
    if apply:
        bpy.context.view_layer.objects.active = ob
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    if mat is not None:
        if ob.data.materials:
            ob.data.materials[0] = mat
        else:
            ob.data.materials.append(mat)
    return ob

def replate_mesh(ob, verts, faces):
    """Remplace la géométrie d'un objet par un maillage neuf (from_pydata sûr)."""
    me = bpy.data.meshes.new(ob.name + '_mesh')
    me.from_pydata(list(verts), [], list(faces))
    me.update()
    mat = ob.active_material
    if mat is not None:
        me.materials.append(mat)
    ob.data = me
    return ob

def smooth(ob, auto_angle=None):
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.select_all(action='DESELECT')
    ob.select_set(True)
    bpy.ops.object.shade_smooth()
    if auto_angle is not None:
        ob.data.use_auto_smooth = True
        ob.data.auto_smooth_angle = math.radians(auto_angle)

def subsurf(ob, levels=1, apply=True):
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.select_all(action='DESELECT')
    ob.select_set(True)
    bpy.ops.object.modifier_add(type='SUBSURF')
    ob.modifiers[-1].levels = levels
    ob.modifiers[-1].render_levels = levels
    if apply:
        bpy.ops.object.modifier_apply(modifier=ob.modifiers[-1].name)

def skin_to(ob, arm):
    """Attache un mesh à l'armature avec des poids automatiques."""
    bpy.ops.object.select_all(action='DESELECT')
    ob.select_set(True)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.parent_set(type='ARMATURE_AUTO')
    return ob

def parent_to_bone(ob, arm, bone):
    ob.parent = arm
    ob.parent_type = 'BONE'
    ob.parent_bone = bone

def mat(name, color, roughness=0.8, metalness=0.0, emission=0.0, transmission=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    nt.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
    bsdf.inputs['Base Color'].default_value = (*color, 1.0)
    bsdf.inputs['Roughness'].default_value = roughness
    bsdf.inputs['Metallic'].default_value = metalness
    if emission:
        bsdf.inputs['Emission'].default_value = (*color, 1.0)
        bsdf.inputs['Emission Strength'].default_value = emission
    if transmission:
        bsdf.inputs['Transmission'].default_value = transmission
        bsdf.inputs['IOR'].default_value = 1.45
    return m

def loft(name, zs, radii, sxz=1.0, syz=1.0, segments=24, matn='Skin',
         close_bottom=False, close_top=False):
    """Lisse les silhouettes en tubes non-capsules : anneaux empilés."""
    ob = mesh_new(name, mat=bpy.data.materials[matn])
    verts, faces = [], []
    n = len(zs)
    for i, z in enumerate(zs):
        r = radii[i]
        for j in range(segments):
            a = j / segments * math.tau
            x = math.cos(a) * r * sxz
            y = math.sin(a) * r * syz
            verts.append((x, y, z))
    for i in range(n - 1):
        base = i * segments
        for j in range(segments):
            j2 = (j + 1) % segments
            faces.append((base + j, base + j2, base + segments + j2, base + segments + j))
    if close_top:
        c = len(verts)
        top = (n - 1) * segments
        verts.append((0, 0, zs[-1]))
        for j in range(segments):
            faces.append((c, top + j, top + ((j + 1) % segments)))
    if close_bottom:
        c = len(verts)
        verts.append((0, 0, zs[0]))
        for j in range(segments):
            faces.append((c, (j + 1) % segments, j))
    ob.data.from_pydata(verts, [], faces)
    ob.data.update()
    smooth(ob)
    return ob

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
SKIN = mat('Skin', (0.96, 0.76, 0.66), roughness=0.55)
LIP = mat('Lip', (0.62, 0.34, 0.28), roughness=0.45)
HAIR = mat('Hair', (0.40, 0.24, 0.16), roughness=0.75)
SHIRT = mat('Shirt', (0.96, 0.95, 0.90), roughness=0.92)
JACKET = mat('Jacket', (0.44, 0.49, 0.61), roughness=0.80, metalness=0.04)
JACKET_DARK = mat('JacketDark', (0.36, 0.41, 0.52), roughness=0.82)
TROUS = mat('Trousers', (0.15, 0.19, 0.27), roughness=0.85)
SHOE = mat('Shoe', (0.08, 0.11, 0.16), roughness=0.55, metalness=0.06)
SCLERA = mat('Sclera', (0.97, 0.97, 0.97), roughness=0.25)
IRIS = mat('Iris', (0.58, 0.34, 0.16), roughness=0.35)
PUPIL = mat('Pupil', (0.02, 0.02, 0.025), roughness=0.25)
CORNEA = mat('Cornea', (1.0, 1.0, 1.0), roughness=0.04, transmission=0.85)
ACCENT = mat('Accent', (0.13, 0.83, 0.93), roughness=0.3, metalness=0.2, emission=1.2)

# ---------------------------------------------------------------------------
# Armature (rig.py + bone "spine" pour la hiérarchie demandée)
# ---------------------------------------------------------------------------
arm_ob = bpy.data.objects.new('JARVIS_Armature', bpy.data.armatures.new('JARVIS_Armature'))
bpy.context.collection.objects.link(arm_ob)
bpy.context.view_layer.objects.active = arm_ob
bpy.ops.object.mode_set(mode='EDIT')
E = arm_ob.data.edit_bones

def bone(name, head, tail, parent=None):
    b = E.new(name)
    b.head = Vector(head)
    b.tail = Vector(tail)
    b.parent = parent
    return b

def chain(side, x):
    bones = {}
    bones['clavicle'] = bone(f'clavicle_{side}', (x * 0.052, -0.005, Z_CHEST + 0.105), (x * X_ARM, -0.005, Z_SHOULDER), B['chest'])
    bones['upperArm'] = bone(f'upperArm_{side}', (x * X_ARM, -0.005, Z_SHOULDER), (x * X_ELBOW, -0.005, Z_ELBOW), bones['clavicle'])
    bones['lowerArm'] = bone(f'lowerArm_{side}', (x * X_ELBOW, -0.005, Z_ELBOW), (x * X_WRIST, -0.005, Z_WRIST), bones['upperArm'])
    bones['hand'] = bone(f'hand_{side}', (x * X_WRIST, -0.005, Z_WRIST), (x * (X_WRIST + 0.01), -0.005, Z_WRIST - 0.08), bones['lowerArm'])
    # doigts
    fingers = []
    spec = [
        ('thumb', 0.030, -0.030, 0.018, (0.052, 0.050, 0.045)),
        ('index', 0.0, -0.006, 0.006, (0.055, 0.042, 0.032)),
        ('middle', 0.0, -0.010, 0.012, (0.058, 0.044, 0.032)),
        ('ring', 0.0, -0.006, 0.012, (0.054, 0.042, 0.030)),
        ('pinky', 0.0, -0.008, 0.016, (0.048, 0.036, 0.026)),
    ]
    base = (x * (X_WRIST + 0.008), -0.005, Z_WRIST - 0.080)
    for fname, dx, dy, dz, (l1, l2, l3) in spec:
        if fname == 'thumb':
            h0 = (x * (X_WRIST + 0.006), -0.024, Z_WRIST - 0.088)
            d = Vector((-x * 0.30, -0.05, -0.09))
        else:
            h0 = (x * (X_WRIST + 0.008) + x * dx, -0.005 + dy, Z_WRIST - 0.082 + dz)
            d = Vector((0, 0, -1))
        p = None
        nms = [f'{fname}_01_{side}', f'{fname}_02_{side}', f'{fname}_03_{side}']
        pos = h0
        lens = (l1, l2, l3)
        for nm, ln in zip(nms, lens):
            head_v = Vector(pos)
            tail_v = Vector((head_v.x + d.x * ln, head_v.y + d.y * ln, head_v.z + d.z * ln))
            b = bone(nm, head_v, tail_v, bones['hand'] if p is None else p)
            fingers.append(b)
            p = b
            pos = (tail_v.x, tail_v.y, tail_v.z)
    return bones, fingers

B = {}
B['root'] = bone('root', (0, 0, 0), (0, 0, 0.12))
B['pelvis'] = bone('pelvis', (0, 0, Z_HIP), (0, 0, Z_SPINE01), B['root'])
B['spine'] = bone('spine', (0, 0, Z_HIP), (0, 0, Z_SPINE01), B['pelvis'])
B['spine_01'] = bone('spine_01', (0, 0, Z_SPINE01), (0, 0, Z_SPINE02), B['spine'])
B['spine_02'] = bone('spine_02', (0, 0, Z_SPINE02), (0, 0, Z_CHEST), B['spine_01'])
B['chest'] = bone('chest', (0, 0, Z_CHEST), (0, 0, Z_NECK), B['spine_02'])
B['neck'] = bone('neck', (0, 0, Z_NECK), (0, -0.012, Z_HEAD_BASE), B['chest'])
B['head'] = bone('head', (0, -0.012, Z_HEAD_BASE), (0, -0.012, Z_TOP), B['neck'])
ARM = {}
FINGERS = {}
for s, x in (('L', 1.0), ('R', -1.0)):
    ARM[s], FINGERS[s] = chain(s, x)
LEG = {}
for s, x in (('L', 1.0), ('R', -1.0)):
    ul = bone(f'upperLeg_{s}', (x * X_LEG, 0, Z_HIP), (x * X_LEG, 0, Z_KNEE), B['pelvis'])
    ll = bone(f'lowerLeg_{s}', (x * X_LEG, 0, Z_KNEE), (x * X_LEG, 0, Z_ANKLE), ul)
    ft = bone(f'foot_{s}', (x * X_LEG, 0, Z_ANKLE), (x * X_LEG, -0.115, 0.028), ll)
    to = bone(f'toe_{s}', (x * X_LEG, -0.115, 0.028), (x * X_LEG, -0.185, 0.022), ft)
    LEG[s] = {'upperLeg': ul, 'lowerLeg': ll, 'foot': ft, 'toe': to}
bpy.ops.object.mode_set(mode='OBJECT')

# ---------------------------------------------------------------------------
# Tête (mesh unique : crâne + visage + mâchoire + nez + oreilles + lèvres +
# sourcils) avec morph targets. Trois matériaux : Skin, Hair(sourcils), Lip.
# ---------------------------------------------------------------------------
head = new_primitive('primitive_uv_sphere_add', 'Head', SKIN,
                     location=(0, -0.02, 1.63), scale=(0.095, 0.10, 0.135),
                     rotation=(0, 0, 0))
head.data.materials.append(HAIR)
head.data.materials.append(LIP)

def add_feature(kind, name, matn, location, scale, rotation=(0, 0, 0)):
    ob = new_primitive(kind, '_tmp_feature', bpy.data.materials[matn],
                       location=location, scale=scale, rotation=rotation)
    return ob

def join_into(target, ob):
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.select_all(action='DESELECT')
    target.select_set(True)
    ob.select_set(True)
    bpy.ops.object.join()

# Mâchoire / menton / pommettes
jaw = add_feature('primitive_uv_sphere_add', '_jaw', 'Skin', (0, -0.095, 1.50), (0.082, 0.075, 0.10))
join_into(head, jaw)
for sx in (-1.0, 1.0):
    cheek = add_feature('primitive_uv_sphere_add', '_cheek', 'Skin', (sx * 0.072, -0.112, 1.555), (0.045, 0.02, 0.035))
    join_into(head, cheek)

# Nez (arête + pointe)
nose_b = add_feature('primitive_uv_sphere_add', '_nose_b', 'Skin', (0, -0.128, 1.615), (0.014, 0.028, 0.030))
join_into(head, nose_b)
nose_t = add_feature('primitive_uv_sphere_add', '_nose_t', 'Skin', (0, -0.151, 1.545), (0.016, 0.028, 0.024))
join_into(head, nose_t)
for sx in (-1.0, 1.0):
    n_al = add_feature('primitive_uv_sphere_add', '_nose_al', 'Skin', (sx * 0.017, -0.152, 1.532), (0.011, 0.02, 0.016))
    join_into(head, n_al)

# Arcade sourcilière + sourcils (sourcils = matériau Hair)
for sx in (-1.0, 1.0):
    ridge = add_feature('primitive_uv_sphere_add', '_ridge', 'Skin', (sx * 0.036, -0.118, 1.598), (0.032, 0.020, 0.014))
    join_into(head, ridge)
    brow1 = add_feature('primitive_uv_sphere_add', '_brow1', 'Hair', (sx * 0.040, -0.119, 1.590), (0.040, 0.014, 0.014))
    join_into(head, brow1)
    brow2 = add_feature('primitive_uv_sphere_add', '_brow2', 'Hair', (sx * 0.058, -0.118, 1.583), (0.026, 0.012, 0.012))
    join_into(head, brow2)

# Oreilles
for sx in (-1.0, 1.0):
    ear = add_feature('primitive_uv_sphere_add', '_ear', 'Skin', (sx * 0.096, 0.0, 1.595), (0.024, 0.042, 0.055))
    join_into(head, ear)

# Lèvres (liseré double + coins relevés = sourire neutre doux)
up = add_feature('primitive_uv_sphere_add', '_lip_u', 'Lip', (0, -0.130, 1.534), (0.026, 0.016, 0.011))
join_into(head, up)
lo = add_feature('primitive_uv_sphere_add', '_lip_l', 'Lip', (0, -0.132, 1.504), (0.024, 0.015, 0.010))
join_into(head, lo)
for sx in (-1.0, 1.0):
    cn = add_feature('primitive_uv_sphere_add', '_lip_c', 'Lip', (sx * 0.024, -0.129, 1.524), (0.011, 0.015, 0.011))
    join_into(head, cn)

head.name = 'Head'
bpy.context.view_layer.objects.active = head
bpy.ops.object.select_all(action='DESELECT')
head.select_set(True)

# ---------------------------------------------------------------------------
# Morph targets : expressions + visemes (noms stables pour Three.js)
# ---------------------------------------------------------------------------
SKEMS = {}
def skey(name):
    sk = head.shape_key_add(name=name)
    return sk

def verts():
    return [v.co for v in head.data.vertices]

def add_morph(name, deltas):
    sk = head.shape_key_add(name=name)
    for i, d in deltas.items():
        sk.data[i].co = sk.data[i].co + Vector(d)
    return sk

def mask_region(pred):
    return {i: v.co for i, v in enumerate(head.data.vertices) if pred(v.co)}

def box_in(a, b, lo, hi):
    return all(lo[k] <= a[k] <= hi[k] for k in range(3))

# Masques utiles (coordonnées monde = coordonnées mesh après application)
EYE_L = (-0.037, -0.115, 1.565)
EYE_R = (0.037, -0.115, 1.565)
BROW_L = (-0.042, -0.119, 1.588)
BROW_R = (0.042, -0.119, 1.588)
MOUTH = (0, -0.130, 1.52)

def lid_mask(eye):
    ex, ey, ez = eye
    return {i: v.co for i, v in enumerate(head.data.vertices)
            if abs(v.co.x - ex) < 0.045 and v.co.y < -0.05
            and v.co.y > -0.09 and -0.035 < v.co.z - (ez + 0.03) < 0.05}

def brow_mask(eye):
    ex, ey, ez = eye
    return {i: v.co for i, v in enumerate(head.data.vertices)
            if abs(v.co.x - ex) < 0.06 and v.co.y < -0.06 and 0.018 < v.co.z - ez < 0.05}

def mouth_lower():
    return {i: v.co for i, v in enumerate(head.data.vertices)
            if abs(v.co.x) < 0.045 and v.co.y < -0.10 and 1.45 < v.co.z < 1.52}

def mouth_upper():
    return {i: v.co for i, v in enumerate(head.data.vertices)
            if abs(v.co.x) < 0.045 and v.co.y < -0.10 and 1.52 <= v.co.z < 1.55}

def jaw_mask():
    out = {}
    for i, c in enumerate(head.data.vertices):
        v = c.co
        if v.y < -0.05 and v.z < 1.53:
            # le haut du visage n'est jamais inclus
            if not (v.y > -0.115 and v.z > 1.505 and abs(v.x) < 0.02):
                out[i] = v
        if v.y < -0.10 and 1.50 < v.z < 1.545 and abs(v.x) < 0.03:
            out[i] = v  # menton + lèvre inférieure
    return out

def corners(which=1.0):
    out = {}
    for i, c in enumerate(head.data.vertices):
        v = c.co
        if v.y < -0.10 and 1.515 < v.z < 1.535 and abs(v.x) > 0.015:
            out[i] = v
    return out

def rot_z_y(deltas, pivot, angle, ratio=1.0):
    """Rotation autour d'un axe X passant par `pivot` (ouverture/fermeture de la bouche)."""
    out = {}
    for i, c in deltas.items():
        v = c if hasattr(c, 'x') else c.co
        dy, dz = (v.y - pivot[1]), (v.z - pivot[2])
        # petite rotation : applique signe "fermeture vers le bas"
        a = angle * ratio
        ny = dy * math.cos(a) - dz * math.sin(a)
        nz = dy * math.sin(a) + dz * math.cos(a)
        out[i] = (0, ny - dy, nz - dz)
    return out

def morphs():
    head.shape_key_add(name='Basis')
    ml = lid_mask(EYE_L); mr = lid_mask(EYE_R)
    bl = brow_mask(EYE_L); br = brow_mask(EYE_R)
    jaw = jaw_mask()
    lo = mouth_lower(); up = mouth_upper(); cor = corners(1.0)

    def blend_ratio(v):
        return max(0.0, min(1.0, 1.0 - (v.y + 0.150) * 4.0))

    def blend_eye(v):
        return max(0.0, min(1.0, 1.0 - abs(v.y + 0.100) * 8.0))

    add_morph('blinkLeft', {i: (0, 0, -0.030 * blend_eye(v)) for i, v in ml.items()})
    add_morph('blinkRight', {i: (0, 0, -0.030 * blend_eye(v)) for i, v in mr.items()})

    # --- sourcils ---
    add_morph('browUp', {**{i: (0, 0, 0.045 * blend_ratio(v)) for i, v in bl.items()},
                          **{i: (0, 0, 0.045 * blend_ratio(v)) for i, v in br.items()}})
    add_morph('browDown', {**{i: (0, 0, -0.030 * blend_ratio(v)) for i, v in bl.items()},
                            **{i: (0, 0, -0.030 * blend_ratio(v)) for i, v in br.items()}})

    # --- yeux écarquillés / plissés ---
    add_morph('eyeWide', {**{i: (0, 0, 0.035 * blend_eye(v)) for i, v in ml.items()},
                           **{i: (0, 0, 0.035 * blend_eye(v)) for i, v in mr.items()}})
    add_morph('squint', {**{i: (0, 0, -0.024 * blend_eye(v)) for i, v in ml.items()},
                          **{i: (0, 0, -0.024 * blend_eye(v)) for i, v in mr.items()},
                          **{i: (0, -0.003, 0.006) for i, v in
                              {k: v for k, v in mask_region(lambda c:
                                  c.y < -0.10 and 1.55 < c.z < 1.575 and abs(c.x) > 0.04).items()}.items()}})

    # --- sourire / frown ---
    sm = {}
    fm = {}
    for i, v in cor.items():
        corner_x = 1.0 if v.x > 0 else -1.0
        sm[i] = (corner_x * 0.004, -0.002, 0.016)
        fm[i] = (corner_x * -0.003, 0.002, -0.014)
    # joues remontées lors du sourire
    for i, v in mask_region(lambda c: c.y < -0.10 and 1.54 < c.z < 1.575 and abs(c.x) > 0.03).items():
        sm[i] = (v.x * 0.02, -0.002, 0.010)
    add_morph('smile', sm)
    add_morph('frown', fm)
    add_morph('mouthSmile', sm)
    add_morph('mouthFrown', fm)
    add_morph('smileLeft', {i: d for i, d in sm.items()
                            if head.data.vertices[i].co.x <= 0})
    add_morph('smileRight', {i: d for i, d in sm.items()
                             if head.data.vertices[i].co.x >= 0})
    add_morph('squintLeft', {**{i: (0, 0, -0.024 * blend_eye(v)) for i, v in ml.items()},
                              **{i: (0, 0, -0.024 * blend_eye(v)) for i, v in
                                 {k: v for k, v in bl.items() if k in ml}.items()}})
    add_morph('squintRight', {**{i: (0, 0, -0.024 * blend_eye(v)) for i, v in mr.items()},
                               **{i: (0, 0, -0.024 * blend_eye(v)) for i, v in
                                  {k: v for k, v in br.items() if k in mr}.items()}})

    # --- bouche : position, protrusion, ouverture ---
    pucker = {}
    funnel = {}
    for i, v in {**lo, **up}.items():
        corner_x = 1.0 if v.x >= 0 else -1.0
        pucker[i] = (corner_x * v.x * 0.4, -0.012, 0)
        funnel[i] = (corner_x * v.x * 0.7, -0.016, 0)
    add_morph('mouthPucker', pucker)
    add_morph('mouthFunnel', funnel)
    add_morph('mouthLeft', {i: (0.012, 0, 0) for i in {**lo, **up}})
    add_morph('mouthRight', {i: (-0.012, 0, 0) for i in {**lo, **up}})

    # --- mâchoire basée sur rotation ---
    add_morph('jawOpen', rot_z_y(jaw, (0, -0.02, 1.505), 0.22))

    # --- visemes ---
    def viseme(name, open_=0.0, wide=0.0, round_=0.0, up_raise=0.0, lo_raise=0.0):
        d = {}
        j = rot_z_y(dict(jaw), (0, -0.02, 1.505), 0.20 * open_)
        for i, dd in j.items():
            d[i] = d.get(i, (0, 0, 0))
        for i, dd in d.items():
            pass
        # ouverture verticale (lèvres)
        for i, v in up.items():
            d[i] = (v.x * wide * 0.010, -0.004 * round_, 0.012 * open_ + up_raise * 0.008)
        for i, v in lo.items():
            d[i] = (v.x * wide * 0.010, -0.004 * round_, -0.010 * open_ - lo_raise * 0.010)
        # coins
        for i, v in cor.items():
            x = 1.0 if v.x >= 0 else -1.0
            d[i] = (x * wide * 0.014, -0.006 * round_, 0.004 if open_ > 0.3 else 0)
        # mâchoire
        for i, dd in rot_z_y(jaw, (0, -0.02, 1.505), 0.20 * open_).items():
            cur = d.get(i, (0, 0, 0))
            d[i] = (cur[0] + dd[0], cur[1] + dd[1], cur[2] + dd[2])
        add_morph('viseme_' + name, d)

    viseme('REST', 0.0)
    viseme('A', open_=0.9, wide=0.3)
    viseme('E', open_=0.4, wide=1.0)
    viseme('I', open_=0.3, wide=0.8)
    viseme('O', open_=0.55, round_=1.0)
    viseme('U', open_=0.3, round_=1.0)
    viseme('MBP', open_=-0.25, round_=0.5)
    viseme('FV', open_=0.05, up_raise=0.5, lo_raise=-0.3)
    viseme('L', open_=0.2, wide=0.5)
    viseme('WQ', open_=0.3, round_=0.9)
    viseme('CH', open_=0.55, wide=0.5)
    viseme('TH', open_=0.2, wide=-0.2)


# Subdivision d'abord (les clés de forme sont ensuite calculées sur le maillage dense)
subsurf(head, levels=1)
smooth(head)
morphs()

# ---------------------------------------------------------------------------
# Yeux (suivent la tête rigidement, pas de skinning)
# ---------------------------------------------------------------------------
def build_eye(side):
    x = 0.037 if side == 'R' else -0.037
    g = bpy.data.objects.new('Eye_' + side, bpy.data.meshes.new('Eye_' + side))
    bpy.context.collection.objects.link(g)
    scl = new_primitive('primitive_uv_sphere_add', '_sclera', SCLERA, (x, -0.118, 1.565), (0.0155, 0.0155, 0.0155))
    ir = new_primitive('primitive_uv_sphere_add', '_iris', IRIS, (x, -0.128, 1.565), (0.013, 0.004, 0.013))
    pu = new_primitive('primitive_uv_sphere_add', '_pupil', PUPIL, (x, -0.1295, 1.565), (0.0055, 0.003, 0.0055))
    co = new_primitive('primitive_uv_sphere_add', '_cornea', CORNEA, (x, -0.120, 1.565), (0.016, 0.016, 0.016))
    for ob in (scl, ir, pu, co):
        smooth(ob)
        ob.parent = g
    g.parent = arm_ob
    g.parent_type = 'BONE'
    g.parent_bone = 'head'
    return g

build_eye('L')
build_eye('R')

# ---------------------------------------------------------------------------
# Cheveux (volume châtain, mèche latérale balayée). Suivent la tête rigidement.
# ---------------------------------------------------------------------------
hair = mesh_new('Hair', mat=HAIR)
verts, faces = [], []
segments = 16
rings = [0.105, 0.107, 0.106, 0.098, 0.085, 0.068, 0.045]
rz = [1.72, 1.665, 1.60, 1.54, 1.495, 1.465, 1.452]
centers = [0, 0, 0, -0.008, -0.014, -0.02, -0.024]
prev = None
for ring, (z, r, cy) in enumerate(zip(rz, rings, centers)):
    base = len(verts)
    for j in range(segments):
        a = j / segments * math.tau
        verts.append((math.cos(a) * r, -0.03 + math.sin(a) * r + cy + 0.03 * (j / segments), z))
    if prev is None:
        # sommet du crâne : fermeture en éventail
        c = len(verts)
        verts.append((0, -0.03, z + 0.01))
        for j in range(segments):
            faces.append((c, base + j, base + ((j + 1) % segments)))
    else:
        b0 = prev
        for j in range(segments):
            j2 = (j + 1) % segments
            faces.append((b0 + j, b0 + j2, base + j2, base + j))
    prev = base
hair.data.from_pydata(verts, [], faces)
hair.data.update()
smooth(hair)

def add_strand(name, bx, by, bz, len_, tilt, rad=0.016, matn='Hair'):
    from mathutils import Vector as V
    ob = new_primitive('primitive_cylinder_add', name, bpy.data.materials[matn],
                       location=(bx, by, bz), scale=(rad, rad, len_))
    forward = V((abs(tilt[1]), -abs(tilt[0]), 1.0)).normalized()
    q = V((0, 0, 1)).rotation_difference(forward)
    ob.rotation_euler = q.to_euler('XYZ')
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.transform_apply(rotation=True)
    ob.parent = arm_ob
    ob.parent_type = 'BONE'
    ob.parent_bone = 'head'
    smooth(ob)
    return ob

# mèches balayées vers l'arrière / côté (quiff volumineux)
strands = [
    (-0.020, -0.105, 1.70, 0.075, (0.25, 0.12)),
    (0.020, -0.100, 1.70, 0.075, (-0.20, 0.15)),
    (-0.002, -0.112, 1.68, 0.066, (0.12, 0.10)),
    (-0.045, -0.100, 1.66, 0.060, (0.30, 0.06)),
    (0.045, -0.100, 1.66, 0.060, (-0.30, 0.06)),
    (-0.070, -0.085, 1.63, 0.055, (0.35, -0.04)),
    (0.070, -0.085, 1.63, 0.055, (-0.35, -0.04)),
    (-0.095, -0.055, 1.60, 0.050, (0.4, -0.16)),
    (0.095, -0.055, 1.60, 0.050, (-0.4, -0.16)),
]
for i, (bx, by, bz, ln, tilt) in enumerate(strands):
    add_strand('Strand_%d' % i, bx, by, bz, ln, tilt)
# touffe avant (franges courtes au-dessus du front)
for i in range(4):
    x = -0.035 + i * 0.023
    add_strand('Fringe_%d' % i, x, -0.125, 1.60, 0.045, (0.08, 0.10), 0.013)

hair.parent = arm_ob
hair.parent_type = 'BONE'
hair.parent_bone = 'head'

# ---------------------------------------------------------------------------
# Corps (sous les vêtements) + bras/jambes pour les poids de peau
# ---------------------------------------------------------------------------
torso = loft('Body', [0.94, 1.02, 1.10, 1.18, 1.24, 1.30, 1.36, 1.42],
             [0.19, 0.20, 0.21, 0.215, 0.21, 0.205, 0.19, 0.16], sxz=1.0, syz=0.78)
subsurf(torso, levels=1)
skin_to(torso, arm_ob)

neck = new_primitive('primitive_cylinder_add', 'Neck', SKIN, (0, 0, (Z_NECK + Z_HEAD_BASE) / 2), (0.055, 0.055, 0.055))
smooth(neck)
skin_to(neck, arm_ob)

# Bras nus (manches des vêtements les recouvrent) + mains
def build_arm(side):
    x = 1.0 if side == 'L' else -1.0
    upper = new_primitive('primitive_cylinder_add', 'UpperArm_' + side, SKIN,
                          (x * 0.19, -0.005, (Z_SHOULDER + Z_ELBOW) / 2),
                          (0.048, 0.048, 0.28))
    upper.rotation_euler = (0, 0, x * -0.09)
    bpy.context.view_layer.objects.active = upper
    bpy.ops.object.transform_apply(rotation=True)
    smooth(upper)
    skin_to(upper, arm_ob)
    lower = new_primitive('primitive_cylinder_add', 'LowerArm_' + side, SKIN,
                          (x * 0.222, -0.005, (Z_ELBOW + Z_WRIST) / 2),
                          (0.038, 0.038, 0.26))
    lower.rotation_euler = (0, 0, x * -0.13)
    bpy.context.view_layer.objects.active = lower
    bpy.ops.object.transform_apply(rotation=True)
    smooth(lower)
    skin_to(lower, arm_ob)
    return build_hand(side)

def build_hand(side):
    x = 1.0 if side == 'L' else -1.0
    palm = new_primitive('primitive_uv_sphere_add', 'Palm_' + side, SKIN,
                         (x * (X_WRIST + 0.005), -0.005, Z_WRIST - 0.040),
                         (0.028, 0.020, 0.046))
    palm.rotation_euler = (0, x * 0.35, 0)
    bpy.context.view_layer.objects.active = palm
    bpy.ops.object.transform_apply(rotation=True)
    smooth(palm)
    skin_to(palm, arm_ob)
    # doigts : un petit tube par os
    spec_bones = []
    for nm in ('thumb', 'index', 'middle', 'ring', 'pinky'):
        for seg in ('01', '02', '03'):
            spec_bones.append(f'{nm}_{seg}_{side}')
    for nm in spec_bones:
        bn = arm_ob.data.bones[nm]
        h = bn.head; t = bn.tail
        d = Vector(t) - Vector(h)
        ln = d.length
        mid = (Vector(h) + Vector(t)) * 0.5
        ob = new_primitive('primitive_cylinder_add', 'Dig_' + nm, SKIN, mid, (0.009, 0.009, ln))
        q = Vector((0, 0, 1)).rotation_difference(d.normalized())
        ob.rotation_euler = q.to_euler('XYZ')
        bpy.context.view_layer.objects.active = ob
        bpy.ops.object.transform_apply(rotation=True)
        smooth(ob)
        skin_to(ob, arm_ob)
    return palm

build_arm('L')
build_arm('R')

def build_leg(side):
    x = 1.0 if side == 'L' else -1.0
    upper = new_primitive('primitive_cylinder_add', 'UpperLeg_' + side, SKIN,
                          (x * X_LEG, 0, (Z_HIP + Z_KNEE) / 2), (0.062, 0.062, 0.47))
    smooth(upper)
    skin_to(upper, arm_ob)
    lower = new_primitive('primitive_cylinder_add', 'LowerLeg_' + side, SKIN,
                          (x * X_LEG, 0, (Z_KNEE + Z_ANKLE) / 2), (0.042, 0.042, 0.39))
    smooth(lower)
    skin_to(lower, arm_ob)
    foot = new_primitive('primitive_uv_sphere_add', 'Foot_' + side, SKIN,
                         (x * X_LEG, -0.055, 0.055), (0.040, 0.075, 0.055))
    smooth(foot)
    skin_to(foot, arm_ob)

build_leg('L')
build_leg('R')

# ---------------------------------------------------------------------------
# Vêtements : chemise claire, veste gris bleu, pantalon sombre, chaussures
# ---------------------------------------------------------------------------
def garment(name, matn, zs, radii, sxz=1.0, syz=1.0, levels=1):
    ob = loft(name, zs, radii, sxz=sxz, syz=syz, matn=matn)
    subsurf(ob, levels=levels)
    skin_to(ob, arm_ob)
    return ob

shirt = garment('Shirt', 'Shirt',
                [0.955, 1.02, 1.10, 1.18, 1.24, 1.30, 1.35, 1.40, 1.44],
                [0.21, 0.215, 0.22, 0.225, 0.225, 0.22, 0.215, 0.19, 0.165], syz=1.0)
jacket = garment('Jacket', 'Jacket',
                 [0.965, 1.02, 1.08, 1.14, 1.20, 1.26, 1.32, 1.38, 1.44, 1.46],
                 [0.225, 0.228, 0.228, 0.226, 0.224, 0.222, 0.218, 0.205, 0.175, 0.15], syz=0.98)
# revers de veste : deux panneaux inclinés (devant)
def lapel(side):
    x = 1.0 if side == 'L' else -1.0
    ob = new_primitive('primitive_uv_sphere_add', 'Lapel_' + side, JACKET_DARK,
                       (x * 0.11, -0.225, 1.34), (0.035, 0.02, 0.09))
    ob.rotation_euler = (0, 0, x * 0.35)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.transform_apply(rotation=True)
    smooth(ob)
    skin_to(ob, arm_ob)
lapel('L'); lapel('R')
# col chemise
collar = new_primitive('primitive_cylinder_add', 'Collar', SHIRT, (0, 0, 1.455), (0.062, 0.062, 0.022))
smooth(collar)
skin_to(collar, arm_ob)
# petit accessoire : fin liseré cyan au niveau du revers (discret)
pin = new_primitive('primitive_uv_sphere_add', 'LapelPin', ACCENT, (0.125, -0.235, 1.30), (0.006, 0.004, 0.006))
smooth(pin)
skin_to(pin, arm_ob)

# manches veste + chemise (extensions cylindriques légèrement évasées)
def sleeve(name, matn, side, z0, r0, r1, z1, x0, x1, tilt):
    x = 1.0 if side == 'L' else -1.0
    ob = new_primitive('primitive_cylinder_add', name, bpy.data.materials[matn],
                       ((x0 + x1) / 2 * x, -0.005, (z0 + z1) / 2),
                       (1, 1, 1))
    # conversion en cône tronqué : remplace le maillage
    verts, faces = [], []
    n = 14
    base = 0
    for z, r, xc in ((z0, r0, x0 * x), (z1, r1, x1 * x)):
        for j in range(n):
            a = j / n * math.tau
            verts.append((xc + math.cos(a) * r, -0.006 + math.sin(a) * r, z))
        base += n
    for j in range(n):
        j2 = (j + 1) % n
        faces.append((j, j2, n + j2, n + j))
    replate_mesh(ob, verts, faces)
    ob.rotation_euler = (0, 0, x * tilt)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.transform_apply(rotation=True)
    smooth(ob)
    subsurf(ob, levels=1)
    skin_to(ob, arm_ob)
    return ob

# manche longue chemise (épaule → poignet)
sleeve('ShirtSleeve_L', 'Shirt', 'L', 1.36, 0.06, 0.042, 0.86, 0.16, 0.235, 0.10)
sleeve('ShirtSleeve_R', 'Shirt', 'R', 1.36, 0.06, 0.042, 0.86, -0.16, -0.235, -0.10)
# manche courte veste (épaule → mi-avant-bras)
sleeve('JacketSleeve_L', 'Jacket', 'L', 1.37, 0.065, 0.055, 1.02, 0.158, 0.228, 0.10)
sleeve('JacketSleeve_R', 'Jacket', 'R', 1.37, 0.065, 0.055, 1.02, -0.158, -0.228, -0.10)

trousers = garment('Trousers', 'Trousers',
                   [0.93, 0.90, 0.84, 0.76, 0.68, 0.60, 0.52, 0.45],
                   [0.165, 0.15, 0.115, 0.09, 0.075, 0.062, 0.054, 0.048], sxz=1.0, syz=0.92)
# bas de pantalon séparés (deux jambes masquent le tube unique)
for s, x in (('L', 1.0), ('R', -1.0)):
    leg2 = new_primitive('primitive_cylinder_add', 'TrouserLeg_' + s, TROUS,
                         (x * X_LEG, 0, (Z_KNEE + 0.10) / 2), (0.052, 0.052, 0.45))
    smooth(leg2)
    skin_to(leg2, arm_ob)

for s, x in (('L', 1.0), ('R', -1.0)):
    shoe = new_primitive('primitive_uv_sphere_add', 'Shoe_' + s, SHOE,
                         (x * X_LEG, -0.052, 0.075), (0.05, 0.085, 0.07))
    shoe.scale = (0.05, 0.085, 0.058)
    shoe.location = (x * X_LEG, -0.052, 0.058)
    bpy.context.view_layer.objects.active = shoe
    bpy.ops.object.transform_apply(location=True, scale=True)
    smooth(shoe)
    subsurf(shoe, levels=1)
    skin_to(shoe, arm_ob)

# ---------------------------------------------------------------------------
# Animations — réutilise assets/blender/animations.py (noms compatibles runtime)
# ---------------------------------------------------------------------------
import sys, os as _os
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import animations as ANIM

ARM_OB = arm_ob
if ARM_OB.animation_data is None:
    ARM_OB.animation_data_create()

import json

def bake_action(spec):
    """spec: {name, frames, loop, kind, keys: {frame: {bone: (pitch,tilt,yaw)}}}"""
    act = bpy.data.actions.new(spec['name'])
    ARM_OB.animation_data.action = act
    root = ARM_OB.pose.bones.get('root')
    for frame in sorted(spec['keys']):
        bpy.context.scene.frame_set(frame)
        pose = spec['keys'][frame]
        for bone_name, rot in pose.items():
            if bone_name.startswith('__'):
                continue
            pb = ARM_OB.pose.bones.get(bone_name)
            if pb is None:
                continue
            pb.rotation_mode = 'XYZ'
            pb.rotation_euler = (
                math.radians(rot[0]), math.radians(rot[1]), math.radians(rot[2]))
            pb.keyframe_insert(data_path='rotation_euler', frame=frame)
        loc = pose.get('__loc_root')
        if loc is not None and root is not None:
            root.location = Vector(loc)
            root.keyframe_insert(data_path='location', frame=frame)
    ARM_OB.animation_data.action = None
    return act

for spec in ANIM.ALL_ACTIONS:
    bake_action(spec)

# ---------------------------------------------------------------------------
# Métadonnées exportées dans les extras glTF (requis par ui/js/avatar/)
# ---------------------------------------------------------------------------
morph_names = [k for k in head.data.shape_keys.key_blocks.keys() if k != 'Basis']
meta = {
    **ANIM.METADATA,
    "morphTargets": morph_names,
    "visemes": [k for k in morph_names if k.startswith('viseme_')],
    "expressions": [k for k in morph_names if not k.startswith('viseme_')],
    "fingerChains": {
        chain: [f'{chain}_{i:02d}_{side}' for i in (1, 2, 3)]
        for side in ('L', 'R')
        for chain in ('thumb', 'index', 'middle', 'ring', 'pinky')
    },
    "height": 1.78,
}
arm_ob['jarvis'] = json.dumps(meta)
bpy.context.view_layer.update()

# ---------------------------------------------------------------------------
# Export — toutes les actions disponibles via NLA strips
# ---------------------------------------------------------------------------
bpy.context.scene.frame_set(1)
if ARM_OB.animation_data is None:
    ARM_OB.animation_data_create()
tracks = [a.name for a in bpy.data.actions]
for act_name in tracks:
    act = bpy.data.actions[act_name]
    trk = ARM_OB.animation_data.nla_tracks.new()
    trk.name = act_name
    trk.mute = False
    frames = [int(kc.co.x) for f in act.fcurves for kc in f.keyframe_points] if act.fcurves else [1]
    last = max(frames or [2])
    strip = trk.strips.new(act_name, 1, act)
    strip.action_frame_end = last
ARM_OB.animation_data.action = None

import os
os.makedirs(os.path.dirname(OUT), exist_ok=True)
bpy.ops.export_scene.gltf(
    filepath=OUT,
    export_format='GLB',
    use_selection=False,
    export_animations=True,
    export_skins=True,
    export_morph=True,
    export_yup=True,
    export_apply=True,
    export_extras=True,
)
print('EXPORTED', OUT)