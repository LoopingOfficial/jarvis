"""Textures : import PNG/JPG/EXR/HDRI, UV mapping, branchement Principled.

Point d'integration ComfyUI : une texture generee ailleurs est simplement un
fichier image que ce module importe et branche reellement sur le materiau.
"""
from __future__ import annotations

import os

import bpy

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".exr", ".hdr", ".tga", ".tif", ".tiff", ".webp"}

# Canal du Principled BSDF vise par chaque type de texture.
SLOT_INPUTS = {
    "base_color": "Base Color",
    "color": "Base Color",
    "albedo": "Base Color",
    "roughness": "Roughness",
    "metallic": "Metallic",
    "emission": "Emission Color",
    "alpha": "Alpha",
    "normal": "Normal",
}

NON_COLOR_SLOTS = {"roughness", "metallic", "normal", "alpha"}


def is_image(path: str) -> bool:
    return os.path.splitext(str(path or ""))[1].lower() in IMAGE_EXT


def load_image(path: str):
    if not path or not os.path.isfile(path):
        return None
    try:
        return bpy.data.images.load(path, check_existing=True)
    except Exception:
        return None


def unwrap(obj, method: str = "smart", angle: float = 66.0, margin: float = 0.02) -> bool:
    """UV mapping reel. `smart` convient a la plupart des objets procedureaux."""
    if obj is None or obj.type != "MESH":
        return False
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        if str(method).lower() == "cube":
            bpy.ops.uv.cube_project(cube_size=1.0)
        elif str(method).lower() == "sphere":
            bpy.ops.uv.sphere_project()
        elif str(method).lower() == "cylinder":
            bpy.ops.uv.cylinder_project()
        elif str(method).lower() == "unwrap":
            bpy.ops.uv.unwrap(method="ANGLE_BASED", margin=float(margin))
        else:
            bpy.ops.uv.smart_project(angle_limit=float(angle) * 3.14159 / 180.0,
                                     island_margin=float(margin))
        bpy.ops.object.mode_set(mode="OBJECT")
        return True
    except Exception:
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass
        return False


def has_uvs(obj) -> bool:
    return bool(obj and obj.type == "MESH" and len(obj.data.uv_layers))


def _principled(mat):
    if not mat or not mat.use_nodes:
        return None
    return next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)


def attach_texture(mat, path: str, slot: str = "base_color",
                   scale: float = 1.0, strength: float = 1.0) -> bool:
    """Branche une image sur un canal du Principled. False si rien n'a ete fait."""
    image = load_image(path)
    bsdf = _principled(mat)
    if image is None or bsdf is None:
        return False
    slot = str(slot or "base_color").lower()
    target = SLOT_INPUTS.get(slot)
    if not target or target not in bsdf.inputs:
        return False
    nt = mat.node_tree
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.location = (-620, 260 - 260 * len(nt.nodes) % 900)
    if slot in NON_COLOR_SLOTS:
        try:
            image.colorspace_settings.name = "Non-Color"
        except Exception:
            pass
    if float(scale) != 1.0:
        mapping = nt.nodes.new("ShaderNodeMapping")
        coord = nt.nodes.new("ShaderNodeTexCoord")
        mapping.inputs["Scale"].default_value = (float(scale),) * 3
        nt.links.new(coord.outputs["UV"], mapping.inputs["Vector"])
        nt.links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
    if slot == "normal":
        nmap = nt.nodes.new("ShaderNodeNormalMap")
        nmap.inputs["Strength"].default_value = float(strength)
        nt.links.new(tex.outputs["Color"], nmap.inputs["Color"])
        nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
        return True
    if slot == "alpha":
        nt.links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
        mat.blend_method = "BLEND"
        return True
    nt.links.new(tex.outputs["Color"], bsdf.inputs[target])
    if slot == "emission" and "Emission Strength" in bsdf.inputs:
        bsdf.inputs["Emission Strength"].default_value = max(0.1, float(strength))
    return True


def apply_texture_set(obj, textures: dict, scale: float = 1.0,
                      material_name: str = "") -> dict:
    """Applique un jeu de textures {slot: chemin} sur le materiau de l'objet."""
    from materials import ensure_material

    result = {"applied": [], "skipped": [], "uv": False}
    if obj is None or obj.type != "MESH":
        return result
    mat = None
    if material_name:
        mat = bpy.data.materials.get(material_name)
    if mat is None:
        mat = obj.data.materials[0] if obj.data.materials else None
    if mat is None:
        mat = ensure_material(obj, "Mat_Textured")
    if not has_uvs(obj):
        result["uv"] = unwrap(obj)
    else:
        result["uv"] = True
    for slot, path in (textures or {}).items():
        if attach_texture(mat, path, slot, scale=scale):
            result["applied"].append({"slot": slot, "file": os.path.basename(str(path))})
        else:
            result["skipped"].append({"slot": slot, "file": str(path),
                                      "reason": "fichier illisible ou canal inconnu"})
    result["material"] = mat.name
    return result


def texture_summary() -> list:
    out = []
    for img in bpy.data.images:
        if img.name in {"Render Result", "Viewer Node"}:
            continue
        out.append({"name": img.name, "size": list(img.size),
                    "file": bpy.path.abspath(img.filepath) if img.filepath else ""})
    return out
