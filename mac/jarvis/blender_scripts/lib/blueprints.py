"""Gammes de pièces pour les modèles paramétriques (composées de primitives réelles).

Chaque blueprint est une liste de pièces dict avec : shape, name, size, scale,
location, rotation, material (nom de preset ou dictionnaire). Toutes les pièces
sont de vraies primitives Blender : la géométrie est réelle, jamais simulée.
"""
from __future__ import annotations

import math

from materials import resolve_merge, apply_to_object  # noqa: F401
from geometry import create_primitive, add_text  # noqa: F401

_P = {
    "tabletop": {"shape": "cube", "size": 0.4, "scale": (3.0, 1.9, 0.14),
                 "location": (0.0, 0.0, 0.78), "material": "wood"},
    "tleg": {"shape": "cylinder", "size": 0.6, "scale": (0.1, 0.1, 1.35),
             "location": (0.0, 0.0, 0.38), "material": "wood"},
    "chairseat": {"shape": "cube", "size": 0.4, "scale": (1.5, 1.4, 0.08),
                  "location": (0.0, 0.0, 0.62), "material": "fabric"},
    "chairback": {"shape": "cube", "size": 0.4, "scale": (1.4, 0.08, 1.3),
                  "location": (0.0, -0.52, 1.1), "material": "fabric"},
    "shelfboard": {"shape": "cube", "size": 0.4, "scale": (2.0, 0.9, 0.07),
                   "material": "wood"},
    "shelfleg": {"shape": "cube", "size": 0.4, "scale": (0.08, 0.9, 1.6),
                 "material": "wood"},
    "bottlebody": {"shape": "cylinder", "size": 0.6, "scale": (0.3, 0.3, 1.2),
                   "location": (0.0, 0.0, 0.4), "material": "glass"},
    "bottleneck": {"shape": "cylinder", "size": 0.5, "scale": (0.12, 0.12, 0.5),
                   "location": (0.0, 0.0, 1.05), "material": "glass"},
    "cubebox": {"shape": "cube", "size": 0.4,
                "material": "stylized"},
}


def _v(part: dict, key: str, default):
    return part.get(key, default)


def _l(part: dict) -> tuple:
    return tuple(float(x) for x in _v(part, "location", (0.0, 0.0, 0.0)))


def _r(part: dict) -> tuple:
    return tuple(float(x) for x in _v(part, "rotation", (0.0, 0.0, 0.0)))


def _s(part: dict):
    val = _v(part, "scale", None)
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return (float(val), float(val), float(val))
    return tuple(float(x) for x in val)


def blueprint(kind: str) -> list[dict] | None:
    kind = str(kind or "").lower().split()[0] if kind else ""
    factory = _BLUEPRINTS.get(kind)
    if not factory:
        return None
    return factory()


def build_parts(parts: list[dict], default_material="stylized") -> list:
    """Construit les pièces réelles, retourne les objets créés."""
    created = []
    for i, part in enumerate(parts or []):
        shape = str(_v(part, "shape", "cube"))
        name = str(_v(part, "name", f"Piece_{i + 1}"))
        material = _v(part, "material", None)
        kind_text = shape == "text"
        if kind_text:
            obj = add_text(str(_v(part, "text", name)), size=float(_v(part, "size", 0.3)),
                           extrude=float(_v(part, "extrude", 0.03)), location=_l(part),
                           name=name)
        else:
            obj = create_primitive(shape, scale=_s(part), size=float(_v(part, "size", 1.0)),
                                   location=_l(part), name=name)
            obj.rotation_euler = _r(part)
        merged = resolve_merge(material if isinstance(material, dict)
                               else {"preset": material} if material
                               else {"preset": default_material})
        if merged:
            apply_to_object(obj, color=merged.get("color", "6e5f7d"),
                            metallic=float(merged.get("metallic", 0.0)),
                            roughness=float(merged.get("roughness", 0.5)),
                            emission_strength=float(merged.get("emission_strength", 0.0)),
                            emission_color=merged.get("emission_color", merged.get("color")),
                            name=f"Mat_{name}")
        created.append(obj)
    return created


