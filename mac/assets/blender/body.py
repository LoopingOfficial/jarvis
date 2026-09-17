"""Géométrie de l'avatar JARVIS — homme adulte jeune, stylisé premium.

Direction artistique (référence fournie) : visage légèrement allongé, mâchoire
douce, sourcils épais, yeux noisette expressifs, nez fin, cheveux châtains
volumineux vers le haut et balayés sur le côté, chemise claire sous une
surchemise gris bleu aux manches remontées, pantalon sombre, chaussures sobres.

Le crâne est un loft ensuite *sculpté* (orbites, pommettes, arcades, lèvres,
menton) : c'est ce qui fait la différence entre un masque plat et un visage.

Coordonnées Blender : Z vers le haut, personnage tourné vers -Y.
"""
from __future__ import annotations

import math

from mesh_lib import cut_faces, loft, merge, sculpt, sphere
from rig import (FINGERS, X_ARM, X_ELBOW, X_LEG, X_WRIST, Z_ANKLE, Z_CHEST, Z_ELBOW,
                 Z_HEAD, Z_HIP, Z_KNEE, Z_NECK, Z_SHOULDER, Z_SPINE01, Z_SPINE02,
                 Z_WRIST)

SEG = 24            # segments par anneau (corps)
SEG_HEAD = 40       # la tête n'est PAS subdivisée : elle naît déjà dense
SEG_LIMB = 14
SEG_FINGER = 8

# Repères du visage, réutilisés par les shape keys.
HEAD_CENTER = (0.0, 0.008, 1.632)
HAIRLINE_Z = 1.700
FACE_Z_EYE = 1.634
FACE_Z_MOUTH = 1.560
FACE_Z_BROW = 1.666
FACE_Y_FRONT = -0.094
EYE_X = 0.038
EYE_Y = -0.076
EYE_CENTER_L = (EYE_X, EYE_Y, FACE_Z_EYE)
EYE_CENTER_R = (-EYE_X, EYE_Y, FACE_Z_EYE)
MOUTH_CENTER = (0.0, -0.094, FACE_Z_MOUTH)
MOUTH_CORNER_L = (0.030, -0.084, FACE_Z_MOUTH + 0.003)
MOUTH_CORNER_R = (-0.030, -0.084, FACE_Z_MOUTH + 0.003)
BROW_CENTER_L = (0.040, -0.084, FACE_Z_BROW)
BROW_CENTER_R = (-0.040, -0.084, FACE_Z_BROW)

# Profil de contrôle du crâne : (z, rayon X, rayon Y, décalage Y, aplat. arrière)
SKULL_PROFILE = [
    (1.496, 0.028, 0.030, -0.044, 1.00),   # pointe du menton
    (1.512, 0.047, 0.053, -0.040, 1.02),
    (1.530, 0.062, 0.070, -0.031, 1.05),   # mâchoire
    (1.548, 0.073, 0.083, -0.020, 1.08),
    (1.566, 0.084, 0.094, -0.010, 1.11),   # bouche
    (1.584, 0.090, 0.100, -0.002, 1.13),
    (1.602, 0.094, 0.104, 0.004, 1.14),    # pommettes
    (1.620, 0.096, 0.106, 0.008, 1.15),
    (1.638, 0.097, 0.107, 0.010, 1.15),    # yeux
    (1.656, 0.096, 0.106, 0.012, 1.14),
    (1.674, 0.094, 0.104, 0.014, 1.13),    # sourcils / front
    (1.694, 0.087, 0.098, 0.016, 1.12),
    (1.714, 0.082, 0.093, 0.018, 1.10),
    (1.734, 0.073, 0.083, 0.018, 1.08),
    (1.752, 0.059, 0.068, 0.018, 1.06),
    (1.764, 0.038, 0.044, 0.018, 1.04),
    (1.772, 0.016, 0.019, 0.018, 1.03),
]


