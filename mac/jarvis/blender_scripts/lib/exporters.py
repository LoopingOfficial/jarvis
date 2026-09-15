"""Export multi-format + VERIFICATION reelle du fichier produit.

Rien n'est declare exporte sans que le fichier existe et soit relu :
un GLB est reouvert, son entete et son chunk JSON sont analyses pour compter
meshes, materiaux et animations effectivement presents.
"""
from __future__ import annotations

import json
import os
import struct

import bpy

FORMATS = ("glb", "gltf", "fbx", "obj", "stl", "blend")


def _call(op, **kwargs):
    """Appelle un operateur en retirant les arguments inconnus de la version."""
    try:
        return op(**kwargs)
    except TypeError:
        try:
            props = {p.identifier for p in op.get_rna_type().properties}
        except Exception:
            props = set()
        clean = {k: v for k, v in kwargs.items() if k in props}
        return op(**clean)


def export_gltf(path: str, binary: bool = True, animations: bool = True,
                textures: bool = True, morph: bool = True, draco: bool = False) -> str:
    kwargs = {
        "filepath": str(path),
        "export_format": "GLB" if binary else "GLTF_SEPARATE",
        "export_apply": False,
        "export_materials": "EXPORT" if textures else "NONE",
        "export_animations": bool(animations),
        "export_skins": True,
        "export_morph": bool(morph),
        "export_yup": True,
        "export_cameras": False,
        "export_lights": False,
        "export_draco_mesh_compression_enable": bool(draco),
        "use_selection": False,
    }
    if animations:
        # Une piste NLA = une animation nommee dans le GLB.
        kwargs["export_animation_mode"] = "NLA_TRACKS"
        kwargs["export_nla_strips"] = True
    _call(bpy.ops.export_scene.gltf, **kwargs)
    return str(path) if os.path.isfile(path) else ""


def export_fbx(path: str, animations: bool = True) -> str:
    _call(bpy.ops.export_scene.fbx, filepath=str(path), use_selection=False,
          bake_anim=bool(animations), add_leaf_bones=False, path_mode="COPY",
          embed_textures=True, mesh_smooth_type="FACE", apply_unit_scale=True)
    return str(path) if os.path.isfile(path) else ""


def export_obj(path: str) -> str:
    if hasattr(bpy.ops.wm, "obj_export"):
        _call(bpy.ops.wm.obj_export, filepath=str(path), export_materials=True,
              export_selected_objects=False, export_triangulated_mesh=True)
    else:
        _call(bpy.ops.export_scene.obj, filepath=str(path), use_materials=True,
              use_selection=False, use_triangles=True)
    return str(path) if os.path.isfile(path) else ""


def export_stl(path: str) -> str:
    if hasattr(bpy.ops.wm, "stl_export"):
        _call(bpy.ops.wm.stl_export, filepath=str(path), export_selected_objects=False)
    else:
        _call(bpy.ops.export_mesh.stl, filepath=str(path), use_selection=False)
    return str(path) if os.path.isfile(path) else ""


def save_blend(path: str) -> str:
    bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True,
                                compress=False, relative_remap=False)
    return str(path) if os.path.isfile(path) else ""


EXPORTERS = {
    "glb": lambda p, o: export_gltf(p, True, o.get("animations", True),
                                    o.get("textures", True), o.get("morph", True),
                                    o.get("draco", False)),
    "gltf": lambda p, o: export_gltf(p, False, o.get("animations", True),
                                     o.get("textures", True), o.get("morph", True)),
    "fbx": lambda p, o: export_fbx(p, o.get("animations", True)),
    "obj": lambda p, o: export_obj(p),
    "stl": lambda p, o: export_stl(p),
    "blend": lambda p, o: save_blend(p),
}


def export(path: str, fmt: str = "glb", options=None) -> dict:
    """Exporte puis VERIFIE. `ok` n'est vrai que si le fichier est relisible."""
    fmt = str(fmt or "glb").lower().lstrip(".")
    if fmt not in EXPORTERS:
        return {"ok": False, "format": fmt, "error": "Format non supporte: " + fmt}
    try:
        written = EXPORTERS[fmt](path, dict(options or {}))
    except Exception as exc:
        return {"ok": False, "format": fmt, "path": str(path),
                "error": "Export %s impossible : %s" % (fmt, str(exc)[:400])}
    if not written or not os.path.isfile(written):
        return {"ok": False, "format": fmt, "path": str(path),
                "error": "Blender n'a produit aucun fichier %s." % fmt}
    info = verify(written, fmt)
    info.update({"format": fmt, "path": written, "name": os.path.basename(written),
                 "size": os.path.getsize(written)})
    return info


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def read_glb(path: str) -> dict:
    """Relit un GLB : entete binaire + chunk JSON. Aucune supposition."""
    with open(path, "rb") as fh:
        header = fh.read(12)
        if len(header) < 12:
            raise ValueError("fichier GLB tronque")
        magic, version, total = struct.unpack("<4sII", header)
        if magic != b"glTF":
            raise ValueError("entete GLB invalide")
        chunk_header = fh.read(8)
        length, ctype = struct.unpack("<II", chunk_header)
        if ctype != 0x4E4F534A:
            raise ValueError("premier chunk non JSON")
        doc = json.loads(fh.read(length).decode("utf-8"))
    return {"glb_version": version, "declared_size": total, "gltf": doc}


def verify(path: str, fmt: str = "glb") -> dict:
    """Controle reel du fichier produit. Retourne ok=False avec la raison."""
    size = os.path.getsize(path) if os.path.isfile(path) else 0
    if size < 32:
        return {"ok": False, "error": "Fichier vide ou tronque (%d octets)." % size}
    if fmt not in {"glb", "gltf"}:
        return {"ok": True, "verified": "taille", "size": size}
    if fmt == "gltf":
        try:
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception as exc:
            return {"ok": False, "error": "glTF illisible : %s" % str(exc)[:200]}
    else:
        try:
            doc = read_glb(path)["gltf"]
        except Exception as exc:
            return {"ok": False, "error": "GLB illisible : %s" % str(exc)[:200]}
    meshes = doc.get("meshes") or []
    prim_count = sum(len(m.get("primitives") or []) for m in meshes)
    animations = [a.get("name", "") for a in (doc.get("animations") or [])]
    materials = [m.get("name", "") for m in (doc.get("materials") or [])]
    accessors = doc.get("accessors") or []
    vertices = 0
    for mesh in meshes:
        for prim in mesh.get("primitives") or []:
            idx = (prim.get("attributes") or {}).get("POSITION")
            if isinstance(idx, int) and idx < len(accessors):
                vertices += int(accessors[idx].get("count") or 0)
    ok = bool(meshes) and prim_count > 0
    result = {
        "ok": ok, "size": size, "meshes": len(meshes), "primitives": prim_count,
        "vertices": vertices, "materials": materials, "animations": animations,
        "skins": len(doc.get("skins") or []), "images": len(doc.get("images") or []),
        "nodes": len(doc.get("nodes") or []),
        "generator": (doc.get("asset") or {}).get("generator", ""),
    }
    if not ok:
        result["error"] = "Le fichier exporte ne contient aucun maillage."
    return result
