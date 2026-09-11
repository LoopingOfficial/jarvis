"""Blendshapes faciaux et visèmes de JARVIS.

Chaque clé est une déformation analytique du maillage de tête : un ancrage,
un rayon d'influence et un déplacement lissé. Aucune dépendance à une
topologie importée — les clés restent donc reproductibles à l'identique.

Deux familles :
  - expressions (ARKit-like) : blink, brow, squint, jaw, mouth…
  - visèmes : REST A E I O U MBP FV L WQ CH TH
Les deux se cumulent librement à l'exécution (lip sync + émotion).
"""
from __future__ import annotations

import math

from body import (BROW_CENTER_L, BROW_CENTER_R, EYE_CENTER_L, EYE_CENTER_R,
                  FACE_Y_FRONT, FACE_Z_MOUTH, MOUTH_CENTER, MOUTH_CORNER_L,
                  MOUTH_CORNER_R)
from mesh_lib import falloff

Vec = tuple[float, float, float]


def _dist(a: Vec, b: Vec, weights: Vec = (1.0, 1.0, 1.0)) -> float:
    return math.sqrt(sum(((a[i] - b[i]) * weights[i]) ** 2 for i in range(3)))


def _apply(verts: list[Vec], anchor: Vec, radius: float, offset: Vec,
           weights: Vec = (1.0, 1.0, 1.0), out: list[Vec] | None = None) -> list[Vec]:
    """Ajoute un déplacement lissé autour de `anchor`."""
    out = out if out is not None else [tuple(v) for v in verts]
    for i, v in enumerate(verts):
        w = falloff(_dist(v, anchor, weights), radius)
        if w <= 0.0:
            continue
        o = out[i]
        out[i] = (o[0] + offset[0] * w, o[1] + offset[1] * w, o[2] + offset[2] * w)
    return out


def _jaw_rotate(verts: list[Vec], amount: float, out: list[Vec] | None = None) -> list[Vec]:
    """Ouverture de mâchoire : rotation des sommets bas du visage autour de l'axe X."""
    out = out if out is not None else [tuple(v) for v in verts]
    pivot_z = 1.598
    pivot_y = 0.030
    angle = math.radians(16.0) * amount
    for i, v in enumerate(verts):
        # Influence progressive du haut de la bouche jusqu'au menton.
        w = 0.0
        if v[2] < pivot_z:
            w = min(1.0, (pivot_z - v[2]) / 0.100)
            w = w * w * (3 - 2 * w)
        if w <= 0.0:
            continue
        dy = v[1] - pivot_y
        dz = v[2] - pivot_z
        ca, sa = math.cos(angle * w), math.sin(angle * w)
        ny = pivot_y + dy * ca - dz * sa
        nz = pivot_z + dy * sa + dz * ca
        o = out[i]
        out[i] = (o[0], o[1] + (ny - v[1]), o[2] + (nz - v[2]))
    return out


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------
def _blink(verts, side: float):
    """Paupière : la calotte descend et s'aplatit sur l'œil."""
    eye = EYE_CENTER_L if side > 0 else EYE_CENTER_R
    out = [tuple(v) for v in verts]
    lid_top = (eye[0], eye[1] - 0.002, eye[2] + 0.016)
    _apply(verts, lid_top, 0.026, (0.0, -0.002, -0.020), out=out)
    _apply(verts, (eye[0], eye[1] - 0.002, eye[2] + 0.004), 0.020,
           (0.0, -0.001, -0.008), out=out)
    return out


def _eye_wide(verts, side: float):
    eye = EYE_CENTER_L if side > 0 else EYE_CENTER_R
    out = [tuple(v) for v in verts]
    _apply(verts, (eye[0], eye[1], eye[2] + 0.014), 0.026, (0.0, 0.0, 0.007), out=out)
    _apply(verts, (eye[0], eye[1], eye[2] - 0.013), 0.020, (0.0, 0.0, -0.004), out=out)
    return out


def _squint(verts, side: float):
    eye = EYE_CENTER_L if side > 0 else EYE_CENTER_R
    out = [tuple(v) for v in verts]
    _apply(verts, (eye[0], eye[1], eye[2] - 0.014), 0.024, (0.0, -0.002, 0.008), out=out)
    _apply(verts, (eye[0], eye[1], eye[2] + 0.014), 0.022, (0.0, 0.0, -0.005), out=out)
    return out


