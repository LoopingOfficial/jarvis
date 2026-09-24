import bpy
import math
import os

OUT = r"C:/Users/jerom/Desktop/jarvis-mac/velko/unreal/anim_source"
os.makedirs(OUT, exist_ok=True)

scene = bpy.context.scene
scene.render.fps = 30

# remove default stuff
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete()

# ---------- build armature ----------
bpy.ops.object.armature_add(enter_editmode=False, location=(0, 0, 0))
arm_obj = bpy.context.active_object
arm_obj.name = "MHC_VELKO_RIG"
arm_obj.show_in_front = True
arm = arm_obj.data
bpy.ops.object.mode_set(mode="EDIT")
arm.edit_bones.remove(arm.edit_bones[0])

def add_bone(name, head, tail, parent=None):
    e = arm.edit_bones.new(name)
    e.head = head
    e.tail = tail
    if parent:
        e.parent = armar.edit_bones.get(parent)
    return e

# We need direct access in edit mode
edit = arm.edit_bones

def NB(name, head, tail, parent=None):
    b = edit.new(name)
    b.head = (head[0], head[1], head[2])
    b.tail = (tail[0], tail[1], tail[2])
    if parent:
        b.parent = edit.get(parent)
    return b

root   = NB("root", (0, 0, 0), (0, 0, 0.97))
pelvis = NB("pelvis", (0, 0, 0.97), (0, 0, 1.02), "root")
sp1    = NB("spine_01", (0, 0, 1.02), (0, 0, 1.08), "pelvis")
sp2    = NB("spine_02", (0, 0, 1.08), (0, 0, 1.16), "spine_01")
sp3    = NB("spine_03", (0, 0, 1.16), (0, 0, 1.23), "spine_02")
sp4    = NB("spine_04", (0, 0, 1.23), (0, 0, 1.29), "spine_03")
sp5    = NB("spine_05", (0, 0, 1.29), (0, 0, 1.36), "spine_04")
nk1    = NB("neck_01", (0, 0, 1.36), (0, 0, 1.42), "spine_05")
nk2    = NB("neck_02", (0, 0, 1.42), (0, 0, 1.47), "neck_01")
head   = NB("head", (0, 0, 1.47), (0, 0, 1.60), "neck_02")

for side in ("l", "r"):
    sx = -0.16 if side == "l" else 0.16
    cl = NB("clavicle_%s" % side, (sx, 0.02, 1.37), (sx * 1.55, 0.04, 1.35), "spine_05")
    ua = NB("upperarm_%s" % side, (sx * 1.55, 0.04, 1.30), (sx * 2.3, 0.06, 1.10), "clavicle_%s" % side)
    la = NB("lowerarm_%s" % side, (sx * 2.3, 0.06, 1.10), (sx * 2.85, 0.08, 0.95), "upperarm_%s" % side)
    ha = NB("hand_%s" % side, (sx * 2.85, 0.08, 0.95), (sx * 3.1, 0.10, 0.92), "lowerarm_%s" % side)
    # fingers (light chains)
    for fname in ("thumb", "index", "middle", "ring", "little"):
        bname = "pinky" if fname == "little" else fname
        fx = sx * 3.1
        fz = 0.93
        if fname == "thumb":
            p1 = NB("thumb_01_%s" % side, (fx, 0.14, 0.92), (fx + sx * 0.18, 0.20, 0.90), "hand_%s" % side)
            p2 = NB("thumb_02_%s" % side, (fx + sx * 0.18, 0.20, 0.90), (fx + sx * 0.36, 0.24, 0.87), "thumb_01_%s" % side)
            p3 = NB("thumb_03_%s" % side, (fx + sx * 0.36, 0.24, 0.87), (fx + sx * 0.52, 0.26, 0.84), "thumb_02_%s" % side)
            continue
        zoff = {"index": 0.92, "middle": 0.93, "ring": 0.91, "little": 0.90}[fname]
        p1 = NB("%s_01_%s" % (bname, side), (fx, 0.10, zoff), (fx + sx * 0.28, 0.10, zoff), "hand_%s" % side)
        p2 = NB("%s_02_%s" % (bname, side), (fx + sx * 0.28, 0.10, zoff), (fx + sx * 0.55, 0.10, zoff), "%s_01_%s" % (bname, side))
        p3 = NB("%s_03_%s" % (bname, side), (fx + sx * 0.55, 0.10, zoff), (fx + sx * 0.80, 0.10, zoff), "%s_02_%s" % (bname, side))

