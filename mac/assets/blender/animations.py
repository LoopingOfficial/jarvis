"""Bibliothèque d'animations de JARVIS.

Les poses sont décrites en degrés dans un repère lisible :
    (pitch, tilt, yaw)
  pitch → hochement avant/arrière (axe X monde)
  tilt  → inclinaison latérale     (axe Y monde)
  yaw   → rotation gauche/droite   (axe Z monde)

Trois familles :
  - base      : idle (plusieurs variantes) et locomotion, en boucle ;
  - additive  : gestes du haut du corps, joués par-dessus la base ;
  - oneshot   : départ/arrêt de marche, demi-tours.

Toutes les clips additives démarrent sur la pose de repos : le runtime les
convertit en animation additive (Three.js `makeClipAdditive`) sans décalage.
"""
from __future__ import annotations

import math

FPS = 30

# Pose de repos utilisée comme référence par toutes les clips additives.
REST: dict[str, tuple[float, float, float]] = {}


def _mirror(pose: dict) -> dict:
    """Transpose une pose du côté gauche vers le côté droit."""
    out = {}
    for bone, rot in pose.items():
        if bone.endswith("_L"):
            name = bone[:-2] + "_R"
        elif bone.endswith("_R"):
            name = bone[:-2] + "_L"
        else:
            out[bone] = (rot[0], -rot[1], -rot[2])
            continue
        out[name] = (rot[0], -rot[1], -rot[2])
    return out


def _arms_down(spread: float = 0.0, bend: float = 0.0) -> dict:
    """Bras le long du corps, très légèrement écartés et fléchis."""
    return {
        "upperArm_L": (2.0 + bend * 0.3, 0.0, -(4.0 + spread)),
        "upperArm_R": (2.0 + bend * 0.3, 0.0, 4.0 + spread),
        "lowerArm_L": (0.0, 0.0, -(6.0 + bend)),
        "lowerArm_R": (0.0, 0.0, 6.0 + bend),
        "hand_L": (0.0, 0.0, -2.0),
        "hand_R": (0.0, 0.0, 2.0),
    }


def _relaxed_fingers(curl: float = 1.0) -> dict:
    """Main détendue : les doigts ne sont jamais parfaitement tendus."""
    out = {}
    for side, sign in (("L", 1.0), ("R", -1.0)):
        for name, base in (("index", 12.0), ("middle", 14.0), ("ring", 16.0),
                           ("pinky", 18.0)):
            out[f"{name}_01_{side}"] = (-base * 0.45 * curl, 0.0, 0.0)
            out[f"{name}_02_{side}"] = (-base * 0.75 * curl, 0.0, 0.0)
            out[f"{name}_03_{side}"] = (-base * 0.55 * curl, 0.0, 0.0)
        out[f"thumb_01_{side}"] = (-6.0 * curl, 0.0, sign * 8.0 * curl)
        out[f"thumb_02_{side}"] = (-8.0 * curl, 0.0, 0.0)
        out[f"thumb_03_{side}"] = (-6.0 * curl, 0.0, 0.0)
    return out


def _pose(*parts: dict) -> dict:
    out: dict[str, tuple[float, float, float]] = {}
    for part in parts:
        for bone, rot in part.items():
            prev = out.get(bone, (0.0, 0.0, 0.0))
            out[bone] = (prev[0] + rot[0], prev[1] + rot[1], prev[2] + rot[2])
    return out


BASE_STANCE = _pose(_arms_down(), _relaxed_fingers(1.0))


