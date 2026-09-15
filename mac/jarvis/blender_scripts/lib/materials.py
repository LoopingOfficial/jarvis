"""Materiaux : Principled BSDF, presets physiques, emission, transmission.

Importe comme module de premier niveau (le dossier lib/ est dans sys.path).
"""
from __future__ import annotations

import bpy

from common import hex_to_rgb, rgba

PRESETS = {
    "metal": {"metallic": 1.0, "roughness": 0.35, "color": "c0c7d4"},
    "chrome": {"metallic": 1.0, "roughness": 0.03, "color": "eaf0f8"},
    "gold": {"metallic": 1.0, "roughness": 0.25, "color": "ffd166"},
    "brushed_metal": {"metallic": 1.0, "roughness": 0.55, "color": "9aa0ab"},
    "plastic": {"metallic": 0.0, "roughness": 0.45, "color": "3a3f4d"},
    "glossy": {"metallic": 0.2, "roughness": 0.08, "color": "5f6b82"},
    "matte": {"metallic": 0.0, "roughness": 0.9, "color": "4a4f5c"},
    "glass": {"metallic": 0.0, "roughness": 0.05, "color": "d9f2ff", "alpha": 0.6},
    "wood": {"metallic": 0.0, "roughness": 0.7, "color": "8a6244"},
    "fabric": {"metallic": 0.0, "roughness": 0.95, "color": "6e5f7d"},
    "rubber": {"metallic": 0.0, "roughness": 0.85, "color": "22242b"},
    "concrete": {"metallic": 0.0, "roughness": 0.95, "color": "8d8e91"},
    "ceramic": {"metallic": 0.0, "roughness": 0.3, "color": "f1f2f3"},
    "emissive": {"metallic": 0.0, "roughness": 0.4, "color": "00ffcc",
                 "emission_strength": 3.0, "emission_color": "00ffcc"},
    "neon": {"metallic": 0.0, "roughness": 0.3, "color": "ff2fb3",
             "emission_strength": 4.0, "emission_color": "ff2fb3"},
    "skin": {"metallic": 0.0, "roughness": 0.55, "color": "e0a878"},
    "stylized": {"metallic": 0.0, "roughness": 0.6, "color": "ff6b6b"},
}

# Rouge/orange : lumières chaudes.
_LIGHT_COLORS = {"warm": "ffd9a0", "cool": "bfe3ff", "white": "ffffff",
                 "cyan": "00fff0", "magenta": "ff2fb3", "purple": "b48cff",
                 "blue": "4d9dff", "red": "ff5252", "green": "3dff8a",
                 "amber": "ffc46b", "pink": "ff9ecb", "orange": "ffa45c"}


def _lcolor(name: str) -> str:
    c = str(name or "").lower().lstrip("#")
    return _LIGHT_COLORS.get(c, c)


def resolve_merge(spec) -> dict:
    """Fusionne preset + surcharges en paramètres Principled BSDF."""
    s = dict(spec or {})
    result = dict(PRESETS.get(str(s.pop("preset", "")).lower(), {}))
    for key in ("color", "metallic", "roughness"):
        if key in s and s[key] not in (None, ""):
            result[key] = s[key]
    if s.get("emission"):
        result["emission_strength"] = float(s["emission"] or result.get("emission_strength") or 0)
        result.setdefault("emission_color", s.get("color") or result.get("color"))
    if s.get("transmission"):
        result.setdefault("transmission", float(s["transmission"]))
    for key in ("alpha", "emission_strength", "emission_color"):
        if key in s and s[key] not in (None, ""):
            result[key] = s[key]
    return result


def light_color(name: str) -> tuple:
    hexc = _lcolor(name)
    return tuple(hex_to_rgb(hexc))