def _skull_at(z: float):
    """Interpole le profil de contrôle à une hauteur donnée."""
    if z <= SKULL_PROFILE[0][0]:
        return SKULL_PROFILE[0][1:]
    if z >= SKULL_PROFILE[-1][0]:
        return SKULL_PROFILE[-1][1:]
    for a, b in zip(SKULL_PROFILE, SKULL_PROFILE[1:]):
        if a[0] <= z <= b[0]:
            t = (z - a[0]) / (b[0] - a[0])
            return tuple(a[i] + (b[i] - a[i]) * t for i in range(1, 5))
    return SKULL_PROFILE[-1][1:]


def _dense_rings(step: float = 0.0075):
    """Anneaux serrés : sans densité suffisante, lèvres et orbites disparaissent."""
    rings = []
    z = SKULL_PROFILE[0][0]
    top = SKULL_PROFILE[-1][0]
    while z <= top + 1e-6:
        rx, ry, cy, sq = _skull_at(z)
        rings.append({"c": (0.0, cy, z), "rx": rx, "ry": ry, "squash": sq})
        z += step
    return rings


def _skull():
    """Crâne dense, puis sculpté : orbites, arcades, pommettes, lèvres, menton."""
    verts, faces = loft(_dense_rings(), segments=SEG_HEAD, cap_start=True, cap_end=True)
    c = HEAD_CENTER

    for eye in (EYE_CENTER_L, EYE_CENTER_R):
        # Orbite : creusée assez large pour que le globe s'y loge.
        verts = sculpt(verts, (eye[0], eye[1] - 0.006, eye[2]), 0.036, -0.0085, center=c)
        # Pli de la paupière supérieure.
        verts = sculpt(verts, (eye[0], eye[1] - 0.004, eye[2] + 0.017), 0.022, -0.0042,
                       center=c, weights=(0.7, 1.0, 1.4))
        # Coin interne, vers l'arête du nez.
        verts = sculpt(verts, (eye[0] * 0.52, eye[1] - 0.002, eye[2] - 0.002), 0.020,
                       -0.0050, center=c)
        # Léger creux sous l'œil.
        verts = sculpt(verts, (eye[0], eye[1] - 0.002, eye[2] - 0.021), 0.018, -0.0022,
                       center=c)

    for brow in (BROW_CENTER_L, BROW_CENTER_R):
        verts = sculpt(verts, (brow[0], brow[1], brow[2] + 0.002), 0.038, 0.0080,
                       center=c, weights=(0.8, 1.0, 1.2))
    verts = sculpt(verts, (0.0, -0.090, FACE_Z_BROW + 0.006), 0.026, 0.0025, center=c)

    for sx in (1.0, -1.0):
        verts = sculpt(verts, (sx * 0.060, -0.066, 1.604), 0.038, 0.0060, center=c)
        verts = sculpt(verts, (sx * 0.054, -0.074, 1.574), 0.028, -0.0022, center=c)
        verts = sculpt(verts, (sx * 0.040, -0.090, 1.548), 0.020, -0.0022, center=c)

    # --- bouche : sans une fente franche, le visage reste muet --------------
    verts = sculpt(verts, (0.0, -0.098, FACE_Z_MOUTH + 0.010), 0.028, 0.0105,
                   center=c, weights=(0.55, 1.0, 1.1))
    verts = sculpt(verts, (0.0, -0.098, FACE_Z_MOUTH - 0.012), 0.028, 0.0100,
                   center=c, weights=(0.55, 1.0, 1.1))
    # Fente large et fine : anisotrope, sinon elle est noyée par le lissage.
    verts = sculpt(verts, (0.0, -0.100, FACE_Z_MOUTH), 0.026, -0.0080,
                   center=c, weights=(0.40, 0.9, 2.6))
    for corner in (MOUTH_CORNER_L, MOUTH_CORNER_R):
        verts = sculpt(verts, corner, 0.018, -0.0035, center=c)

    verts = sculpt(verts, (0.0, -0.094, FACE_Z_MOUTH + 0.021), 0.016, -0.0022,
                   center=c, weights=(1.6, 1.0, 1.0))
    verts = sculpt(verts, (0.0, -0.088, 1.518), 0.028, 0.0055, center=c)
    verts = sculpt(verts, (0.0, -0.084, 1.536), 0.018, -0.0022, center=c)

    for sx in (1.0, -1.0):
        verts = sculpt(verts, (sx * 0.086, -0.026, 1.686), 0.026, -0.0022, center=c)
    return verts, faces