def _brow(verts, side: float | None, amount: float):
    out = [tuple(v) for v in verts]
    anchors = []
    if side is None or side > 0:
        anchors.append(BROW_CENTER_L)
    if side is None or side < 0:
        anchors.append(BROW_CENTER_R)
    for a in anchors:
        _apply(verts, a, 0.048, (0.0, 0.0, 0.014 * amount), out=out)
        _apply(verts, (a[0], a[1], a[2] + 0.020), 0.040, (0.0, 0.0, 0.006 * amount), out=out)
    return out


def _smile(verts, side: float | None, strength: float = 1.0):
    out = [tuple(v) for v in verts]
    corners = []
    if side is None or side > 0:
        corners.append(MOUTH_CORNER_L)
    if side is None or side < 0:
        corners.append(MOUTH_CORNER_R)
    for c in corners:
        pull = 1.0 if c[0] > 0 else -1.0
        _apply(verts, c, 0.036,
               (pull * 0.009 * strength, 0.004 * strength, 0.011 * strength), out=out)
        # Pommette qui se soulève : c'est ce qui rend un sourire crédible.
        cheek = (c[0] * 1.35, c[1] + 0.014, c[2] + 0.036)
        _apply(verts, cheek, 0.040, (0.0, -0.004 * strength, 0.006 * strength), out=out)
    return out


def _frown(verts):
    out = [tuple(v) for v in verts]
    for c in (MOUTH_CORNER_L, MOUTH_CORNER_R):
        pull = 1.0 if c[0] > 0 else -1.0
        _apply(verts, c, 0.034, (pull * 0.002, 0.002, -0.011), out=out)
    return out


def _mouth_pucker(verts):
    out = [tuple(v) for v in verts]
    for c in (MOUTH_CORNER_L, MOUTH_CORNER_R):
        pull = -1.0 if c[0] > 0 else 1.0
        _apply(verts, c, 0.034, (pull * 0.013, -0.008, 0.0), out=out)
    _apply(verts, MOUTH_CENTER, 0.026, (0.0, -0.012, 0.0), out=out)
    return out


def _mouth_funnel(verts):
    out = [tuple(v) for v in verts]
    _apply(verts, MOUTH_CENTER, 0.030, (0.0, -0.009, 0.0), out=out)
    _apply(verts, (MOUTH_CENTER[0], MOUTH_CENTER[1], MOUTH_CENTER[2] + 0.012), 0.022,
           (0.0, -0.004, 0.004), out=out)
    _apply(verts, (MOUTH_CENTER[0], MOUTH_CENTER[1], MOUTH_CENTER[2] - 0.012), 0.022,
           (0.0, -0.004, -0.005), out=out)
    for c in (MOUTH_CORNER_L, MOUTH_CORNER_R):
        pull = -1.0 if c[0] > 0 else 1.0
        _apply(verts, c, 0.030, (pull * 0.009, -0.004, 0.0), out=out)
    return out


def _mouth_shift(verts, direction: float):
    out = [tuple(v) for v in verts]
    _apply(verts, MOUTH_CENTER, 0.048, (direction * 0.011, 0.0, 0.0), out=out)
    return out


def _mouth_press(verts):
    """Lèvres serrées — base des consonnes M / B / P."""
    out = [tuple(v) for v in verts]
    _apply(verts, (MOUTH_CENTER[0], MOUTH_CENTER[1], MOUTH_CENTER[2] + 0.006), 0.030,
           (0.0, 0.0, -0.004), out=out)
    _apply(verts, (MOUTH_CENTER[0], MOUTH_CENTER[1], MOUTH_CENTER[2] - 0.007), 0.030,
           (0.0, 0.0, 0.004), out=out)
    _apply(verts, MOUTH_CENTER, 0.026, (0.0, 0.002, 0.0), out=out)
    return out


def _lip_bite(verts):
    """Lèvre inférieure rentrée sous les dents — F / V."""
    out = [tuple(v) for v in verts]
    _apply(verts, (MOUTH_CENTER[0], MOUTH_CENTER[1], MOUTH_CENTER[2] - 0.008), 0.026,
           (0.0, 0.006, 0.005), out=out)
    return out