for side in ("l", "r"):
    sx = -0.09 if side == "l" else 0.09
    th = NB("thigh_%s" % side, (sx, 0, 0.97), (sx, 0, 0.50), "pelvis")
    ca = NB("calf_%s" % side, (sx, 0, 0.50), (sx, 0, 0.18), "thigh_%s" % side)
    fo = NB("foot_%s" % side, (sx, 0, 0.18), (sx, 0.10, 0.08), "calf_%s" % side)
    ba = NB("ball_%s" % side, (sx, 0.10, 0.08), (sx, 0.16, 0.08), "foot_%s" % side)

bpy.ops.object.mode_set(mode="OBJECT")

# switch armature to quaternion for stable FK anim? we'll use euler
for pbone in arm_obj.pose.bones:
    pbone.rotation_mode = "QUATERNION"

def set_key(bone, loc=None, rotq=None, frame=None):
    pb = arm_obj.pose.bones[bone]
    if loc is not None:
        pb.location = loc
    if rotq is not None:
        pb.rotation_quaternion = rotq
    if frame is not None:
        if loc is not None:
            pb.keyframe_insert(data_path="location", frame=frame)
        if rotq is not None:
            pb.keyframe_insert(data_path="rotation_quaternion", frame=frame)

def eul(y, p, r):
    e = mathutils.Euler((math.radians(r), math.radians(p), math.radians(y)), "XYZ")
    return e.to_quaternion()

import mathutils
ROOT_OFF = eul(0, 0, 0)

def make_action(name, start, end, fn):
    act = bpy.data.actions.new(name)
    arm_obj.animation_data_create()
    arm_obj.animation_data.action = act
    for f in range(start, end + 1):
        t = float(f - start) / max(1, (end - start))
        arm_obj.animation_data.action = act
        fn(t, f)
    return act

def set_all_reference():
    # reference pose
    for b in arm_obj.pose.bones:
        b.location = mathutils.Vector((0, 0, 0))
        b.rotation_quaternion = mathutils.Quaternion((1, 0, 0, 0))

set_all_reference()

# ---------- proxy mesh for export (skinned) ----------
bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=0.26, depth=1.45, location=(0, 0, 0.82))
mesh_obj = bpy.context.active_object
mesh_obj.name = "VELKO_PROXY_MESH"
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.subdivide(number_cuts=6)
bpy.ops.object.mode_set(mode="OBJECT")
bpy.context.view_layer.objects.active = arm_obj
bpy.ops.object.select_all(action="DESELECT")
arm_obj.select_set(True)
mesh_obj.select_set(True)
bpy.ops.object.parent_set(type="ARMATURE_AUTO")
print("PROXY_SKINNED", bpy.ops.object.parent_set.poll())

# ---------------- actions ----------------
import math as m

def anim_idle(t, f):
    bob = m.sin(f * 2 * m.pi / 90) * 0.015
    sway = m.sin(f * 2 * m.pi / 90) * 1.2
    breathe = m.sin(f * 2 * m.pi / 60) * 2.5
    set_key("root", loc=(0, 0, bob), frame=f)
    set_key("pelvis", loc=(0, 0, bob), frame=f)
    set_key("spine_02", rotq=eul(sway, 0, 0), frame=f)
    set_key("spine_04", rotq=eul(sway * 0.5, 0, 0), frame=f)
    set_key("spine_05", rotq=eul(sway * 0.6, -breathe, 0), frame=f)
    set_key("neck_01", rotq=eul(0, -breathe * 0.4, 0), frame=f)
    set_key("head", rotq=eul(sway * 0.3, -breathe * 0.3, 0), frame=f)
    set_key("lowerarm_l", rotq=eul(-2 + sway * 0.4, 0, 0), frame=f)
    set_key("lowerarm_r", rotq=eul(-2 + sway * 0.4, 0, 0), frame=f)

def anim_walk(t, f):
    cyc = (f % 60) / 60.0
    ang = cyc * 2 * m.pi
    legUp = m.sin(ang)
    knee = max(0, m.sin(ang)) * 30
    arm = m.sin(ang + m.pi) * 25
    bob = m.sin(ang * 2) * 0.03
    sway = m.sin(ang) * 3
    set_key("root", loc=(0, legUp * 0.002, bob), frame=f)
    set_key("pelvis", loc=(0, 0, bob), rotq=eul(sway, 0, m.sin(ang) * 2), frame=f)
    set_key("thigh_l", rotq=eul(legUp * 22, 0, 0), frame=f)
    set_key("thigh_r", rotq=eul(-legUp * 22, 0, 0), frame=f)
    set_key("calf_l", rotq=eul(knee, 0, 0), frame=f)
    set_key("calf_r", rotq=eul(-knee, 0, 0), frame=f)
    set_key("foot_l", rotq=eul(-legUp * 8, 0, 0), frame=f)
    set_key("foot_r", rotq=eul(legUp * 8, 0, 0), frame=f)
    set_key("upperarm_l", rotq=eul(-arm, 0, 0), frame=f)
    set_key("upperarm_r", rotq=eul(arm, 0, 0), frame=f)
    set_key("lowerarm_l", rotq=eul(-arm * 0.4, 0, 0), frame=f)
    set_key("lowerarm_r", rotq=eul(arm * 0.4, 0, 0), frame=f)
    set_key("spine_02", rotq=eul(-sway * 0.5, 0, 0), frame=f)
    set_key("spine_05", rotq=eul(sway * 0.4, 0, 0), frame=f)
    set_key("head", rotq=eul(sway * 0.3, 0, 0), frame=f)
    if f == 0:
        set_key("thigh_l", rotq=eul(0, 0, 0), frame=0)

