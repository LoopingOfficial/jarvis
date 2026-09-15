"""Analyse REELLE des pixels de l'image de reference (executee dans Blender).

L'analyseur cote hote (`avatar_reference._extract_features`) interroge un LLM
textuel : sans modele configure, il ne renvoie que des champs vides et les
etapes visage / cheveux / tenue n'ont alors rien a appliquer.

Ce module comble ce trou sans nouvelle dependance : Blender sait decoder PNG /
JPEG / WEBP, donc on lit vraiment l'image et on en extrait des mesures :

    dominant_colors   couleurs dominantes du sujet
    hair_color        bande haute du sujet
    skin_tone         bande du visage
    outfit_colors     bande du torse / des jambes
    face_shape        rapport largeur/hauteur mesure sur la silhouette

Tout ce qui n'est pas mesurable reste vide : aucune valeur n'est inventee, et
une etape sans donnee exploitable est explicitement ignoree par le pipeline.
"""
from __future__ import annotations

THUMB = 72          # l'image est reduite avant lecture : analyse quasi instantanee
BG_TOLERANCE = 0.13  # ecart au fond au-dela duquel un pixel appartient au sujet


def _linear_to_srgb(c):
    if c <= 0.0031308:
        return max(0.0, min(1.0, c * 12.92))
    return max(0.0, min(1.0, 1.055 * (c ** (1.0 / 2.4)) - 0.055))


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(int(round(_linear_to_srgb(c) * 255)) for c in rgb)


def _median(values):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _average(pixels):
    if not pixels:
        return None
    n = float(len(pixels))
    return (sum(p[0] for p in pixels) / n,
            sum(p[1] for p in pixels) / n,
            sum(p[2] for p in pixels) / n)


def _dominant(pixels, count=3):
    """Couleurs dominantes par quantification grossiere (4 niveaux par canal)."""
    buckets = {}
    for r, g, b in pixels:
        key = (int(r * 3.99), int(g * 3.99), int(b * 3.99))
        entry = buckets.setdefault(key, [0, 0.0, 0.0, 0.0])
        entry[0] += 1
        entry[1] += r
        entry[2] += g
        entry[3] += b
    ranked = sorted(buckets.values(), key=lambda e: e[0], reverse=True)[:count]
    return [_hex((e[1] / e[0], e[2] / e[0], e[3] / e[0])) for e in ranked]


def analyze(path):
    """Renvoie les champs mesurables de l'image. {} si l'image est illisible."""
    import bpy
    image = None
    try:
        image = bpy.data.images.load(path, check_existing=False)
        width, height = image.size
        if not width or not height:
            return {}
        scale = THUMB / float(max(width, height))
        image.scale(max(8, int(width * scale)), max(8, int(height * scale)))
        width, height = image.size
        raw = list(image.pixels)
    except Exception:
        return {}
    finally:
        if image is not None:
            try:
                bpy.data.images.remove(image)
            except Exception:
                pass

    def at(x, y):
        i = (y * width + x) * 4
        return (raw[i], raw[i + 1], raw[i + 2], raw[i + 3])

    # --- couleur de fond : mediane du pourtour ---------------------------
    border = []
    for x in range(width):
        border.append(at(x, 0))
        border.append(at(x, height - 1))
    for y in range(height):
        border.append(at(0, y))
        border.append(at(width - 1, y))
    background = (_median([p[0] for p in border]),
                  _median([p[1] for p in border]),
                  _median([p[2] for p in border]))

    # --- silhouette du sujet ---------------------------------------------
    subject = []          # (x, y, r, g, b)
    for y in range(height):
        for x in range(width):
            r, g, b, a = at(x, y)
            if a < 0.35:
                continue
            delta = (abs(r - background[0]) + abs(g - background[1])
                     + abs(b - background[2])) / 3.0
            if delta >= BG_TOLERANCE:
                subject.append((x, y, r, g, b))
    if len(subject) < 24:
        # Pas de sujet detachable du fond : on ne mesure que les dominantes.
        allpx = [(p[0], p[1], p[2]) for p in
                 (at(x, y) for y in range(height) for x in range(width))]
        return {"dominant_colors": _dominant(allpx)}

    ys = [p[1] for p in subject]
    xs = [p[0] for p in subject]
    top, bottom = max(ys), min(ys)          # y=0 est le BAS de l'image
    span = max(1, top - bottom)

    def band(lo, hi):
        """Bande horizontale du sujet, en fraction depuis le HAUT."""
        y_hi = top - span * lo
        y_lo = top - span * hi
        return [(p[2], p[3], p[4]) for p in subject if y_lo <= p[1] <= y_hi]

    features = {}
    dominant = _dominant([(p[2], p[3], p[4]) for p in subject])
    if dominant:
        features["dominant_colors"] = dominant

    hair = _average(band(0.0, 0.16))
    if hair:
        features["hair_color"] = _hex(hair)
    skin = _average(band(0.20, 0.34))
    if skin:
        features["skin_tone"] = _hex(skin)
    outfit = _dominant(band(0.45, 0.80), count=2)
    if outfit:
        features["outfit_colors"] = outfit

    # --- forme du visage : rapport largeur/hauteur de la tete -------------
    head = [p for p in subject if p[1] >= top - span * 0.30]
    if len(head) > 12:
        head_w = max(p[0] for p in head) - min(p[0] for p in head) + 1
        head_h = span * 0.30
        ratio = head_w / max(1.0, head_h)
        if ratio >= 0.95:
            features["face_shape"] = "ronde"
        elif ratio <= 0.62:
            features["face_shape"] = "longue"
        else:
            features["face_shape"] = "ovale"
        features["face_ratio"] = round(ratio, 3)

    # --- morphologie : rapport hauteur/largeur du sujet entier ------------
    body_w = max(xs) - min(xs) + 1
    slenderness = span / max(1.0, float(body_w))
    if slenderness >= 2.6:
        features["body_proportions"] = "élancé"
    elif slenderness <= 1.6:
        features["body_proportions"] = "trapu"
    else:
        features["body_proportions"] = "athlétique"
    features["body_ratio"] = round(slenderness, 3)
    return features


def merge(features, measured):
    """Complete `features` avec les mesures, sans jamais écraser une valeur
    déjà fournie (une analyse LLM explicite reste prioritaire)."""
    filled = []
    for key, value in (measured or {}).items():
        current = features.get(key)
        if current in (None, "", [], {}):
            features[key] = value
            filled.append(key)
    return filled