# Presets physiquement plausibles. Chaque preset est un point de depart :
# les parametres explicites passes par JARVIS le surchargent toujours.
PRESETS = {
    "metal":     {"metallic": 1.0, "roughness": 0.28, "color": "b8bec7"},
    "chrome":    {"metallic": 1.0, "roughness": 0.05, "color": "dfe6e9"},
    "gold":      {"metallic": 1.0, "roughness": 0.22, "color": "f0b429"},
    "brushed_metal": {"metallic": 1.0, "roughness": 0.45, "color": "9aa3ad"},
    "plastic":   {"metallic": 0.0, "roughness": 0.38, "color": "2f3542"},
    "glossy":    {"metallic": 0.0, "roughness": 0.08, "color": "e8ecf1"},
    "matte":     {"metallic": 0.0, "roughness": 0.92, "color": "1c1d24"},
    "glass":     {"metallic": 0.0, "roughness": 0.03, "color": "eaf6ff",
                  "transmission": 1.0, "ior": 1.45, "alpha": 0.15},
    "wood":      {"metallic": 0.0, "roughness": 0.62, "color": "8a5a33"},
    "fabric":    {"metallic": 0.0, "roughness": 0.95, "color": "4a5568", "sheen": 0.5},
    "rubber":    {"metallic": 0.0, "roughness": 0.88, "color": "16181d"},
    "concrete":  {"metallic": 0.0, "roughness": 0.85, "color": "8d939b"},
    "ceramic":   {"metallic": 0.0, "roughness": 0.18, "color": "f2f3f5"},
    "emissive":  {"metallic": 0.0, "roughness": 0.4, "color": "00d2d3", "emission": 6.0},
    "neon":      {"metallic": 0.0, "roughness": 0.3, "color": "00ffcc", "emission": 12.0},
    "skin":      {"metallic": 0.0, "roughness": 0.55, "color": "e0ac86", "subsurface": 0.12},
    "stylized":  {"metallic": 0.0, "roughness": 0.5, "color": "6c5ce7"},
}


def preset(name: str) -> dict:
    return dict(PRESETS.get(str(name or "").lower().replace(" ", "_"), {}))


def _set(bsdf, key: str, value) -> None:
    if key in bsdf.inputs:
        try:
            bsdf.inputs[key].default_value = value
        except Exception:
            pass


def make_material(name: str, color="2a2a3c", metallic: float = 0.0,
                  roughness: float = 0.5, emission_strength: float = 0.0,
                  emission_color=None, alpha: float = 1.0,
                  transmission: float = 0.0, ior: float = 1.45,
                  subsurface: float = 0.0, sheen: float = 0.0) -> bpy.types.Material:
    """Cree un materiau Principled reel. Compatible Blender 3.x et 4.x."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.blend_method = "BLEND" if (alpha < 1.0 or transmission > 0.0) else "OPAQUE"
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = mat.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
    rgb = rgba(color, alpha)
    _set(bsdf, "Base Color", rgb)
    _set(bsdf, "Metallic", float(metallic))
    _set(bsdf, "Roughness", float(roughness))
    _set(bsdf, "Alpha", float(alpha))
    if float(transmission) > 0:
        # Blender 4.x a renomme "Transmission" en "Transmission Weight".
        _set(bsdf, "Transmission", float(transmission))
        _set(bsdf, "Transmission Weight", float(transmission))
        _set(bsdf, "IOR", float(ior))
    if float(subsurface) > 0:
        _set(bsdf, "Subsurface", float(subsurface))
        _set(bsdf, "Subsurface Weight", float(subsurface))
    if float(sheen) > 0:
        _set(bsdf, "Sheen", float(sheen))
        _set(bsdf, "Sheen Weight", float(sheen))
    if float(emission_strength) > 0:
        _set(bsdf, "Emission Color", rgba(emission_color or color, 1.0))
        _set(bsdf, "Emission", rgba(emission_color or color, 1.0))
        _set(bsdf, "Emission Strength", float(emission_strength))
    try:
        mat.diffuse_color = rgb
    except Exception:
        pass
    return mat


def from_spec(spec, name: str = "Mat_JARVIS") -> bpy.types.Material:
    """Construit un materiau depuis un dict declaratif (ou un nom de preset)."""
    if isinstance(spec, str):
        spec = {"preset": spec}
    spec = dict(spec or {})
    base = preset(spec.get("preset") or spec.get("type") or "")
    base.update({k: v for k, v in spec.items()
                 if k not in {"preset", "type", "name"} and v is not None})
    return make_material(
        str(spec.get("name") or name),
        color=base.get("color", base.get("base_color", "2a2a3c")),
        metallic=float(base.get("metallic", 0.0)),
        roughness=float(base.get("roughness", 0.5)),
        emission_strength=float(base.get("emission", base.get("emission_strength", 0.0))),
        emission_color=base.get("emission_color"),
        alpha=float(base.get("alpha", 1.0)),
        transmission=float(base.get("transmission", 0.0)),
        ior=float(base.get("ior", 1.45)),
        subsurface=float(base.get("subsurface", 0.0)),
        sheen=float(base.get("sheen", 0.0)),
    )


def apply_material(obj, mat, slot: int = 0):
    if obj is None or obj.type not in {"MESH", "CURVE", "FONT"}:
        return mat
    if not obj.data.materials:
        obj.data.materials.append(mat)
    else:
        idx = max(0, min(int(slot), len(obj.data.materials) - 1))
        obj.data.materials[idx] = mat
    return mat


def apply_to_object(obj, color="2a2a3c", metallic: float = 0.0,
                    roughness: float = 0.5, emission_strength: float = 0.0,
                    emission_color=None, alpha: float = 1.0,
                    name: str = "Mat_JARVIS"):
    mat = make_material(name if isinstance(name, str) and name else "Mat_JARVIS",
                        color=color, metallic=float(metallic), roughness=float(roughness),
                        emission_strength=float(emission_strength),
                        emission_color=emission_color or color, alpha=float(alpha))
    apply_material(obj, mat)
    return mat


def apply_spec(obj, spec, name: str = "Mat_JARVIS"):
    if not spec:
        return None
    mat = from_spec(spec, name)
    apply_material(obj, mat)
    return mat


def ensure_material(obj, name: str = "Mat_JARVIS"):
    """Retourne le materiau existant de l'objet, ou en cree un neutre."""
    if obj.data.materials and obj.data.materials[0]:
        return obj.data.materials[0]
    mat = make_material(name)
    apply_material(obj, mat)
    return mat