def anim_sit(t, f):
    p = min(1.0, f / 60.0)
    ap = max(0, min(1, p * 2))
    # lower body: hips drop, knees bend
    drop = -0.42 * p
    set_key("root", loc=(0, 0, drop), frame=f)
    set_key("pelvis", loc=(0, 0, drop), rotq=eul(60 * p, 0, 0), frame=f)
    set_key("spine_02", rotq=eul(-30 * p, 10 * ap, 0), frame=f)
    set_key("spine_04", rotq=eul(-35 * p, 0, 0), frame=f)
    set_key("spine_05", rotq=eul(-30 * p, -8 * ap, 0), frame=f)
    set_key("neck_01", rotq=eul(18 * p, 0, 0), frame=f)
    set_key("head", rotq=eul(10 * p, -5 * ap, 0), frame=f)
    set_key("thigh_l", rotq=eul(85 * p, 0, 0), frame=f)
    set_key("thigh_r", rotq=eul(85 * p, 0, 0), frame=f)
    set_key("calf_l", rotq=eul(-120 * p, 0, 0), frame=f)
    set_key("calf_r", rotq=eul(-120 * p, 0, 0), frame=f)
    set_key("foot_l", rotq=eul(20 * p, 0, 0), frame=f)
    set_key("foot_r", rotq=eul(20 * p, 0, 0), frame=f)
    set_key("upperarm_l", rotq=eul(25 * p, 0, -10 * p), frame=f)
    set_key("upperarm_r", rotq=eul(25 * p, 0, 10 * p), frame=f)
    set_key("lowerarm_l", rotq=eul(-80 * p, 0, 0), frame=f)
    set_key("lowerarm_r", rotq=eul(-80 * p, 0, 0), frame=f)

def anim_turn(t, f):
    yaw = -90.0 * (f / 60.0)
    set_key("root", rotq=eul(yaw, 0, 0), frame=f)
    set_key("pelvis", rotq=eul(yaw, 0, 0), frame=f)
    set_key("spine_05", rotq=eul(yaw, 0, 0), frame=f)
    set_key("head", rotq=eul(yaw, 0, 0), frame=f)
    # feet shuffle
    lw = m.sin(f / 60.0 * 2 * m.pi) * 8
    rw = m.sin((f / 60.0 + 0.5) * 2 * m.pi) * 8
    set_key("foot_l", rotq=eul(lw, 0, 0), frame=f)
    set_key("foot_r", rotq=eul(rw, 0, 0), frame=f)

# bake actions then export each as its own fbx
for act_name, fn, dur in (("VELKO_Idle", anim_idle, 60), ("VELKO_Walk", anim_walk, 60),
                          ("VELKO_Sit", anim_sit, 90), ("VELKO_Turn", anim_turn, 60)):
    set_all_reference()
    make_action(act_name, 1, dur, fn)
    scene.frame_start = 1
    scene.frame_end = dur
    scene.frame_set(1)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.select_all(action="DESELECT")
    arm_obj.select_set(True)
    mesh_obj.select_set(True)
    filepath = os.path.join(OUT, act_name + ".fbx")
    bpy.ops.export_scene.fbx(
        filepath=filepath,
        use_selection=True,
        object_types={"MESH", "ARMATURE"},
        add_leaf_bones=False,
        apply_unit_scale=True,
        bake_anim=True,
        bake_anim_use_all_bones=True,
        bake_anim_simplify_factor=1.0,
        bake_anim_step=1,
        use_armature_deform_only=True,
        axis_forward="-Z",
        axis_up="Y",
    )
    print("EXPORTED", filepath)
    # strip this action so the next file carries exactly one take
    anim_data = arm_obj.animation_data
    if anim_data and anim_data.action:
        a = anim_data.action
        arm_obj.animation_data.action = None
        bpy.data.actions.remove(a)

print("ALL_DONE")