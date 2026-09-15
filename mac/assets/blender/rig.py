"""Squelette humain complet de JARVIS.

Définition unique et reproductible du rig : noms, hiérarchie, positions.
Convention Blender : Z = haut, le personnage regarde vers -Y (ce qui donne
+Z « forward » une fois exporté en glTF, la convention de Three.js).
"""
from __future__ import annotations

# Proportions (mètres) d'un adulte élancé de 1,78 m.
HEIGHT = 1.78
Z_ANKLE = 0.095
Z_KNEE = 0.475
Z_HIP = 0.935
Z_SPINE01 = 1.010
Z_SPINE02 = 1.115
Z_CHEST = 1.235
Z_NECK = 1.452
Z_HEAD = 1.528
Z_HEAD_TOP = 1.775
Z_SHOULDER = 1.402
Z_ELBOW = 1.115
Z_WRIST = 0.855

X_LEG = 0.098
X_SHOULDER = 0.052
X_ARM = 0.172
X_ELBOW = 0.198
X_WRIST = 0.212

# (nom, tête, queue, parent, roll)
BONES: list[tuple[str, tuple, tuple, str | None, float]] = [
    ("root", (0.0, 0.0, 0.0), (0.0, 0.0, 0.12), None, 0.0),
    ("pelvis", (0.0, 0.0, Z_HIP), (0.0, 0.0, Z_SPINE01), "root", 0.0),
    ("spine_01", (0.0, 0.0, Z_SPINE01), (0.0, 0.0, Z_SPINE02), "pelvis", 0.0),
    ("spine_02", (0.0, 0.0, Z_SPINE02), (0.0, 0.0, Z_CHEST), "spine_01", 0.0),
    ("chest", (0.0, 0.0, Z_CHEST), (0.0, 0.0, Z_NECK), "spine_02", 0.0),
    ("neck", (0.0, 0.0, Z_NECK), (0.0, -0.012, Z_HEAD), "chest", 0.0),
    ("head", (0.0, -0.012, Z_HEAD), (0.0, -0.012, Z_HEAD_TOP), "neck", 0.0),
]

# -- yeux ------------------------------------------------------------------
# Sans os dediés, les globes suivent la tete et le regard reste mort : ces
# deux os permettent les saccades et le look-at reels.
EYE_BONE_X = 0.038
EYE_BONE_Y = -0.066
EYE_BONE_Z = 1.634
for _s, _x in (("L", 1.0), ("R", -1.0)):
    BONES.append((f"eye_{_s}", (_x * EYE_BONE_X, EYE_BONE_Y, EYE_BONE_Z),
                  (_x * EYE_BONE_X, EYE_BONE_Y - 0.022, EYE_BONE_Z), "head", 0.0))

# -- bras ------------------------------------------------------------------
for _s, _x in (("L", 1.0), ("R", -1.0)):
    BONES += [
        (f"clavicle_{_s}", (_x * X_SHOULDER, -0.005, Z_CHEST + 0.105),
         (_x * X_ARM, -0.005, Z_SHOULDER), "chest", 0.0),
        (f"upperArm_{_s}", (_x * X_ARM, -0.005, Z_SHOULDER),
         (_x * X_ELBOW, -0.005, Z_ELBOW), f"clavicle_{_s}", 0.0),
        (f"lowerArm_{_s}", (_x * X_ELBOW, -0.005, Z_ELBOW),
         (_x * X_WRIST, -0.005, Z_WRIST), f"upperArm_{_s}", 0.0),
        (f"hand_{_s}", (_x * X_WRIST, -0.005, Z_WRIST),
         (_x * (X_WRIST + 0.008), -0.005, Z_WRIST - 0.082), f"lowerArm_{_s}", 0.0),
    ]

# -- doigts ----------------------------------------------------------------
# (nom, décalage latéral depuis le centre de la paume, décalage avant, longueurs)
FINGERS = [
    ("thumb", -0.026, -0.020, (0.035, 0.030, 0.026), 0.030),
    ("index", -0.022, -0.006, (0.038, 0.024, 0.018), 0.000),
    ("middle", -0.007, -0.004, (0.041, 0.026, 0.019), 0.000),
    ("ring", 0.008, -0.004, (0.037, 0.024, 0.018), 0.000),
    ("pinky", 0.022, -0.003, (0.030, 0.019, 0.015), 0.000),
]

for _s, _x in (("L", 1.0), ("R", -1.0)):
    palm_x = _x * (X_WRIST + 0.008)
    palm_z = Z_WRIST - 0.082
    for name, across, forward, lengths, thumb_drop in FINGERS:
        # `across` court le long de l'axe X (largeur de la main), `forward` en Y.
        base = (palm_x + _x * across * 0.0, -0.005 + forward, palm_z + thumb_drop)
        if name == "thumb":
            base = (palm_x - _x * 0.028, -0.005 - 0.018, palm_z + 0.052)
        else:
            base = (palm_x + _x * across, -0.005 + forward, palm_z)
        parent = f"hand_{_s}"
        z = base[2]
        y = base[1]
        for i, length in enumerate(lengths, start=1):
            if name == "thumb":
                # Le pouce part en diagonale vers l'avant et le bas.
                head = (base[0] - _x * (i - 1) * 0.0, y, z)
                tail = (head[0] - _x * length * 0.45, y - length * 0.55, z - length * 0.55)
            else:
                head = (base[0], y, z)
                tail = (base[0], y, z - length)
            bone = f"{name}_{i:02d}_{_s}"
            BONES.append((bone, head, tail, parent, 0.0))
            parent = bone
            y, z = tail[1], tail[2]
            base = (tail[0], tail[1], tail[2])

# -- jambes ----------------------------------------------------------------
for _s, _x in (("L", 1.0), ("R", -1.0)):
    BONES += [
        (f"upperLeg_{_s}", (_x * X_LEG, 0.0, Z_HIP), (_x * X_LEG, 0.0, Z_KNEE), "pelvis", 0.0),
        (f"lowerLeg_{_s}", (_x * X_LEG, 0.0, Z_KNEE), (_x * X_LEG, 0.0, Z_ANKLE),
         f"upperLeg_{_s}", 0.0),
        (f"foot_{_s}", (_x * X_LEG, 0.0, Z_ANKLE), (_x * X_LEG, -0.115, 0.028),
         f"lowerLeg_{_s}", 0.0),
        (f"toe_{_s}", (_x * X_LEG, -0.115, 0.028), (_x * X_LEG, -0.185, 0.022),
         f"foot_{_s}", 0.0),
    ]

BONE_NAMES = [b[0] for b in BONES]

# Chaînes utiles côté runtime (exportées en extras glTF).
FINGER_CHAINS = {
    f"{name}_{side}": [f"{name}_{i:02d}_{side}" for i in (1, 2, 3)]
    for side in ("L", "R") for name, *_ in FINGERS
}

EYE_BONES = ["eye_L", "eye_R"]

IK_CHAINS = {
    "arm_L": ["upperArm_L", "lowerArm_L", "hand_L"],
    "arm_R": ["upperArm_R", "lowerArm_R", "hand_R"],
    "leg_L": ["upperLeg_L", "lowerLeg_L", "foot_L"],
    "leg_R": ["upperLeg_R", "lowerLeg_R", "foot_R"],
}