def object_materials(obj) -> list:
    if obj is None or obj.type not in {"MESH", "CURVE", "FONT"}:
        return []
    return [m.name for m in obj.data.materials if m]


def material_summary() -> list:
    out = []
    for mat in bpy.data.materials:
        entry = {"name": mat.name, "users": mat.users}
        bsdf = None
        if mat.use_nodes:
            bsdf = next((n for n in mat.node_tree.nodes
                         if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is not None:
            for key, label in (("Base Color", "base_color"), ("Metallic", "metallic"),
                               ("Roughness", "roughness"), ("Alpha", "alpha")):
                if key in bsdf.inputs:
                    value = bsdf.inputs[key].default_value
                    try:
                        entry[label] = ([round(float(v), 3) for v in value]
                                        if hasattr(value, "__len__") else round(float(value), 3))
                    except Exception:
                        pass
            entry["textures"] = [n.image.name for n in mat.node_tree.nodes
                                 if n.type == "TEX_IMAGE" and n.image]
        out.append(entry)
    return out


def remove_unused() -> int:
    removed = 0
    for mat in list(bpy.data.materials):
        if mat.users == 0:
            bpy.data.materials.remove(mat)
            removed += 1
    return removed


def smart_uv(obj) -> None:
    """Deplie les UV reels (smart project) pour recevoir des textures."""
    if obj is None or obj.type != "MESH":
        return
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    try:
        bpy.ops.uv.smart_project(angle_limit=1.0472, island_margin=0.002)
    except Exception:
        pass
    bpy.ops.object.mode_set(mode="OBJECT")


_TEXTURE_INPUTS = {"base_color": "Base Color", "roughness": "Roughness",
                   "metallic": "Metallic", "normal": "Normal",
                   "emission": "Emission Color", "alpha": "Alpha"}


def apply_texture(obj, maps: dict, scale: float = 1.0) -> None:
    """Applique de vraies textures image sur l'objet (avec UV mapping)."""
    if obj is None or obj.type != "MESH" or not maps:
        return
    mat = ensure_material(obj)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    smart_uv(obj)
    used = False
    for slot, fname in (maps or {}).items():
        slot_key = str(slot).lower().replace("-", "_")
        if slot_key not in _TEXTURE_INPUTS or not fname:
            continue
        image = bpy.data.images.load(str(fname))
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = image
        if float(scale or 1.0) != 1.0:
            try:
                mapping = nodes.new("ShaderNodeMapping")
                coord = nodes.new("ShaderNodeTexCoord")
                mapping.inputs["Scale"].default_value = (float(scale),) * 3
                links.new(coord.outputs["UV"], mapping.inputs["Vector"])
                links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
            except Exception:
                pass
        if slot_key == "normal":
            map_node = nodes.new("ShaderNodeNormalMap")
            links.new(tex.outputs["Color"], map_node.inputs["Color"])
            target = map_node.outputs["Normal"]
        else:
            target = tex.outputs["Color"]
        input_key = _TEXTURE_INPUTS[slot_key]
        if input_key in bsdf.inputs and target is not None:
            links.new(target, bsdf.inputs[input_key])
            used = True
    if not used:
        raise RuntimeError("Aucun slot de texture reconnu (base_color attendu).")