def head_mesh():
    """Crâne + paupières : tout ce que les blendshapes doivent déformer."""
    return merge(_skull(),
                 _eyelid(1.0, upper=True), _eyelid(-1.0, upper=True),
                 _eyelid(1.0, upper=False), _eyelid(-1.0, upper=False))


def mouth_cavity_mesh():
    """Cavité buccale sombre, juste derrière les lèvres.

    Sans elle, ouvrir la mâchoire (visème A, O…) laisse voir l'intérieur du
    crâne : la bouche paraît déchirée. Avec elle, l'ouverture lit comme une
    vraie bouche.
    """
    cavity = sphere((0.0, -0.062, FACE_Z_MOUTH - 0.004), 0.030, 0.024, 0.022,
                    segments=14, stacks=10)
    # Dents : une simple arcade claire suffit à donner de la lecture.
    upper = loft([
        {"c": (0.0, -0.086, FACE_Z_MOUTH + 0.006), "rx": 0.024, "ry": 0.012},
        {"c": (0.0, -0.086, FACE_Z_MOUTH + 0.001), "rx": 0.024, "ry": 0.012},
    ], segments=12, cap_start=True, cap_end=True)
    return merge(cavity, upper)


def face_parts_mesh():
    """Nez et oreilles, en objet SÉPARÉ.

    Fusionnés au crâne, leurs normales se moyennent avec les siennes et le
    visage se couvre de taches là où les volumes s'interpénètrent. Séparés,
    chaque volume garde son ombrage propre et lit comme une forme nette.
    """
    return merge(_nose(), _ear(1.0), _ear(-1.0))


def _nose():
    """Nez fin : arête étroite depuis la racine, pointe discrète, ailes légères."""
    rings = [
        {"c": (0.0, -0.086, 1.652), "rx": 0.0072, "ry": 0.009},   # racine
        {"c": (0.0, -0.094, 1.638), "rx": 0.0078, "ry": 0.010},
        {"c": (0.0, -0.101, 1.624), "rx": 0.0086, "ry": 0.011},
        {"c": (0.0, -0.107, 1.611), "rx": 0.0098, "ry": 0.012},
        {"c": (0.0, -0.111, 1.602), "rx": 0.0112, "ry": 0.012},   # pointe
        {"c": (0.0, -0.108, 1.595), "rx": 0.0126, "ry": 0.010},   # base
        {"c": (0.0, -0.100, 1.593), "rx": 0.0120, "ry": 0.008},   # ailes
    ]
    return loft(rings, segments=14, cap_start=True, cap_end=True)


def _ear(side: float):
    outer = sphere((side * 0.076, 0.024, 1.624), 0.0055, 0.013, 0.019,
                   segments=12, stacks=9)
    inner = sphere((side * 0.071, 0.022, 1.622), 0.0032, 0.008, 0.011,
                   segments=8, stacks=6)
    return merge(outer, inner)


def _eyelid(side: float, *, upper: bool):
    """Paupière : calotte fine ne couvrant qu'une partie de l'œil.

    Une calotte pleine masquerait le globe : seule la portion haute (ou basse)
    est conservée, le shape key blink la fait descendre sur l'œil.
    """
    cx = side * EYE_X
    cy = EYE_Y + 0.001
    cz = FACE_Z_EYE
    r = 0.0122                                     # juste au-dessus du globe
    rings = []
    if upper:
        angles = [0.02, 0.16, 0.30, 0.42]          # du sommet au bord ciliaire
    else:
        angles = [0.72, 0.84, 0.96]                # bord inférieur, fin
    for t in angles:
        phi = math.pi * t
        z = cz + r * math.cos(phi)
        radius = r * math.sin(phi)
        rings.append({"c": (cx, cy, z), "rx": radius, "ry": radius, "squash": 1.0})
    return loft(rings, segments=12, cap_start=upper, cap_end=not upper)