def _mouth_wide(verts, amount: float = 1.0):
    """Étirement horizontal — voyelles E / I."""
    out = [tuple(v) for v in verts]
    for c in (MOUTH_CORNER_L, MOUTH_CORNER_R):
        pull = 1.0 if c[0] > 0 else -1.0
        _apply(verts, c, 0.034, (pull * 0.011 * amount, 0.003 * amount, 0.0), out=out)
    return out


def _tongue_tip(verts):
    """Langue vers les dents — L / TH : la lèvre supérieure se relève un peu."""
    out = [tuple(v) for v in verts]
    _apply(verts, (MOUTH_CENTER[0], MOUTH_CENTER[1] - 0.002, MOUTH_CENTER[2] + 0.006),
           0.024, (0.0, -0.003, 0.004), out=out)
    return out


def _combine(verts, *layers):
    """Applique plusieurs déformations en cascade sur la même base."""
    out = [tuple(v) for v in verts]
    for fn in layers:
        result = fn(verts)
        for i, r in enumerate(result):
            o, b = out[i], verts[i]
            out[i] = (o[0] + (r[0] - b[0]), o[1] + (r[1] - b[1]), o[2] + (r[2] - b[2]))
    return out


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------
EXPRESSIONS = {
    "smile": lambda v: _smile(v, None),
    "smileLeft": lambda v: _smile(v, 1.0),
    "smileRight": lambda v: _smile(v, -1.0),
    "frown": _frown,
    "browUp": lambda v: _brow(v, None, 1.0),
    "browDown": lambda v: _brow(v, None, -0.7),
    "browUpLeft": lambda v: _brow(v, 1.0, 1.0),
    "browUpRight": lambda v: _brow(v, -1.0, 1.0),
    "blinkLeft": lambda v: _blink(v, 1.0),
    "blinkRight": lambda v: _blink(v, -1.0),
    "eyeWideLeft": lambda v: _eye_wide(v, 1.0),
    "eyeWideRight": lambda v: _eye_wide(v, -1.0),
    "squintLeft": lambda v: _squint(v, 1.0),
    "squintRight": lambda v: _squint(v, -1.0),
    "jawOpen": lambda v: _jaw_rotate(v, 1.0),
    "mouthSmile": lambda v: _smile(v, None, 0.7),
    "mouthFrown": _frown,
    "mouthPucker": _mouth_pucker,
    "mouthFunnel": _mouth_funnel,
    "mouthLeft": lambda v: _mouth_shift(v, 1.0),
    "mouthRight": lambda v: _mouth_shift(v, -1.0),
    "mouthPress": _mouth_press,
    "cheekPuff": lambda v: _apply(v, (0.052, -0.060, 1.560), 0.045, (0.010, -0.006, 0.0)),
}

# Visèmes : combinaisons calibrées pour le français.
VISEMES = {
    "viseme_REST": lambda v: [tuple(x) for x in v],
    "viseme_A": lambda v: _combine(v, lambda b: _jaw_rotate(b, 0.85),
                                   lambda b: _mouth_wide(b, 0.35)),
    "viseme_E": lambda v: _combine(v, lambda b: _jaw_rotate(b, 0.42),
                                   lambda b: _mouth_wide(b, 0.85)),
    "viseme_I": lambda v: _combine(v, lambda b: _jaw_rotate(b, 0.20),
                                   lambda b: _mouth_wide(b, 1.0)),
    "viseme_O": lambda v: _combine(v, lambda b: _jaw_rotate(b, 0.55), _mouth_funnel),
    "viseme_U": lambda v: _combine(v, lambda b: _jaw_rotate(b, 0.22), _mouth_pucker),
    "viseme_MBP": _mouth_press,
    "viseme_FV": lambda v: _combine(v, lambda b: _jaw_rotate(b, 0.14), _lip_bite),
    "viseme_L": lambda v: _combine(v, lambda b: _jaw_rotate(b, 0.34), _tongue_tip),
    "viseme_WQ": lambda v: _combine(v, lambda b: _jaw_rotate(b, 0.18), _mouth_pucker),
    "viseme_CH": lambda v: _combine(v, lambda b: _jaw_rotate(b, 0.24), _mouth_funnel,
                                    lambda b: _mouth_wide(b, 0.25)),
    "viseme_TH": lambda v: _combine(v, lambda b: _jaw_rotate(b, 0.28), _tongue_tip,
                                    lambda b: _mouth_wide(b, 0.3)),
}

ALL_KEYS = {**EXPRESSIONS, **VISEMES}