# ---------------------------------------------------------------------------
# Blueprints
# ---------------------------------------------------------------------------
def _lamp():
    return [
        {"shape": "cylinder", "name": "Base", "size": 1.0, "scale": (0.8, 0.8, 0.1),
         "location": (0.0, 0.0, 0.1), "material": {"preset": "metal", "color": "8a8f9c"}},
        {"shape": "cylinder", "name": "Pied", "size": 0.05, "scale": (1.0, 1.0, 1.4),
         "location": (0.0, 0.0, 0.7), "material": {"preset": "chrome"}},
        {"shape": "cone", "name": "Abat_jour", "size": 1.0, "scale": (0.55, 0.55, 1.0),
         "location": (0.0, 0.0, 1.45), "material": {"preset": "gold", "color": "ffd166"}},
        {"shape": "sphere", "name": "Ampoule", "size": 1.0, "scale": (0.18, 0.18, 0.18),
         "location": (0.0, 0.0, 1.32), "material": {"preset": "emissive", "color": "fff2b0"}},
    ]


def _table():
    parts = [_P["tabletop"], *_l_rect(2, "tleg", 1.0, 0.65, 0.4)]
    return parts


def _l_rect(count_x: int, piece_key: str, span_x: float, span_y: float, y_off: float = 0.0):
    """Place les pièces d'un jeu aux 4 coins (pattern table/chaise)."""
    outs = []
    for xi in range(count_x):
        for yi in range(2):
            x = ((0.65 if xi == 0 else -0.65) * span_x / 0.65)
            y = ((0.4 if yi == 0 else -0.4) * span_y / 0.4)
            p = dict(_P[piece_key])
            p["location"] = (x, y + y_off, p.get("location", (0, 0, 0))[2])
            outs.append(p)
    return outs


def _chair():
    seat = dict(_P["chairseat"])
    back = dict(_P["chairback"])
    legs = []
    for x, y in ((0.55, 0.5), (0.55, -0.5), (-0.55, 0.5), (-0.55, -0.5)):
        legs.append({"shape": "cylinder", "name": "Pied", "size": 0.4,
                     "scale": (0.07, 0.07, 1.15), "location": (x, y, 0.3),
                     "material": "wood"})
    return [seat, back, *legs]


def _shelf():
    return [
        {**_P["shelfleg"], "location": (0.75, 0.0, 0.72), "name": "MontantG"},
        {**_P["shelfleg"], "location": (-0.75, 0.0, 0.72), "name": "MontantD"},
        {**_P["shelfboard"], "location": (0.0, 0.0, 1.4), "name": "TabletteH"},
        {**_P["shelfboard"], "location": (0.0, 0.0, 0.05), "name": "TabletteB"},
    ]


def _bottle():
    return [{**_P["bottlebody"], "name": "Corps"}, {**_P["bottleneck"], "name": "Goulot"}]


def _can():
    return [
        {"shape": "cylinder", "name": "Boite", "size": 0.7, "scale": (0.45, 0.45, 1.3),
         "location": (0.0, 0.0, 0.45), "material": {"preset": "metal", "color": "d8dee8"}},
        {"shape": "cylinder", "name": "Languette", "size": 0.5, "scale": (0.12, 0.12, 0.06),
         "location": (0.0, 0.0, 0.94), "material": "silver"},
    ]


def _cup():
    return [
        {"shape": "cylinder", "name": "Corps", "size": 0.7, "scale": (0.55, 0.55, 0.7),
         "location": (0.0, 0.0, 0.25), "material": {"preset": "ceramic"}},
        {"shape": "torus", "name": "Anse", "size": 0.4, "scale": (0.18, 0.45, 0.18),
         "location": (0.52, 0.0, 0.32), "rotation": (0.0, math.pi / 2, 0.0),
         "material": {"preset": "ceramic"}},
    ]


def _vase():
    return [
        {"shape": "cylinder", "name": "Corps", "size": 0.9, "scale": (0.55, 0.55, 1.2),
         "location": (0.0, 0.0, 0.4), "material": {"preset": "glass"}},
        {"shape": "cylinder", "name": "Col", "size": 0.5, "scale": (0.28, 0.28, 0.4),
         "location": (0.0, 0.0, 1.0), "material": {"preset": "glass"}},
    ]


def _phone():
    return [
        {"shape": "cube", "name": "Corps", "size": 0.4, "scale": (0.75, 1.5, 0.08),
         "location": (0.0, 0.0, 0.04), "material": {"preset": "plastic", "color": "2a2d36"}},
        {"shape": "cube", "name": "Ecran", "size": 0.4, "scale": (0.68, 1.38, 0.02),
         "location": (0.0, 0.0, 0.1), "material": {"preset": "emissive", "color": "7fd8ff"}},
    ]