def eyeball(side: float):
    """Globe : sclère + iris + pupille, légèrement en relief pour capter la lumière."""
    cx = side * EYE_X
    cy = EYE_Y + 0.002
    globe = sphere((cx, cy, FACE_Z_EYE), 0.0112, 0.0112, 0.0112,
                   segments=16, stacks=12)
    iris = sphere((cx, cy - 0.0084, FACE_Z_EYE), 0.0058, 0.0030, 0.0058,
                  segments=14, stacks=7)
    pupil = sphere((cx, cy - 0.0100, FACE_Z_EYE), 0.0026, 0.0016, 0.0026,
                   segments=10, stacks=5)
    return globe, iris, pupil


def brow(side: float):
    """Sourcil épais, arqué, posé sur l'arcade."""
    verts = []
    faces = []
    steps = 11
    top, bottom = [], []
    for i in range(steps):
        t = i / (steps - 1)
        x = side * (0.015 + t * 0.048)
        arch = math.sin(min(1.0, t * 1.15) * math.pi) * 0.0058
        depth = -0.093 + t * 0.020 + (t ** 2) * 0.018
        z = FACE_Z_BROW + arch - t * 0.007
        # Un sourcil épais reste un sourcil : 3 mm, pas 12.
        thickness = 0.0034 * (1.0 - 0.45 * t) + 0.0006
        top.append((x, depth, z + thickness))
        bottom.append((x, depth, z - thickness))
    verts.extend(top)
    verts.extend(bottom)
    for i in range(steps - 1):
        quad = [i, i + 1, steps + i + 1, steps + i]
        faces.append(quad if side > 0 else quad[::-1])
    return verts, faces


def _skull_radius(z: float) -> tuple[float, float, float]:
    """Interpole le profil du crâne — base géométrique de la coiffure."""
    profile = [
        (1.530, 0.062, 0.070, -0.031), (1.566, 0.080, 0.091, -0.010),
        (1.602, 0.090, 0.101, 0.004), (1.638, 0.093, 0.104, 0.010),
        (1.674, 0.090, 0.101, 0.014), (1.714, 0.082, 0.093, 0.018),
        (1.752, 0.059, 0.068, 0.018), (1.772, 0.016, 0.019, 0.018),
    ]
    if z <= profile[0][0]:
        return profile[0][1], profile[0][2], profile[0][3]
    if z >= profile[-1][0]:
        return profile[-1][1], profile[-1][2], profile[-1][3]
    for (z0, rx0, ry0, cy0), (z1, rx1, ry1, cy1) in zip(profile, profile[1:]):
        if z0 <= z <= z1:
            t = (z - z0) / (z1 - z0)
            return (rx0 + (rx1 - rx0) * t, ry0 + (ry1 - ry0) * t, cy0 + (cy1 - cy0) * t)
    return profile[-1][1], profile[-1][2], profile[-1][3]


def _hairline_z(angle: float) -> float:
    """Hauteur du bord de la coiffure selon l'azimut.

    Haute sur le front, plus basse aux tempes, descendante sur la nuque :
    c'est ce bord qui dégage le visage, sans jamais laisser voir l'intérieur
    de la coque (le crâne l'occulte).
    """
    front = math.cos(angle)          # 1 à l'avant (-Y), -1 à l'arrière
    if front > 0:                    # avant : front dégagé
        return 1.700 - 0.052 * (1.0 - front) ** 0.7
    return 1.648 - 0.100 * (-front) ** 0.8