# ---------------------------------------------------------------------------
# Idle — quatre variantes, jamais parfaitement symétriques
# ---------------------------------------------------------------------------
def _idle(name: str, *, seconds: float, sway: float, breath: float,
          head_drift: float, weight: float, arms: dict | None = None) -> dict:
    frames = int(seconds * FPS)
    half = frames // 2
    quarter = frames // 4
    arms = arms or {}
    keys: dict[int, dict] = {}

    def at(f: int, extra: dict) -> None:
        keys[f] = _pose(BASE_STANCE, arms, extra)

    at(0, {
        "pelvis": (0.0, weight * 0.6, 0.0),
        "spine_01": (breath * 0.4, -weight * 0.3, sway * 0.4),
        "spine_02": (breath * 0.5, -weight * 0.2, sway * 0.5),
        "chest": (breath * 0.8, 0.0, sway * 0.3),
        "neck": (-breath * 0.3, 0.0, -sway * 0.4),
        "head": (head_drift * 0.5, head_drift * 0.6, -sway * 0.5),
    })
    at(quarter, {
        "pelvis": (0.0, -weight * 0.2, sway * 0.2),
        "spine_01": (breath * 0.9, weight * 0.2, -sway * 0.2),
        "spine_02": (breath * 1.1, weight * 0.1, -sway * 0.3),
        "chest": (breath * 1.6, 0.0, -sway * 0.2),
        "neck": (-breath * 0.7, 0.0, sway * 0.3),
        "head": (-head_drift * 0.4, -head_drift * 0.3, sway * 0.6),
    })
    at(half, {
        "pelvis": (0.0, -weight * 0.6, 0.0),
        "spine_01": (breath * 0.3, weight * 0.3, -sway * 0.4),
        "spine_02": (breath * 0.4, weight * 0.2, -sway * 0.5),
        "chest": (breath * 0.6, 0.0, -sway * 0.3),
        "neck": (-breath * 0.2, 0.0, sway * 0.4),
        "head": (head_drift * 0.3, -head_drift * 0.5, sway * 0.4),
    })
    at(half + quarter, {
        "pelvis": (0.0, weight * 0.2, -sway * 0.2),
        "spine_01": (breath * 1.0, -weight * 0.2, sway * 0.2),
        "spine_02": (breath * 1.2, -weight * 0.1, sway * 0.3),
        "chest": (breath * 1.7, 0.0, sway * 0.2),
        "neck": (-breath * 0.8, 0.0, -sway * 0.3),
        "head": (-head_drift * 0.5, head_drift * 0.4, -sway * 0.5),
    })
    keys[frames] = dict(keys[0])
    return {"name": name, "frames": frames, "loop": True, "kind": "base", "keys": keys}


IDLES = [
    _idle("idle_neutral", seconds=6.0, sway=1.1, breath=1.3, head_drift=1.0, weight=0.8),
    _idle("idle_relaxed", seconds=7.5, sway=1.6, breath=1.5, head_drift=1.5, weight=1.6,
          arms={"upperArm_L": (0.0, 0.0, -2.5), "lowerArm_L": (0.0, 0.0, -5.0),
                "clavicle_L": (0.0, 0.0, -1.5)}),
    _idle("idle_attentive", seconds=5.0, sway=0.7, breath=1.0, head_drift=0.6, weight=0.4,
          arms={"chest": (-1.5, 0.0, 0.0), "clavicle_L": (0.0, 0.0, -2.0),
                "clavicle_R": (0.0, 0.0, 2.0)}),
    _idle("idle_shift_weight", seconds=9.0, sway=1.4, breath=1.2, head_drift=1.2, weight=2.6,
          arms={"upperArm_R": (0.0, 0.0, 3.0), "lowerArm_R": (0.0, 0.0, 4.0)}),
]

# Bras croisés : idle « posture fermée » utilisée ponctuellement.
_ARMS_CROSSED = {
    "clavicle_L": (0.0, 0.0, -4.0), "clavicle_R": (0.0, 0.0, 4.0),
    "upperArm_L": (-8.0, 0.0, -38.0), "upperArm_R": (-8.0, 0.0, 38.0),
    "lowerArm_L": (0.0, 18.0, -95.0), "lowerArm_R": (0.0, -18.0, 95.0),
    "hand_L": (0.0, 0.0, -18.0), "hand_R": (0.0, 0.0, 18.0),
}
IDLES.append(_idle("idle_arms_crossed", seconds=8.0, sway=0.8, breath=1.0,
                   head_drift=0.8, weight=1.0, arms=_ARMS_CROSSED))


