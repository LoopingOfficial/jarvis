"""Animation : keyframes, actions, pistes NLA, cycles full body.

Deux familles :
  - animations d'OBJET (rotation, flottement, orbite) pour un modele sans rig ;
  - animations d'ARMATURE (idle, walk, run, wave, sit...) pour un personnage.
Chaque clip devient une Action poussee sur une piste NLA, ce qui permet a
l'exporteur glTF de produire plusieurs animations dans un seul GLB.
"""
from __future__ import annotations

import math

import bpy
from mathutils import Euler, Quaternion, Vector

OBJECT_CLIPS = {"spin", "rotate", "turn", "float", "bounce", "orbit", "pulse", "showcase"}
RIG_CLIPS = {"idle", "walk", "run", "turn", "wave", "talk", "head_turn", "look_around",
             "raise_hand", "point", "sit", "jump", "t_pose"}


def _fps(fps: int = 30) -> int:
    bpy.context.scene.render.fps = int(fps)
    return int(fps)


def _ensure_quaternion(pbone) -> None:
    if pbone.rotation_mode != "QUATERNION":
        pbone.rotation_mode = "QUATERNION"


def _key_bone(pbone, frame: int, rot_deg=(0.0, 0.0, 0.0), loc=None) -> None:
    """Pose un bone en degres (X, Y, Z) et pose une vraie keyframe."""
    _ensure_quaternion(pbone)
    euler = Euler([math.radians(float(a)) for a in rot_deg], "XYZ")
    pbone.rotation_quaternion = euler.to_quaternion()
    pbone.keyframe_insert("rotation_quaternion", frame=int(frame))
    if loc is not None:
        pbone.location = Vector(loc)
        pbone.keyframe_insert("location", frame=int(frame))


def action_fcurves(action) -> list:
    """F-curves d'une action, Blender 3.x/4.x (fcurves) et 5.x (slotted actions)."""
    curves = getattr(action, "fcurves", None)
    if curves is not None:
        return list(curves)
    out = []
    for layer in getattr(action, "layers", []) or []:
        for strip in getattr(layer, "strips", []) or []:
            for bag in getattr(strip, "channelbags", []) or []:
                out.extend(list(getattr(bag, "fcurves", []) or []))
    return out


def new_action(owner, name: str):
    action = bpy.data.actions.new(name)
    if owner.animation_data is None:
        owner.animation_data_create()
    owner.animation_data.action = action
    return action


def push_to_nla(owner, action, track_name: str = "") -> bool:
    """Isole l'action sur sa propre piste NLA (une animation GLB par clip).

    Blender 5.x introduit les actions a slots : la piste NLA doit alors
    recevoir le slot utilise, sinon la strip ne joue rien. En cas d'echec, on
    conserve l'action active plutot que de perdre l'animation.
    """
    if owner.animation_data is None:
        owner.animation_data_create()
    slot = getattr(owner.animation_data, "action_slot", None)
    try:
        track = owner.animation_data.nla_tracks.new()
        track.name = track_name or action.name
        strip = track.strips.new(action.name, int(action.frame_range[0]), action)
        if slot is not None and hasattr(strip, "action_slot"):
            strip.action_slot = slot
    except Exception:
        return False        # l'action reste assignee : elle sera exportee quand meme
    owner.animation_data.action = None
    return True


def set_range(length: int, fps: int = 30) -> None:
    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = max(2, int(length))
    _fps(fps)