def hair_mesh():
    """Coiffure en volume fermé : coque externe + doublure + bord cousu.

    Une simple calotte ouverte laisserait voir sa face interne dès que le
    visage est dégagé. La doublure évite ce défaut et donne de l'épaisseur.
    """
    segments = 32
    rows = 9

    def surface(angle: float, t: float, offset: float) -> tuple[float, float, float]:
        z0 = _hairline_z(angle)
        z_top = 1.774
        # Progression adoucie : la masse s'accumule vers le sommet.
        e = t * t * (3.0 - 2.0 * t)
        z = z0 + (z_top - z0) * e
        rx, ry, cy = _skull_radius(z)
        shrink = math.sin(math.acos(max(-1.0, min(1.0, (z - z0) / max(1e-4, z_top - z0)))))
        shrink = 0.35 + 0.65 * shrink
        rx = rx * (0.99 + 0.01 * shrink) + offset
        ry = ry * (0.99 + 0.01 * shrink) + offset
        x = -math.sin(angle) * rx
        y = cy - math.cos(angle) * ry
        if y > cy:
            y = cy + (y - cy) * 1.12          # arrière du crâne plus plat
        return (x, y, z)

    def shell(offset: float, flip: bool):
        verts = []
        faces = []
        for i in range(rows + 1):
            t = i / rows
            for j in range(segments):
                angle = 2.0 * math.pi * j / segments
                verts.append(surface(angle, t, offset))
        for i in range(rows):
            for j in range(segments):
                k = (j + 1) % segments
                a = i * segments + j
                b = i * segments + k
                cc = (i + 1) * segments + k
                d = (i + 1) * segments + j
                quad = [a, b, cc, d]
                faces.append(quad[::-1] if flip else quad)
        return verts, faces

    outer = shell(0.006, False)
    inner = shell(0.0015, True)

    # Volume relevé et balayé sur le côté (référence : masse haute, mèche gauche).
    def style(verts):
        verts = sculpt(verts, (0.012, -0.072, 1.742), 0.060, 0.014,
                       direction=(0.20, -0.16, 0.96))
        verts = sculpt(verts, (0.042, -0.038, 1.762), 0.048, 0.010,
                       direction=(0.42, 0.0, 0.91))
        verts = sculpt(verts, (0.036, -0.080, 1.712), 0.048, 0.008,
                       direction=(0.86, -0.32, 0.40))
        return verts

    outer = (style(outer[0]), outer[1])
    inner = (style(inner[0]), inner[1])

    # Couture du bord : la coiffure devient un volume fermé.
    verts, faces = merge(outer, inner)
    n_outer = len(outer[0])
    rim = []
    for j in range(segments):
        k = (j + 1) % segments
        rim.append([j, k, n_outer + k, n_outer + j][::-1])
    faces.extend(rim)
    return verts, faces


# ---------------------------------------------------------------------------
# Corps
# ---------------------------------------------------------------------------
def neck_mesh():
    rings = [
        {"c": (0.0, 0.0, Z_NECK - 0.070), "rx": 0.064, "ry": 0.062},
        {"c": (0.0, -0.002, Z_NECK - 0.032), "rx": 0.050, "ry": 0.050},
        {"c": (0.0, -0.006, Z_NECK + 0.004), "rx": 0.047, "ry": 0.048},
        {"c": (0.0, -0.012, Z_HEAD - 0.008), "rx": 0.050, "ry": 0.054},
        {"c": (0.0, -0.016, Z_HEAD + 0.022), "rx": 0.056, "ry": 0.062},
    ]
    return loft(rings, segments=SEG, cap_start=False, cap_end=False)


def torso_skin_mesh():
    """Buste sous les vêtements — évite toute percée aux jonctions."""
    rings = [
        {"c": (0.0, 0.0, Z_HIP - 0.10), "rx": 0.118, "ry": 0.080},
        {"c": (0.0, 0.0, Z_HIP), "rx": 0.120, "ry": 0.080},
        {"c": (0.0, 0.0, Z_SPINE01), "rx": 0.118, "ry": 0.078},
        {"c": (0.0, 0.0, Z_SPINE02), "rx": 0.124, "ry": 0.082},
        {"c": (0.0, 0.0, Z_CHEST), "rx": 0.132, "ry": 0.086},
        {"c": (0.0, 0.0, Z_CHEST + 0.085), "rx": 0.124, "ry": 0.082},
        {"c": (0.0, 0.0, Z_NECK - 0.030), "rx": 0.086, "ry": 0.068},
    ]
    torso = loft(rings, segments=SEG, cap_start=True, cap_end=True)
    # Épaules : sans deltoïde, le bras reste un tube collé au torse.
    return merge(torso, _deltoid(1.0), _deltoid(-1.0))