def _laptop():
    return [
        {"shape": "cube", "name": "Base", "size": 0.5, "scale": (1.6, 1.0, 0.05),
         "location": (0.0, 0.0, 0.02), "material": {"preset": "metal", "color": "5d6570"}},
        {"shape": "cube", "name": "Ecran", "size": 0.3, "scale": (0.05, 1.0, 1.1),
         "location": (0.0, -0.45, 0.25), "rotation": (0.0, math.pi / 3, 0.0),
         "material": {"preset": "plastic", "color": "3a3f4d"}},
        {"shape": "cube", "name": "LumiereEcran", "size": 0.3, "scale": (0.02, 0.9, 0.8),
         "location": (0.0, -0.25, 0.35), "rotation": (0.0, math.pi / 3, 0.0),
         "material": {"preset": "emissive", "color": "bfe3ff"}},
    ]


def _monitor():
    return [
        {"shape": "cylinder", "name": "Pied", "size": 0.6, "scale": (0.45, 0.45, 0.06),
         "location": (0.0, 0.0, 0.03), "material": "plastic"},
        {"shape": "cylinder", "name": "Tige", "size": 0.05, "scale": (1.0, 1.0, 1.6),
         "location": (0.0, 0.0, 0.42), "material": "plastic"},
        {"shape": "cube", "name": "Chassis", "size": 0.7, "scale": (2.4, 0.5, 1.4),
         "location": (0.0, 0.0, 0.92), "material": {"preset": "plastic", "color": "22242b"}},
        {"shape": "cube", "name": "Ecran", "size": 0.7, "scale": (2.3, 0.08, 1.28),
         "location": (0.0, 0.21, 0.96), "material": {"preset": "emissive", "color": "7fd8ff"}},
    ]


def _drone():
    arms = []
    for x, y, ang in ((1.0, 1.0, math.pi / 4), (1.0, -1.0, -math.pi / 4),
                      (-1.0, 1.0, -math.pi / 4), (-1.0, -1.0, math.pi / 4)):
        arms.append({"shape": "cylinder", "name": f"Bras_{x:.0f}{y:.0f}", "size": 0.5,
                     "scale": (0.06, 1.2, 0.06), "location": (x * 0.45, y * 0.45, 0.1),
                     "rotation": (0.0, 0.0, ang), "material": {"preset": "metal", "color": "7d8491"}})
        arms.append({"shape": "cylinder", "name": f"Rotor_{x:.0f}{y:.0f}", "size": 0.9,
                     "scale": (0.5, 0.5, 0.05), "location": (x * 0.9, y * 0.9, 0.14),
                     "material": {"preset": "rubber", "color": "14161c"}})
    body = {"shape": "cube", "name": "Corps", "size": 0.4, "scale": (0.8, 0.8, 0.24),
            "location": (0.0, 0.0, 0.14), "material": {"preset": "plastic", "color": "3a3f4d"}}
    cam = {"shape": "sphere", "name": "Camera", "size": 1.0, "scale": (0.16, 0.16, 0.22),
           "location": (0.0, 0.28, 0.06), "material": {"preset": "glass"}}
    return [body, cam, *arms]


def _rocket():
    fins = []
    for x, y in ((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)):
        fins.append({"shape": "cube", "name": "Aileron", "size": 0.4,
                     "scale": (0.14, 0.8, 0.7), "location": (x * 0.45, y * 0.45, 0.35),
                     "material": {"preset": "metal", "color": "c0392b"}})
    return [
        {"shape": "cylinder", "name": "Corps", "size": 1.4, "scale": (0.6, 0.6, 2.4),
         "location": (0.0, 0.0, 0.9), "material": {"preset": "metal", "color": "dfe6ea"}},
        {"shape": "cone", "name": "Nez", "size": 0.7, "scale": (0.6, 0.6, 1.0),
         "location": (0.0, 0.0, 2.45), "material": {"preset": "metal", "color": "c0392b"}},
        {"shape": "cylinder", "name": "Tuyere", "size": 0.6, "scale": (0.5, 0.5, 0.4),
         "location": (0.0, 0.0, -0.1), "material": {"preset": "metal", "color": "4a4f5c"}},
        *fins,
    ]