# ---------------------------------------------------------------------------
# Locomotion
# ---------------------------------------------------------------------------
WALK_FRAMES = 32
# Amplitude de hanche : détermine la longueur de pas, donc la vitesse au sol.
HIP_SWING = 24.0
LEG_LENGTH = 0.84
STRIDE = 2.0 * LEG_LENGTH * math.sin(math.radians(HIP_SWING))   # par pas
CYCLE_DISTANCE = STRIDE * 2.0                                    # deux pas / cycle


def _walk(name: str, direction: float = 1.0, strafe: float = 0.0) -> dict:
    """Cycle de marche complet : jambes, bassin, contre-rotation, balancement."""
    keys: dict[int, dict] = {}
    n = WALK_FRAMES
    for f in range(n + 1):
        t = (f % n) / n
        phase = t * 2.0 * math.pi
        swing = math.sin(phase)
        counter = math.sin(phase + math.pi)
        bob = abs(math.sin(phase * 2.0))

        hip_l = HIP_SWING * swing * direction
        hip_r = HIP_SWING * counter * direction
        # Le genou ne fléchit que pendant la phase de retour (jambe en l'air).
        knee_l = max(0.0, -swing * direction) * 52.0 + 6.0
        knee_r = max(0.0, -counter * direction) * 52.0 + 6.0
        # Pied : déroulé talon → pointe.
        foot_l = -hip_l * 0.35 + max(0.0, swing * direction) * 8.0
        foot_r = -hip_r * 0.35 + max(0.0, counter * direction) * 8.0

        pose = _pose(_relaxed_fingers(0.8), {
            "root": (0.0, 0.0, 0.0),
            "pelvis": (1.5, math.sin(phase * 2.0) * 1.8, swing * 6.0 * direction),
            "spine_01": (-0.5, 0.0, -swing * 2.5 * direction),
            "spine_02": (-0.5, 0.0, -swing * 3.0 * direction),
            "chest": (-1.0, 0.0, -swing * 3.5 * direction),
            "neck": (1.0, 0.0, swing * 1.2 * direction),
            "head": (0.5, math.sin(phase) * 0.8, swing * 1.0 * direction),

            "upperLeg_L": (-hip_l, strafe * 6.0, 0.0),
            "lowerLeg_L": (knee_l, 0.0, 0.0),
            "foot_L": (foot_l, 0.0, 0.0),
            "toe_L": (max(0.0, swing * direction) * 12.0, 0.0, 0.0),
            "upperLeg_R": (-hip_r, strafe * 6.0, 0.0),
            "lowerLeg_R": (knee_r, 0.0, 0.0),
            "foot_R": (foot_r, 0.0, 0.0),
            "toe_R": (max(0.0, counter * direction) * 12.0, 0.0, 0.0),

            # Les bras contre-balancent les jambes.
            "clavicle_L": (0.0, 0.0, -2.0),
            "clavicle_R": (0.0, 0.0, 2.0),
            "upperArm_L": (hip_r * 0.62, 0.0, -6.0),
            "lowerArm_L": (-abs(hip_r) * 0.30 - 12.0, 0.0, -8.0),
            "hand_L": (0.0, 0.0, -3.0),
            "upperArm_R": (hip_l * 0.62, 0.0, 6.0),
            "lowerArm_R": (-abs(hip_l) * 0.30 - 12.0, 0.0, 8.0),
            "hand_R": (0.0, 0.0, 3.0),
        })
        # Oscillation verticale : le bassin remonte à chaque appui.
        pose["__loc_root"] = (0.0, 0.0, bob * 0.022 - 0.010)
        keys[f] = pose
    return {"name": name, "frames": n, "loop": True, "kind": "base", "keys": keys}