def _deltoid(side: float):
    return sphere((side * (X_ARM - 0.024), -0.004, Z_SHOULDER + 0.024),
                  0.054, 0.051, 0.054, segments=SEG_LIMB, stacks=10)


def arm_mesh(side: float):
    """Bras complet (peau), du deltoïde au poignet."""
    x0, x1, x2 = side * X_ARM, side * X_ELBOW, side * X_WRIST
    rings = [
        {"c": (side * (X_ARM - 0.030), -0.004, Z_SHOULDER + 0.055), "rx": 0.058, "ry": 0.056},
        {"c": (x0, -0.004, Z_SHOULDER), "rx": 0.052, "ry": 0.051},
        {"c": (x0 + side * 0.008, -0.004, Z_SHOULDER - 0.090), "rx": 0.044, "ry": 0.044},
        {"c": (x1, -0.004, Z_ELBOW + 0.025), "rx": 0.039, "ry": 0.039},
        {"c": (x1, -0.004, Z_ELBOW), "rx": 0.037, "ry": 0.038},
        {"c": ((x1 + x2) / 2, -0.004, (Z_ELBOW + Z_WRIST) / 2 + 0.02), "rx": 0.036, "ry": 0.036},
        {"c": ((x1 + x2) / 2, -0.004, (Z_ELBOW + Z_WRIST) / 2 - 0.03), "rx": 0.032, "ry": 0.032},
        {"c": (x2, -0.004, Z_WRIST + 0.02), "rx": 0.027, "ry": 0.027},
        {"c": (x2, -0.004, Z_WRIST), "rx": 0.025, "ry": 0.026},
    ]
    return loft(rings, segments=SEG_LIMB, cap_start=False, cap_end=False)


def hand_mesh(side: float):
    """Paume + cinq doigts articulés (3 phalanges chacun)."""
    parts = []
    px = side * (X_WRIST + 0.008)
    pz = Z_WRIST - 0.082
    palm = [
        {"c": (side * X_WRIST, -0.004, Z_WRIST + 0.004), "rx": 0.025, "ry": 0.025},
        {"c": (px, -0.005, Z_WRIST - 0.028), "rx": 0.030, "ry": 0.020},
        {"c": (px, -0.005, Z_WRIST - 0.058), "rx": 0.032, "ry": 0.018},
        {"c": (px, -0.005, pz), "rx": 0.030, "ry": 0.017},
    ]
    parts.append(loft(palm, segments=SEG_LIMB, cap_start=True, cap_end=True))

    for name, across, forward, lengths, _drop in FINGERS:
        if name == "thumb":
            base = (px - side * 0.026, -0.021, pz + 0.050)
            direction = (-side * 0.45, -0.55, -0.55)
            radius = 0.0090
        else:
            base = (px + side * across, -0.005 + forward, pz + 0.004)
            direction = (0.0, 0.0, -1.0)
            radius = 0.0080 if name != "pinky" else 0.0066
        rings = []
        x, y, z = base
        rings.append({"c": (x, y, z), "rx": radius, "ry": radius})
        for k, length in enumerate(lengths):
            x += direction[0] * length
            y += direction[1] * length
            z += direction[2] * length
            shrink = 1.0 - 0.14 * (k + 1)
            rings.append({"c": (x, y, z), "rx": radius * shrink, "ry": radius * shrink})
        parts.append(loft(rings, segments=SEG_FINGER, cap_start=True, cap_end=True))
    return merge(*parts)