# ---------------------------------------------------------------------------
# Animations d'objet (modele sans armature)
# ---------------------------------------------------------------------------
def animate_object(obj, clip: str = "spin", duration: float = 3.0, fps: int = 30,
                   axis: str = "Z", amplitude: float = 1.0, loop: bool = True) -> dict:
    """Anime reellement un objet. Retourne le descriptif du clip cree."""
    clip = str(clip or "spin").lower()
    frames = max(2, int(float(duration) * int(fps)))
    action = new_action(obj, "JARVIS_" + clip)
    base_loc = obj.location.copy()
    base_rot = obj.rotation_euler.copy()
    obj.rotation_mode = "XYZ"
    idx = {"X": 0, "Y": 1, "Z": 2}.get(str(axis).upper(), 2)

    if clip in {"spin", "rotate", "turn", "showcase"}:
        turns = 1.0 if clip != "turn" else 0.5
        for i, frame in enumerate((1, frames // 2, frames)):
            rot = base_rot.copy()
            rot[idx] = base_rot[idx] + 2.0 * math.pi * turns * (i / 2.0)
            obj.rotation_euler = rot
            obj.keyframe_insert("rotation_euler", frame=frame)
    elif clip in {"float", "bounce"}:
        height = 0.18 * float(amplitude) * max(0.2, obj.dimensions.z or 1.0)
        for frame, dz in ((1, 0.0), (frames // 2, height), (frames, 0.0)):
            obj.location = base_loc + Vector((0.0, 0.0, dz))
            obj.keyframe_insert("location", frame=frame)
    elif clip == "pulse":
        base_scale = obj.scale.copy()
        for frame, factor in ((1, 1.0), (frames // 2, 1.0 + 0.12 * float(amplitude)), (frames, 1.0)):
            obj.scale = base_scale * factor
            obj.keyframe_insert("scale", frame=frame)
    elif clip == "orbit":
        radius = max(0.5, (obj.dimensions.length or 1.0))
        steps = 24
        for i in range(steps + 1):
            angle = 2.0 * math.pi * i / steps
            obj.location = base_loc + Vector((radius * math.cos(angle),
                                              radius * math.sin(angle), 0.0))
            obj.keyframe_insert("location", frame=1 + int(i * frames / steps))
    else:
        return {"created": False, "reason": "clip objet inconnu: " + clip}

    _make_linear_or_bezier(action, loop)
    push_to_nla(obj, action, action.name)
    set_range(frames, fps)
    return {"created": True, "name": action.name, "target": obj.name,
            "kind": "object", "frames": frames, "fps": fps,
            "duration_s": round(frames / float(fps), 2)}


def _make_linear_or_bezier(action, loop: bool) -> None:
    for fcurve in action_fcurves(action):
        for kp in fcurve.keyframe_points:
            kp.interpolation = "BEZIER"
        if loop:
            try:
                mod = fcurve.modifiers.new("CYCLES")
                mod.mode_before = "REPEAT"
                mod.mode_after = "REPEAT"
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Animations d'armature (full body)
# ---------------------------------------------------------------------------
def _pose(rig):
    bpy.context.view_layer.objects.active = rig
    try:
        bpy.ops.object.mode_set(mode="POSE")
    except Exception:
        pass


def _reset_pose(rig) -> None:
    for pbone in rig.pose.bones:
        _ensure_quaternion(pbone)
        pbone.rotation_quaternion = Quaternion((1, 0, 0, 0))
        pbone.location = Vector((0, 0, 0))


def _bone(rig, name):
    return rig.pose.bones.get(name)


def _walk_cycle(rig, frames: int, stride: float = 34.0, run: bool = False) -> None:
    """Cycle de marche 4 poses (contact, passing, contact oppose, passing)."""
    swing = float(stride) * (1.6 if run else 1.0)
    lift = 42.0 if run else 26.0
    bounce = 0.055 if run else 0.028
    quarter = max(1, frames // 4)
    keys = [1, 1 + quarter, 1 + 2 * quarter, 1 + 3 * quarter, frames]
    # (frame index) -> (phase L, phase R) : +1 jambe avant, -1 jambe arriere
    phases = [(1.0, -1.0), (0.0, 0.0), (-1.0, 1.0), (0.0, 0.0), (1.0, -1.0)]
    for frame, (pl, pr) in zip(keys, phases):
        for side, phase in (("L", pl), ("R", pr)):
            thigh = _bone(rig, "thigh_" + side)
            shin = _bone(rig, "shin_" + side)
            foot = _bone(rig, "foot_" + side)
            arm = _bone(rig, "upperArm_" + side)
            fore = _bone(rig, "lowerArm_" + side)
            if thigh:
                _key_bone(thigh, frame, (swing * phase, 0, 0))
            if shin:
                bend = lift if phase < -0.2 else (6.0 if phase > 0.2 else lift * 0.55)
                _key_bone(shin, frame, (-bend, 0, 0))
            if foot:
                _key_bone(foot, frame, (12.0 * phase, 0, 0))
            if arm:
                # Les bras contrebalancent les jambes.
                _key_bone(arm, frame, (-swing * 0.62 * phase, 0, 0))
            if fore:
                _key_bone(fore, frame, (-(18.0 if run else 11.0), 0, 0))
        pelvis = _bone(rig, "pelvis")
        if pelvis:
            up = bounce if abs(pl) < 0.2 else 0.0
            _key_bone(pelvis, frame, (2.0 if run else 1.0, 0, 0), loc=(0.0, 0.0, up))
        chest = _bone(rig, "chest")
        if chest:
            _key_bone(chest, frame, (4.0 if run else 1.5, 0, -6.0 * pl))


def _idle(rig, frames: int) -> None:
    mid = max(2, frames // 2)
    for frame, amount in ((1, 0.0), (mid, 1.0), (frames, 0.0)):
        for name, rot, loc in (
            ("pelvis", (1.2 * amount, 0, 0), (0.0, 0.0, -0.012 * amount)),
            ("spine_01", (1.5 * amount, 0, 0), None),
            ("chest", (2.0 * amount, 0, 1.2 * amount), None),
            ("head", (-1.6 * amount, 0, 2.0 * amount), None),
            ("upperArm_L", (3.0 * amount, 0, 0), None),
            ("upperArm_R", (3.0 * amount, 0, 0), None),
        ):
            pbone = _bone(rig, name)
            if pbone:
                _key_bone(pbone, frame, rot, loc)


def _wave(rig, frames: int, side: str = "R") -> None:
    arm = _bone(rig, "upperArm_" + side)
    fore = _bone(rig, "lowerArm_" + side)
    hand = _bone(rig, "hand_" + side)
    sign = 1.0 if side == "R" else -1.0
    raise_f = max(2, frames // 5)
    for frame, up in ((1, 0.0), (raise_f, 1.0), (frames - raise_f, 1.0), (frames, 0.0)):
        if arm:
            _key_bone(arm, frame, (0, 0, -105.0 * up * sign))
        if fore:
            _key_bone(fore, frame, (0, 0, -35.0 * up * sign))
    steps = 4
    for i in range(steps + 1):
        frame = raise_f + int(i * (frames - 2 * raise_f) / max(1, steps))
        if hand:
            _key_bone(hand, frame, (0, 0, 26.0 * sign * (1 if i % 2 == 0 else -1)))


def _head_turn(rig, frames: int, angle: float = 42.0) -> None:
    head, neck = _bone(rig, "head"), _bone(rig, "neck")
    quarter = max(2, frames // 4)
    for frame, factor in ((1, 0.0), (quarter, 1.0), (frames - quarter, -1.0), (frames, 0.0)):
        if head:
            _key_bone(head, frame, (0, 0, float(angle) * factor))
        if neck:
            _key_bone(neck, frame, (0, 0, float(angle) * 0.35 * factor))


def _raise_hand(rig, frames: int, side: str = "R", point: bool = False) -> None:
    arm = _bone(rig, "upperArm_" + side)
    fore = _bone(rig, "lowerArm_" + side)
    sign = 1.0 if side == "R" else -1.0
    hold = max(2, frames // 4)
    target = (0, 0, -150.0 * sign) if not point else (0, -78.0 * sign, -62.0 * sign)
    for frame, up in ((1, 0.0), (hold, 1.0), (frames - hold, 1.0), (frames, 0.0)):
        if arm:
            _key_bone(arm, frame, tuple(a * up for a in target))
        if fore:
            _key_bone(fore, frame, (0, 0, (-18.0 if point else -25.0) * sign * up))


def _sit(rig, frames: int) -> None:
    end = frames
    mid = max(2, frames // 2)
    for frame, amount in ((1, 0.0), (mid, 0.6), (end, 1.0)):
        for name, rot, loc in (
            ("pelvis", (0, 0, 0), (0.0, 0.0, -0.42 * amount)),
            ("thigh_L", (86.0 * amount, 0, 0), None),
            ("thigh_R", (86.0 * amount, 0, 0), None),
            ("shin_L", (-88.0 * amount, 0, 0), None),
            ("shin_R", (-88.0 * amount, 0, 0), None),
            ("spine_01", (-6.0 * amount, 0, 0), None),
            ("upperArm_L", (12.0 * amount, 0, 0), None),
            ("upperArm_R", (12.0 * amount, 0, 0), None),
        ):
            pbone = _bone(rig, name)
            if pbone:
                _key_bone(pbone, frame, rot, loc)


def _talk(rig, frames: int) -> None:
    head = _bone(rig, "head")
    chest = _bone(rig, "chest")
    steps = 6
    for i in range(steps + 1):
        frame = 1 + int(i * (frames - 1) / steps)
        swing = (1 if i % 2 == 0 else -1)
        if head:
            _key_bone(head, frame, (-4.0 * swing, 0, 6.0 * swing))
        if chest:
            _key_bone(chest, frame, (1.5 * swing, 0, 2.0 * swing))
    for side in ("L", "R"):
        arm, fore = _bone(rig, "upperArm_" + side), _bone(rig, "lowerArm_" + side)
        sign = 1.0 if side == "R" else -1.0
        for i in range(steps + 1):
            frame = 1 + int(i * (frames - 1) / steps)
            up = 22.0 if i % 2 == 0 else 34.0
            if arm:
                _key_bone(arm, frame, (0, 0, -up * sign))
            if fore:
                _key_bone(fore, frame, (0, 0, -(38.0 if i % 2 else 52.0) * sign))


def _body_turn(rig, frames: int, angle: float = 180.0) -> None:
    root = _bone(rig, "root")
    if root is None:
        return
    for frame, factor in ((1, 0.0), (frames, 1.0)):
        _key_bone(root, frame, (0, 0, float(angle) * factor))
    _walk_cycle(rig, frames, stride=16.0)


def _jump(rig, frames: int) -> None:
    pelvis = _bone(rig, "pelvis")
    crouch, peak, land = max(2, frames // 5), max(3, frames // 2), frames
    for frame, (dz, knee) in ((1, (0.0, 0.0)), (crouch, (-0.16, 62.0)),
                              (peak, (0.42, 18.0)), (land, (0.0, 0.0))):
        if pelvis:
            _key_bone(pelvis, frame, (0, 0, 0), loc=(0.0, 0.0, dz))
        for side in ("L", "R"):
            thigh, shin = _bone(rig, "thigh_" + side), _bone(rig, "shin_" + side)
            if thigh:
                _key_bone(thigh, frame, (knee * 0.6, 0, 0))
            if shin:
                _key_bone(shin, frame, (-knee, 0, 0))


CLIP_BUILDERS = {
    "idle": lambda rig, f, o: _idle(rig, f),
    "walk": lambda rig, f, o: _walk_cycle(rig, f, o.get("stride", 34.0), False),
    "run": lambda rig, f, o: _walk_cycle(rig, f, o.get("stride", 34.0), True),
    "wave": lambda rig, f, o: _wave(rig, f, o.get("side", "R")),
    "talk": lambda rig, f, o: _talk(rig, f),
    "head_turn": lambda rig, f, o: _head_turn(rig, f, o.get("angle", 42.0)),
    "look_around": lambda rig, f, o: _head_turn(rig, f, o.get("angle", 62.0)),
    "raise_hand": lambda rig, f, o: _raise_hand(rig, f, o.get("side", "R"), False),
    "point": lambda rig, f, o: _raise_hand(rig, f, o.get("side", "R"), True),
    "sit": lambda rig, f, o: _sit(rig, f),
    "turn": lambda rig, f, o: _body_turn(rig, f, o.get("angle", 180.0)),
    "jump": lambda rig, f, o: _jump(rig, f),
    "t_pose": lambda rig, f, o: None,
}

DEFAULT_DURATION = {"idle": 4.0, "walk": 1.2, "run": 0.8, "wave": 2.4, "talk": 3.0,
                    "head_turn": 3.0, "look_around": 4.0, "raise_hand": 2.4,
                    "point": 2.4, "sit": 2.0, "turn": 1.6, "jump": 1.4, "t_pose": 0.5}


def animate_rig(rig, clip: str = "idle", duration: float = 0.0, fps: int = 30,
                options=None, loop: bool = True) -> dict:
    """Cree une VRAIE action d'armature. Retourne le descriptif du clip."""
    clip = str(clip or "idle").lower()
    builder = CLIP_BUILDERS.get(clip)
    if builder is None:
        return {"created": False, "reason": "clip inconnu: " + clip}
    seconds = float(duration) if float(duration or 0) > 0 else DEFAULT_DURATION.get(clip, 2.0)
    frames = max(2, int(seconds * int(fps)))
    _pose(rig)
    _reset_pose(rig)
    action = new_action(rig, "JARVIS_" + clip)
    builder(rig, frames, dict(options or {}))
    if clip == "t_pose":
        for pbone in rig.pose.bones:
            _key_bone(pbone, 1, (0, 0, 0))
    _make_linear_or_bezier(action, loop and clip in {"idle", "walk", "run", "talk"})
    try:
        bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass
    push_to_nla(rig, action, action.name)
    set_range(frames, fps)
    return {"created": True, "name": action.name, "target": rig.name, "kind": "rig",
            "clip": clip, "frames": frames, "fps": fps,
            "duration_s": round(frames / float(fps), 2)}


def clear_animations() -> int:
    """Supprime toutes les actions et pistes NLA. Retourne le nombre supprime."""
    count = 0
    for obj in bpy.data.objects:
        if obj.animation_data:
            for track in list(obj.animation_data.nla_tracks):
                obj.animation_data.nla_tracks.remove(track)
            obj.animation_data.action = None
            obj.animation_data_clear()
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)
        count += 1
    return count


def animation_summary() -> list:
    out = []
    for obj in bpy.data.objects:
        if not obj.animation_data:
            continue
        for track in obj.animation_data.nla_tracks:
            for strip in track.strips:
                out.append({"name": strip.action.name if strip.action else track.name,
                            "track": track.name, "target": obj.name,
                            "frames": [int(strip.frame_start), int(strip.frame_end)]})
        if obj.animation_data.action:
            action = obj.animation_data.action
            out.append({"name": action.name, "track": "", "target": obj.name,
                        "frames": [int(action.frame_range[0]), int(action.frame_range[1])]})
    return out
