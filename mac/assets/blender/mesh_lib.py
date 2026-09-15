"""Primitives de maillage pour l'avatar JARVIS.

Tout est loft (tube à sections variables) : torse, membres, cou, doigts, tête.
Un seul générateur donne une silhouette organique cohérente et un contrôle
analytique total des sommets — indispensable pour poser les poids de skinning
et les shape keys sans dépendre d'un maillage importé.
"""
from __future__ import annotations

import math

Vec = tuple[float, float, float]


def _ring(cx: float, cy: float, cz: float, rx: float, ry: float, segments: int,
          squash_back: float = 1.0) -> list[Vec]:
    """Anneau elliptique dans le plan XY, à la hauteur cz.

    `squash_back` aplatit la moitié arrière (y > 0) : utile pour l'arrière du
    crâne, le dos et les doigts.
    """
    out: list[Vec] = []
    for i in range(segments):
        a = 2.0 * math.pi * i / segments
        x = math.cos(a) * rx
        y = math.sin(a) * ry
        if y > 0:
            y *= squash_back
        out.append((cx + x, cy + y, cz))
    return out


def loft(rings: list[dict], segments: int = 16, cap_start: bool = True,
         cap_end: bool = True) -> tuple[list[Vec], list[list[int]]]:
    """Construit un tube à partir de sections successives.

    Chaque section : {"c": (x, y, z), "rx": r, "ry": r, "squash": 1.0,
                      "axis": "z"|"y"} — `axis` indique la direction d'empilement.
    """
    verts: list[Vec] = []
    faces: list[list[int]] = []
    ring_starts: list[int] = []

    for ring in rings:
        cx, cy, cz = ring["c"]
        rx = ring["rx"]
        ry = ring.get("ry", rx)
        squash = ring.get("squash", 1.0)
        axis = ring.get("axis", "z")
        start = len(verts)
        ring_starts.append(start)
        for i in range(segments):
            a = 2.0 * math.pi * i / segments
            u = math.cos(a) * rx
            v = math.sin(a) * ry
            if v > 0:
                v *= squash
            if axis == "z":                      # section horizontale (empilement vertical)
                verts.append((cx + u, cy + v, cz))
            elif axis == "y":                    # section verticale (empilement en Y)
                verts.append((cx + u, cy, cz + v))
            else:                                # "x" : empilement latéral
                verts.append((cx, cy + u, cz + v))

    for k in range(len(rings) - 1):
        a0 = ring_starts[k]
        b0 = ring_starts[k + 1]
        for i in range(segments):
            j = (i + 1) % segments
            faces.append([a0 + i, a0 + j, b0 + j, b0 + i])

    if cap_start and rings:
        faces.append(list(range(ring_starts[0], ring_starts[0] + segments))[::-1])
    if cap_end and rings:
        last = ring_starts[-1]
        faces.append(list(range(last, last + segments)))
    return verts, faces


def sphere(center: Vec, rx: float, ry: float, rz: float, segments: int = 16,
           stacks: int = 12, squash_back: float = 1.0) -> tuple[list[Vec], list[list[int]]]:
    """Ellipsoïde fermé (tête, yeux, épaules, bouts de doigts)."""
    rings = []
    for s in range(1, stacks):
        t = s / stacks                                  # 0 → 1 du bas vers le haut
        phi = math.pi * t
        z = center[2] - rz * math.cos(phi)
        r = math.sin(phi)
        rings.append({"c": (center[0], center[1], z), "rx": rx * r, "ry": ry * r,
                      "squash": squash_back})
    verts, faces = loft(rings, segments=segments, cap_start=False, cap_end=False)
    bottom = len(verts)
    verts.append((center[0], center[1], center[2] - rz))
    top = len(verts)
    verts.append((center[0], center[1], center[2] + rz))
    for i in range(segments):
        j = (i + 1) % segments
        faces.append([bottom, j, i])
        last = (len(rings) - 1) * segments
        faces.append([top, last + i, last + j])
    return verts, faces


def merge(*parts) -> tuple[list[Vec], list[list[int]]]:
    """Concatène plusieurs (verts, faces) en un seul maillage."""
    verts: list[Vec] = []
    faces: list[list[int]] = []
    for pv, pf in parts:
        offset = len(verts)
        verts.extend(pv)
        faces.extend([[i + offset for i in f] for f in pf])
    return verts, faces


def transform(part, *, scale: Vec = (1, 1, 1), translate: Vec = (0, 0, 0),
              mirror_x: bool = False):
    """Mise à l'échelle / translation / miroir d'un maillage déjà construit."""
    pv, pf = part
    sx, sy, sz = scale
    tx, ty, tz = translate
    m = -1.0 if mirror_x else 1.0
    verts = [((x * sx) * m + tx, y * sy + ty, z * sz + tz) for (x, y, z) in pv]
    faces = [f[::-1] for f in pf] if mirror_x else [list(f) for f in pf]
    return verts, faces


def smoothstep(edge0: float, edge1: float, x: float) -> float:
    if edge1 <= edge0:
        return 0.0 if x < edge0 else 1.0
    t = max(0.0, min(1.0, (x - edge0) / (edge1 - edge0)))
    return t * t * (3.0 - 2.0 * t)


def cut_faces(part, predicate) -> tuple[list[Vec], list[list[int]]]:
    """Supprime les faces dont le centre vérifie `predicate` — sert à dégager
    le visage de la calotte capillaire (une coiffure ne couvre pas les yeux)."""
    verts, faces = part
    kept = []
    for f in faces:
        cx = sum(verts[i][0] for i in f) / len(f)
        cy = sum(verts[i][1] for i in f) / len(f)
        cz = sum(verts[i][2] for i in f) / len(f)
        if not predicate(cx, cy, cz):
            kept.append(list(f))
    used = sorted({i for f in kept for i in f})
    remap = {old: new for new, old in enumerate(used)}
    return [verts[i] for i in used], [[remap[i] for i in f] for f in kept]


def sculpt(verts: list[Vec], anchor: Vec, radius: float, amount: float, *,
           center: Vec | None = None, direction: Vec | None = None,
           weights: Vec = (1.0, 1.0, 1.0)) -> list[Vec]:
    """Creuse ou gonfle une zone — orbites, pommettes, arcades, lèvres.

    Sans `direction`, le déplacement est radial depuis `center` : c'est ce qui
    donne des creux et des reliefs crédibles sur un crâne.
    """
    out = []
    for v in verts:
        d = math.sqrt(sum(((v[i] - anchor[i]) * weights[i]) ** 2 for i in range(3)))
        w = falloff(d, radius)
        if w <= 0.0:
            out.append(v)
            continue
        if direction is not None:
            dx, dy, dz = direction
        else:
            c = center or (0.0, 0.0, 0.0)
            dx, dy, dz = v[0] - c[0], v[1] - c[1], v[2] - c[2]
            length = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
            dx, dy, dz = dx / length, dy / length, dz / length
        out.append((v[0] + dx * amount * w, v[1] + dy * amount * w, v[2] + dz * amount * w))
    return out


def falloff(distance: float, radius: float) -> float:
    """Atténuation lisse 1 → 0 sur `radius`. Base des shape keys."""
    if radius <= 0:
        return 0.0
    return smoothstep(1.0, 0.0, min(1.0, distance / radius))