def _turn(name: str, direction: float) -> dict:
    """Pivot sur place : le corps tourne, les pieds se replacent."""
    n = 24
    keys = {}
    for f in range(n + 1):
        t = f / n
        phase = t * 2.0 * math.pi
        swing = math.sin(phase)
        keys[f] = _pose(BASE_STANCE, {
            "pelvis": (1.0, 0.0, direction * 4.0 * math.sin(phase * 0.5)),
            "chest": (-1.0, 0.0, -direction * 2.0 * math.sin(phase * 0.5)),
            "head": (0.0, 0.0, direction * 3.0 * math.sin(phase * 0.5)),
            "upperLeg_L": (-14.0 * swing * direction, 0.0, direction * 8.0),
            "lowerLeg_L": (max(0.0, -swing * direction) * 30.0 + 5.0, 0.0, 0.0),
            "upperLeg_R": (14.0 * swing * direction, 0.0, direction * 8.0),
            "lowerLeg_R": (max(0.0, swing * direction) * 30.0 + 5.0, 0.0, 0.0),
            "upperArm_L": (-swing * 8.0, 0.0, -8.0),
            "upperArm_R": (swing * 8.0, 0.0, 8.0),
            "lowerArm_L": (-10.0, 0.0, -8.0),
            "lowerArm_R": (-10.0, 0.0, 8.0),
        })
    return {"name": name, "frames": n, "loop": True, "kind": "base", "keys": keys}