def leg_skin_mesh(side: float):
    x = side * X_LEG
    rings = [
        {"c": (x, 0.0, Z_HIP + 0.02), "rx": 0.078, "ry": 0.080},
        {"c": (x, 0.0, (Z_HIP + Z_KNEE) / 2), "rx": 0.064, "ry": 0.068},
        {"c": (x, 0.0, Z_KNEE), "rx": 0.053, "ry": 0.056},
        {"c": (x, -0.004, (Z_KNEE + Z_ANKLE) / 2), "rx": 0.048, "ry": 0.050},
        {"c": (x, 0.0, Z_ANKLE), "rx": 0.034, "ry": 0.036},
    ]
    return loft(rings, segments=SEG_LIMB, cap_start=True, cap_end=True)


# ---------------------------------------------------------------------------
# Vêtements
# ---------------------------------------------------------------------------
def shirt_mesh():
    """Chemise claire, rentrée : elle descend sous la taille du pantalon."""
    rings = [
        {"c": (0.0, 0.0, Z_HIP - 0.120), "rx": 0.116, "ry": 0.080},
        {"c": (0.0, 0.0, Z_HIP - 0.060), "rx": 0.122, "ry": 0.084},
        {"c": (0.0, 0.0, Z_SPINE01), "rx": 0.123, "ry": 0.083},
        {"c": (0.0, 0.0, Z_SPINE02), "rx": 0.129, "ry": 0.087},
        {"c": (0.0, 0.0, Z_CHEST), "rx": 0.141, "ry": 0.093},
        {"c": (0.0, 0.0, Z_CHEST + 0.082), "rx": 0.131, "ry": 0.088},
        {"c": (0.0, -0.002, Z_NECK - 0.040), "rx": 0.098, "ry": 0.078},
    ]
    body_mesh = loft(rings, segments=SEG, cap_start=True, cap_end=False)
    collar = loft([
        {"c": (0.0, -0.004, Z_NECK - 0.044), "rx": 0.078, "ry": 0.073},
        {"c": (0.0, -0.006, Z_NECK - 0.012), "rx": 0.064, "ry": 0.063},
        {"c": (0.0, -0.008, Z_NECK + 0.016), "rx": 0.062, "ry": 0.062},
    ], segments=SEG, cap_start=False, cap_end=False)
    return merge(body_mesh, collar)


def jacket_mesh():
    """Surchemise gris bleu, manches remontées sous le coude."""
    parts = []
    rings = [
        {"c": (0.0, 0.0, Z_HIP - 0.135), "rx": 0.133, "ry": 0.095},
        {"c": (0.0, 0.0, Z_HIP - 0.060), "rx": 0.137, "ry": 0.097},
        {"c": (0.0, 0.0, Z_SPINE01), "rx": 0.134, "ry": 0.094},
        {"c": (0.0, 0.0, Z_SPINE02), "rx": 0.140, "ry": 0.098},
        {"c": (0.0, 0.0, Z_CHEST), "rx": 0.151, "ry": 0.103},
        {"c": (0.0, 0.0, Z_CHEST + 0.080), "rx": 0.142, "ry": 0.097},
        {"c": (0.0, -0.002, Z_NECK - 0.046), "rx": 0.112, "ry": 0.086},
        {"c": (0.0, -0.004, Z_NECK - 0.022), "rx": 0.092, "ry": 0.078},
    ]
    parts.append(loft(rings, segments=SEG, cap_start=True, cap_end=False))

    for side in (1.0, -1.0):
        x0, x1 = side * X_ARM, side * X_ELBOW
        # La première section démarre DANS le torse : aucune couture visible.
        sleeve = [
            {"c": (side * (X_ARM - 0.086), -0.004, Z_SHOULDER + 0.062), "rx": 0.040, "ry": 0.040},
            {"c": (side * (X_ARM - 0.048), -0.004, Z_SHOULDER + 0.046), "rx": 0.064, "ry": 0.062},
            {"c": (side * (X_ARM - 0.026), -0.004, Z_SHOULDER + 0.026), "rx": 0.068, "ry": 0.065},
            {"c": (side * (X_ARM - 0.008), -0.004, Z_SHOULDER + 0.008), "rx": 0.065, "ry": 0.063},
            {"c": (x0, -0.004, Z_SHOULDER - 0.030), "rx": 0.059, "ry": 0.058},
            {"c": (x0 + side * 0.008, -0.004, Z_SHOULDER - 0.090), "rx": 0.053, "ry": 0.053},
            {"c": (x1, -0.004, Z_ELBOW + 0.020), "rx": 0.048, "ry": 0.048},
            {"c": (x1, -0.004, Z_ELBOW - 0.038), "rx": 0.046, "ry": 0.046},
            {"c": (x1, -0.004, Z_ELBOW - 0.052), "rx": 0.050, "ry": 0.050},
        ]
        parts.append(loft(sleeve, segments=SEG_LIMB, cap_start=False, cap_end=True))
    return merge(*parts)