def _building():
    return [
        {"shape": "cube", "name": "Tour", "size": 1.2, "scale": (2.4, 2.4, 4.0),
         "location": (0.0, 0.0, 2.0), "material": {"preset": "concrete", "color": "9aa0ab"}},
        {"shape": "plane", "name": "Toit", "size": 1.0, "scale": (2.5, 2.5, 1.0),
         "location": (0.0, 0.0, 4.01), "material": {"preset": "metal", "color": "5d6570"}},
    ]


def _tree():
    return [
        {"shape": "cylinder", "name": "Tronc", "size": 1.0, "scale": (0.9, 0.9, 2.2),
         "location": (0.0, 0.0, 1.1), "material": {"preset": "wood"}},
        {"shape": "icosphere", "name": "Houppier", "size": 1.2, "scale": (2.2, 2.2, 1.6),
         "location": (0.0, 0.0, 2.8), "material": {"preset": "stylized", "color": "2f9e57"}},
    ]


def _gear():
    return [
        {"shape": "torus", "name": "Couronne", "size": 0.9, "scale": (1.45, 1.45, 0.55),
         "location": (0.0, 0.0, 0.2), "material": {"preset": "metal", "color": "aeb6c0"}},
        {"shape": "cylinder", "name": "Moyeu", "size": 0.6, "scale": (0.8, 0.8, 0.5),
         "location": (0.0, 0.0, 0.2), "material": {"preset": "metal", "color": "8a8f9c"}},
    ]


def _ring():
    return [{"shape": "torus", "name": "Anneau", "size": 0.7, "scale": (1.7, 1.7, 0.5),
             "location": (0.0, 0.0, 0.3), "material": {"preset": "gold"}}]


def _sword():
    return [
        {"shape": "cube", "name": "Lame", "size": 0.5, "scale": (0.14, 2.4, 0.05),
         "location": (0.0, 0.0, 1.0), "material": {"preset": "chrome"}},
        {"shape": "cube", "name": "Garde", "size": 0.4, "scale": (0.9, 0.25, 0.16),
         "location": (0.0, -0.1, 0.08), "material": {"preset": "gold"}},
        {"shape": "cylinder", "name": "Poignee", "size": 0.5, "scale": (0.14, 0.14, 0.8),
         "location": (0.0, 0.0, -0.18), "material": {"preset": "rubber", "color": "3a2530"}},
    ]


def _panel():
    return [
        {"shape": "cube", "name": "Panneau", "size": 0.7, "scale": (2.2, 0.7, 1.3),
         "location": (0.0, 0.0, 0.5), "material": {"preset": "metal", "color": "3a3f4d"}},
        {"shape": "cube", "name": "Cadran", "size": 0.7, "scale": (1.9, 0.08, 1.0),
         "location": (0.0, 0.31, 0.5), "material": {"preset": "emissive", "color": "00ffcc"}},
    ]


def _speaker():
    return [
        {"shape": "cube", "name": "Enceinte", "size": 0.7, "scale": (1.6, 2.2, 1.1),
         "location": (0.0, 0.0, 0.55), "material": {"preset": "matte", "color": "2a2d36"}},
        {"shape": "cylinder", "name": "Haut_parleur", "size": 0.9, "scale": (0.8, 0.8, 0.15),
         "location": (0.0, 0.9, 0.7), "rotation": (math.pi / 2, 0.0, 0.0),
         "material": {"preset": "metal", "color": "9aa0ab"}},
    ]


def _planet():
    return [
        {"shape": "sphere", "name": "Planete", "size": 1.2, "scale": (1.3, 1.3, 1.3),
         "location": (0.0, 0.0, 0.6), "material": {"preset": "stylized", "color": "6c5ce7"}},
        {"shape": "torus", "name": "Anneau", "size": 0.8, "scale": (2.2, 2.2, 0.18),
         "location": (0.0, 0.0, 0.6), "rotation": (math.pi / 3, 0.0, 0.0),
         "material": {"preset": "neon"}},
    ]


def _column():
    return [
        {"shape": "cube", "name": "Base", "size": 0.8, "scale": (1.7, 1.7, 0.2),
         "location": (0.0, 0.0, 0.1), "material": {"preset": "concrete"}},
        {"shape": "cylinder", "name": "Fut", "size": 0.6, "scale": (0.9, 0.9, 2.4),
         "location": (0.0, 0.0, 1.2), "material": {"preset": "concrete"}},
        {"shape": "cube", "name": "Chapiteau", "size": 0.8, "scale": (1.7, 1.7, 0.2),
         "location": (0.0, 0.0, 2.3), "material": {"preset": "concrete"}},
    ]