def _transition(name: str, target_walk: bool) -> dict:
    """Départ ou arrêt de marche — supprime le passage brutal idle ↔ walk."""
    n = 14
    keys = {}
    walk = _walk("tmp")["keys"]
    start = BASE_STANCE if target_walk else walk[WALK_FRAMES // 4]
    end = walk[0] if target_walk else BASE_STANCE
    bones = set(start) | set(end)
    for f in range(n + 1):
        t = f / n
        t = t * t * (3 - 2 * t)
        pose = {}
        for bone in bones:
            if bone.startswith("__"):
                continue
            a = start.get(bone, (0.0, 0.0, 0.0))
            b = end.get(bone, (0.0, 0.0, 0.0))
            pose[bone] = tuple(a[i] + (b[i] - a[i]) * t for i in range(3))
        keys[f] = pose
    return {"name": name, "frames": n, "loop": False, "kind": "base", "keys": keys}


LOCOMOTION = [
    _walk("walk_forward", 1.0),
    _walk("walk_backward", -1.0),
    _walk("walk_left", 1.0, strafe=1.0),
    _walk("walk_right", 1.0, strafe=-1.0),
    _turn("turn_left", 1.0),
    _turn("turn_right", -1.0),
    _transition("start_walk", True),
    _transition("stop_walk", False),
]


# ---------------------------------------------------------------------------
# Gestes conversationnels (additifs, haut du corps)
# ---------------------------------------------------------------------------
def _gesture(name: str, poses: list[tuple[float, dict]], seconds: float = 1.6) -> dict:
    """`poses` : [(t normalisé 0→1, pose)]. La clip commence et finit au repos."""
    frames = int(seconds * FPS)
    keys: dict[int, dict] = {0: {}}
    for t, pose in poses:
        keys[max(1, int(t * frames))] = pose
    keys[frames] = {}
    return {"name": name, "frames": frames, "loop": False, "kind": "additive", "keys": keys}


def _hand_open(side: str, sign: float) -> dict:
    return {
        f"index_01_{side}": (14.0, 0.0, 0.0), f"index_02_{side}": (18.0, 0.0, 0.0),
        f"middle_01_{side}": (14.0, 0.0, 0.0), f"middle_02_{side}": (20.0, 0.0, 0.0),
        f"ring_01_{side}": (12.0, 0.0, 0.0), f"ring_02_{side}": (18.0, 0.0, 0.0),
        f"pinky_01_{side}": (10.0, 0.0, 0.0), f"pinky_02_{side}": (16.0, 0.0, 0.0),
        f"thumb_01_{side}": (4.0, 0.0, sign * -6.0),
    }


def _point(side: str) -> dict:
    return {
        f"index_01_{side}": (16.0, 0.0, 0.0), f"index_02_{side}": (18.0, 0.0, 0.0),
        f"index_03_{side}": (12.0, 0.0, 0.0),
        f"middle_01_{side}": (-28.0, 0.0, 0.0), f"middle_02_{side}": (-45.0, 0.0, 0.0),
        f"ring_01_{side}": (-30.0, 0.0, 0.0), f"ring_02_{side}": (-48.0, 0.0, 0.0),
        f"pinky_01_{side}": (-30.0, 0.0, 0.0), f"pinky_02_{side}": (-48.0, 0.0, 0.0),
    }


_EXPLAIN_A = _pose({
    "clavicle_R": (0.0, 0.0, 5.0),
    "upperArm_R": (-32.0, 0.0, 26.0), "lowerArm_R": (-46.0, 14.0, 30.0),
    "hand_R": (-10.0, 18.0, 8.0), "chest": (0.0, 0.0, -3.0), "head": (0.0, 1.5, -2.0),
}, _hand_open("R", -1.0))
_EXPLAIN_B = _pose({
    "clavicle_R": (0.0, 0.0, 4.0),
    "upperArm_R": (-24.0, 0.0, 32.0), "lowerArm_R": (-38.0, 6.0, 24.0),
    "hand_R": (6.0, 8.0, 4.0), "chest": (0.0, 0.0, 2.0), "head": (0.0, -1.0, 1.5),
}, _hand_open("R", -1.0))

GESTURES = [
    _gesture("gesture_neutral", [(0.5, {"chest": (0.0, 0.0, 1.0), "head": (1.0, 0.0, 0.0)})],
             seconds=1.2),
    _gesture("gesture_explain", [(0.25, _EXPLAIN_A), (0.6, _EXPLAIN_B), (0.85, _EXPLAIN_A)],
             seconds=2.2),
    _gesture("gesture_open_hand", [(0.35, _pose({
        "clavicle_R": (0.0, 0.0, 6.0), "clavicle_L": (0.0, 0.0, -6.0),
        "upperArm_R": (-26.0, 0.0, 30.0), "upperArm_L": (-26.0, 0.0, -30.0),
        "lowerArm_R": (-40.0, 10.0, 26.0), "lowerArm_L": (-40.0, -10.0, -26.0),
        "hand_R": (-6.0, 20.0, 0.0), "hand_L": (-6.0, -20.0, 0.0),
        "chest": (-2.0, 0.0, 0.0),
    }, _hand_open("R", -1.0), _hand_open("L", 1.0))), (0.7, _pose({
        "upperArm_R": (-20.0, 0.0, 26.0), "upperArm_L": (-20.0, 0.0, -26.0),
        "lowerArm_R": (-34.0, 8.0, 22.0), "lowerArm_L": (-34.0, -8.0, -22.0),
    }, _hand_open("R", -1.0), _hand_open("L", 1.0)))], seconds=2.0),
    _gesture("gesture_small_point", [(0.35, _pose({
        "clavicle_R": (0.0, 0.0, 4.0),
        "upperArm_R": (-38.0, 0.0, 20.0), "lowerArm_R": (-52.0, 8.0, 16.0),
        "hand_R": (-4.0, 6.0, 0.0), "head": (2.0, 0.0, -3.0),
    }, _point("R"))), (0.62, _pose({
        "upperArm_R": (-34.0, 0.0, 22.0), "lowerArm_R": (-46.0, 8.0, 16.0),
    }, _point("R")))], seconds=1.7),
    _gesture("gesture_acknowledge", [
        (0.28, {"head": (7.0, 0.0, 0.0), "neck": (4.0, 0.0, 0.0)}),
        (0.52, {"head": (-2.0, 0.0, 0.0), "neck": (-1.0, 0.0, 0.0)}),
        (0.74, {"head": (4.0, 0.0, 0.0), "neck": (2.0, 0.0, 0.0)})], seconds=1.3),
    _gesture("gesture_agree", [
        (0.22, {"head": (9.0, 0.0, 0.0), "neck": (5.0, 0.0, 0.0), "chest": (2.0, 0.0, 0.0)}),
        (0.45, {"head": (-4.0, 0.0, 0.0), "neck": (-2.0, 0.0, 0.0)}),
        (0.68, {"head": (8.0, 0.0, 0.0), "neck": (4.0, 0.0, 0.0)}),
        (0.86, {"head": (-2.0, 0.0, 0.0)})], seconds=1.5),
    _gesture("gesture_disagree", [
        (0.22, {"head": (0.0, 1.5, 13.0), "neck": (0.0, 0.0, 6.0)}),
        (0.5, {"head": (0.0, -1.5, -13.0), "neck": (0.0, 0.0, -6.0)}),
        (0.78, {"head": (0.0, 1.0, 8.0), "neck": (0.0, 0.0, 4.0)})], seconds=1.5),
    _gesture("gesture_thinking", [
        (0.3, {"head": (-4.0, 6.0, 12.0), "neck": (-2.0, 3.0, 6.0),
               "chest": (0.0, 0.0, 3.0)}),
        (0.7, {"head": (-2.0, 4.0, 9.0), "neck": (-1.0, 2.0, 5.0)})], seconds=2.6),
    _gesture("gesture_success", [(0.3, _pose({
        "clavicle_R": (0.0, 0.0, 7.0),
        "upperArm_R": (-46.0, 0.0, 22.0), "lowerArm_R": (-58.0, 6.0, 14.0),
        "hand_R": (-8.0, 0.0, 0.0), "head": (5.0, 0.0, 0.0), "chest": (-3.0, 0.0, 0.0),
    }, _hand_open("R", -1.0))), (0.62, {"head": (7.0, 0.0, 0.0)})], seconds=1.8),
    _gesture("gesture_concern", [
        (0.35, {"head": (-3.0, 3.0, 6.0), "chest": (3.0, 0.0, 0.0),
                "clavicle_L": (0.0, 0.0, -3.0), "clavicle_R": (0.0, 0.0, 3.0)}),
        (0.7, {"head": (-1.0, 2.0, 4.0), "chest": (2.0, 0.0, 0.0)})], seconds=2.0),
    _gesture("gesture_welcome", [(0.32, _pose({
        "clavicle_R": (0.0, 0.0, 8.0), "clavicle_L": (0.0, 0.0, -8.0),
        "upperArm_R": (-34.0, 0.0, 40.0), "upperArm_L": (-34.0, 0.0, -40.0),
        "lowerArm_R": (-30.0, 12.0, 30.0), "lowerArm_L": (-30.0, -12.0, -30.0),
        "hand_R": (-8.0, 22.0, 0.0), "hand_L": (-8.0, -22.0, 0.0),
        "chest": (-4.0, 0.0, 0.0), "head": (3.0, 0.0, 0.0),
    }, _hand_open("R", -1.0), _hand_open("L", 1.0))), (0.72, _pose({
        "upperArm_R": (-26.0, 0.0, 34.0), "upperArm_L": (-26.0, 0.0, -34.0),
    }, _hand_open("R", -1.0), _hand_open("L", 1.0)))], seconds=2.2),
    _gesture("gesture_question", [(0.35, _pose({
        "clavicle_R": (0.0, 0.0, 5.0),
        "upperArm_R": (-22.0, 0.0, 30.0), "lowerArm_R": (-44.0, 16.0, 26.0),
        "hand_R": (-14.0, 26.0, 0.0), "head": (-2.0, 4.0, -6.0),
    }, _hand_open("R", -1.0))), (0.7, _pose({
        "upperArm_R": (-18.0, 0.0, 28.0), "head": (-1.0, 3.0, -4.0),
    }, _hand_open("R", -1.0)))], seconds=1.9),
    _gesture("gesture_wait", [(0.3, _pose({
        "clavicle_R": (0.0, 0.0, 5.0),
        "upperArm_R": (-40.0, 0.0, 18.0), "lowerArm_R": (-56.0, 4.0, 12.0),
        "hand_R": (-18.0, 0.0, 0.0),
    }, _hand_open("R", -1.0))), (0.75, _pose({
        "upperArm_R": (-38.0, 0.0, 18.0),
    }, _hand_open("R", -1.0)))], seconds=1.8),
    _gesture("gesture_reassure", [(0.32, _pose({
        "clavicle_R": (0.0, 0.0, 4.0),
        "upperArm_R": (-28.0, 0.0, 22.0), "lowerArm_R": (-42.0, 10.0, 18.0),
        "hand_R": (12.0, 10.0, 0.0), "head": (4.0, 2.0, 0.0),
    }, _hand_open("R", -1.0))), (0.55, _pose({
        "upperArm_R": (-24.0, 0.0, 22.0), "hand_R": (-6.0, 10.0, 0.0),
    }, _hand_open("R", -1.0))), (0.78, _pose({
        "upperArm_R": (-27.0, 0.0, 22.0), "hand_R": (10.0, 10.0, 0.0),
    }, _hand_open("R", -1.0)))], seconds=2.0),
    _gesture("gesture_arms_cross", [(0.55, _ARMS_CROSSED), (1.0, _ARMS_CROSSED)],
             seconds=1.6),
]

# Noms de contrat utilisés par AvatarEngine / Three.js. Les clips détaillés
# restent disponibles (idle_neutral, gesture_explain, ...), mais ces aliases
# rendent le pack stable pour le state machine et l'AnimationMixer.
def _alias(name: str, source: dict) -> dict:
    return {**source, "name": name,
            "keys": {frame: dict(pose) for frame, pose in source["keys"].items()}}


_gesture_by_name = {item["name"]: item for item in GESTURES}
CANONICAL = [
    _alias("idle", IDLES[0]),
    _alias("breathing", IDLES[0]),
    _alias("blink", _gesture_by_name["gesture_neutral"]),
    _alias("look_around", _gesture_by_name["gesture_disagree"]),
    _alias("head_nod", _gesture_by_name["gesture_acknowledge"]),
    _alias("head_tilt", _gesture_by_name["gesture_thinking"]),
    _alias("thinking", _gesture_by_name["gesture_thinking"]),
    _alias("talking", _gesture_by_name["gesture_explain"]),
    _gesture("typing", [(0.25, {"hand_L": (-3.0, 0.0, -2.0), "hand_R": (-3.0, 0.0, 2.0)}),
                         (0.55, {"hand_L": (3.0, 0.0, -2.0), "hand_R": (3.0, 0.0, 2.0)}),
                         (0.8, {"hand_L": (-2.0, 0.0, -2.0), "hand_R": (2.0, 0.0, 2.0)})], seconds=1.2),
    _alias("wave", _gesture_by_name["gesture_welcome"]),
    _alias("point", _gesture_by_name["gesture_small_point"]),
    _alias("explain", _gesture_by_name["gesture_explain"]),
    _alias("walk", LOCOMOTION[0]),
]

ALL_ACTIONS = IDLES + LOCOMOTION + GESTURES + CANONICAL

METADATA = {
    "fps": FPS,
    "walkCycleFrames": WALK_FRAMES,
    "walkCycleSeconds": WALK_FRAMES / FPS,
    "walkCycleDistance": round(CYCLE_DISTANCE, 4),
    "walkSpeed": round(CYCLE_DISTANCE / (WALK_FRAMES / FPS), 4),
    "baseClips": [a["name"] for a in IDLES + LOCOMOTION],
    "additiveClips": [a["name"] for a in GESTURES],
    "idleClips": [a["name"] for a in IDLES],
    "canonicalClips": [a["name"] for a in CANONICAL],
}