def belt_mesh():
    """Ceinture fine, à peine plus large que le pantalon."""
    rings = [
        {"c": (0.0, 0.0, Z_HIP - 0.084), "rx": 0.129, "ry": 0.092},
        {"c": (0.0, 0.0, Z_HIP - 0.060), "rx": 0.130, "ry": 0.093},
    ]
    band = loft(rings, segments=SEG, cap_start=False, cap_end=False)
    buckle = sphere((0.0, -0.086, Z_HIP - 0.072), 0.017, 0.006, 0.010,
                    segments=10, stacks=5)
    return merge(band, buckle)


def pants_mesh():
    parts = []
    hips = [
        {"c": (0.0, 0.0, Z_HIP - 0.055), "rx": 0.126, "ry": 0.090},
        {"c": (0.0, 0.0, Z_HIP - 0.135), "rx": 0.128, "ry": 0.092},
        {"c": (0.0, 0.0, Z_HIP - 0.215), "rx": 0.124, "ry": 0.090},
    ]
    parts.append(loft(hips, segments=SEG, cap_start=True, cap_end=False))
    for side in (1.0, -1.0):
        x = side * X_LEG
        leg = [
            {"c": (x, 0.0, Z_HIP - 0.205), "rx": 0.081, "ry": 0.085},
            {"c": (x, 0.0, (Z_HIP + Z_KNEE) / 2), "rx": 0.069, "ry": 0.073},
            {"c": (x, 0.0, Z_KNEE), "rx": 0.060, "ry": 0.063},
            {"c": (x, -0.004, (Z_KNEE + Z_ANKLE) / 2), "rx": 0.055, "ry": 0.057},
            {"c": (x, 0.0, Z_ANKLE + 0.030), "rx": 0.047, "ry": 0.049},
        ]
        parts.append(loft(leg, segments=SEG_LIMB, cap_start=False, cap_end=True))
    return merge(*parts)


def shoes_mesh():
    parts = []
    for side in (1.0, -1.0):
        x = side * X_LEG
        shoe = [
            {"c": (x, 0.006, Z_ANKLE + 0.042), "rx": 0.044, "ry": 0.047},
            {"c": (x, 0.000, Z_ANKLE - 0.010), "rx": 0.047, "ry": 0.055},
            {"c": (x, -0.045, 0.030), "rx": 0.048, "ry": 0.070},
            {"c": (x, -0.110, 0.026), "rx": 0.045, "ry": 0.060},
            {"c": (x, -0.165, 0.019), "rx": 0.033, "ry": 0.032},
        ]
        parts.append(loft(shoe, segments=SEG_LIMB, cap_start=False, cap_end=True))
        sole = [
            {"c": (x, -0.030, 0.006), "rx": 0.049, "ry": 0.090},
            {"c": (x, -0.030, 0.020), "rx": 0.050, "ry": 0.092},
        ]
        parts.append(loft(sole, segments=SEG_LIMB, cap_start=True, cap_end=True))
    return merge(*parts)


def accent_mesh():
    """Détail cyan très discret : fin liseré sous le col."""
    rings = [
        {"c": (0.0, -0.004, Z_NECK - 0.026), "rx": 0.086, "ry": 0.074},
        {"c": (0.0, -0.005, Z_NECK - 0.018), "rx": 0.087, "ry": 0.075},
    ]
    return loft(rings, segments=SEG, cap_start=False, cap_end=False)