def _robot():
    parts = [
        {"shape": "cube", "name": "Tete", "size": 0.4, "scale": (0.8, 0.8, 0.7),
         "location": (0.0, 0.0, 1.55), "material": {"preset": "metal", "color": "dfe6ea"}},
        {"shape": "cube", "name": "Torse", "size": 0.5, "scale": (1.5, 1.0, 1.6),
         "location": (0.0, 0.0, 0.9), "material": {"preset": "metal", "color": "b9c2cc"}},
        {"shape": "sphere", "name": "OeilL", "size": 1.0, "scale": (0.09, 0.09, 0.09),
         "location": (0.14, 0.36, 1.62), "material": {"preset": "emissive", "color": "00ffcc"}},
        {"shape": "sphere", "name": "OeilR", "size": 1.0, "scale": (0.09, 0.09, 0.09),
         "location": (-0.14, 0.36, 1.62), "material": {"preset": "emissive", "color": "00ffcc"}},
        {"shape": "cube", "name": "BrasG", "size": 0.3, "scale": (0.22, 0.22, 1.4),
         "location": (0.7, 0.0, 1.1), "material": {"preset": "metal", "color": "8a8f9c"}},
        {"shape": "cube", "name": "BrasD", "size": 0.3, "scale": (0.22, 0.22, 1.4),
         "location": (-0.7, 0.0, 1.1), "material": {"preset": "metal", "color": "8a8f9c"}},
        {"shape": "cylinder", "name": "JambeG", "size": 0.4, "scale": (0.5, 0.5, 1.2),
         "location": (0.28, 0.0, 0.45), "material": {"preset": "metal", "color": "6e7680"}},
        {"shape": "cylinder", "name": "JambeD", "size": 0.4, "scale": (0.5, 0.5, 1.2),
         "location": (-0.28, 0.0, 0.45), "material": {"preset": "metal", "color": "6e7680"}},
        {"shape": "sphere", "name": "Antenne", "size": 1.0, "scale": (0.1, 0.1, 0.1),
         "location": (0.0, 0.0, 1.95), "material": {"preset": "emissive", "color": "ff5252"}},
    ]
    return parts


def _character():
    return None


def _abstract():
    return [
        {"shape": "icosphere", "name": "Noyau", "size": 1.0, "scale": (1.1, 1.1, 1.1),
         "location": (0.0, 0.0, 0.8), "material": {"preset": "emissive", "color": "6c5ce7"}},
        {"shape": "torus", "name": "Orbite", "size": 0.7, "scale": (2.4, 2.4, 0.12),
         "location": (0.0, 0.0, 0.8), "rotation": (math.pi / 3, 0.0, 0.2),
         "material": {"preset": "neon", "color": "00ffcc"}},
        {"shape": "torus", "name": "Orbite2", "size": 0.7, "scale": (2.0, 2.0, 0.1),
         "location": (0.0, 0.0, 0.8), "rotation": (math.pi / 2, 0.0, -0.5),
         "material": {"preset": "neon", "color": "ff2fb3"}},
    ]


_BLUEPRINTS = {
    "lamp": _lamp, "lampe": _lamp,
    "table": _table, "tables": _table,
    "chair": _chair, "chaise": _chair,
    "shelf": _shelf, "etagere": _shelf, "shelves": _shelf,
    "bottle": _bottle, "bouteille": _bottle,
    "can": _can, "cannette": _can,
    "cup": _cup, "tasse": _cup, "mug": _cup,
    "vase": _vase,
    "phone": _phone, "telephone": _phone,
    "laptop": _laptop, "portable": _laptop,
    "monitor": _monitor, "ecran": _monitor,
    "drone": _drone,
    "rocket": _rocket, "fusee": _rocket,
    "building": _building, "immeuble": _building, "tour": _building,
    "tree": _tree, "arbre": _tree,
    "gear": _gear, "engrenage": _gear,
    "ring": _ring, "bague": _ring, "anneau": _ring,
    "sword": _sword, "epee": _sword,
    "panel": _panel, "panneau": _panel,
    "speaker": _speaker, "enceinte": _speaker, "haut_parleur": _speaker,
    "planet": _planet, "planete": _planet,
    "column": _column, "colonne": _column,
    "robot": _robot, "robots": _robot,
    "character": _character, "personnage": _character, "avatar": _character,
    "human": _character, "humain": _character,
    "abstract": _abstract,
}