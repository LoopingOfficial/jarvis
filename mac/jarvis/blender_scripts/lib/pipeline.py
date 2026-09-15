"""Chaine standard d'un job 3D : scene -> action -> sauvegarde -> export -> apercu.

Ce module est le seul endroit qui decide de l'ordre des etapes, ce qui garantit
que chaque outil produit les memes livrables : model.blend, model.glb,
preview.png et metadata.json, tous verifies.
"""
from __future__ import annotations

import os
import time

import bpy

import common
import exporters
import sceneio

# Etapes -> (progression atteinte, libelle UI)
STAGES = [
    ("preparing", 0.06, "Preparation"),
    ("geometry", 0.30, "Creation de la geometrie"),
    ("materials", 0.45, "Materiaux"),
    ("textures", 0.55, "Textures"),
    ("rig", 0.65, "Rig"),
    ("animation", 0.74, "Animation"),
    ("optimize", 0.82, "Optimisation"),
    ("exporting", 0.90, "Export"),
    ("preview", 0.96, "Previsualisation"),
    ("finalizing", 0.99, "Finalisation"),
]
STAGE_INDEX = {name: (value, label) for name, value, label in STAGES}


def stage(name: str, message: str = "", value: float = 0.0) -> None:
    """Publie une etape reelle (lue par le manager, relayee en SSE)."""
    ref, label = STAGE_INDEX.get(name, (value or 0.5, name))
    common.progress(value or ref, message or label, stage=name)


def open_scene(settings) -> dict:
    """Reprend le .blend du projet si demande, sinon part d'une scene vide."""
    stage("preparing", "Preparation de la scene")
    source = str(settings.get("blend_in") or "").strip()
    if source and os.path.isfile(source):
        sceneio.open_blend(source)
        return {"reused": True, "blend_in": source}
    sceneio.new_scene(clear=True)
    return {"reused": False, "blend_in": ""}


def ensure_lighting_and_camera(settings, force: bool = False) -> None:
    import camera
    import lighting

    from camera import scene_bounds

    _, dims, radius = scene_bounds()
    if force or not lighting.has_light():
        lighting.studio(radius=max(1.2, radius * 3.0),
                        height=max(1.5, dims.z * 1.6 + 1.0),
                        energy=float(settings.get("light_energy", 320.0)),
                        target=(0.0, 0.0, dims.z * 0.35))
    hdri = str(settings.get("hdri") or "")
    if hdri:
        lighting.world_hdri(hdri, float(settings.get("hdri_strength", 1.0)))
    camera.frame_scene()


def save_project(settings) -> str:
    """Sauvegarde le .blend du projet (source de la conversation continue)."""
    path = common.out("model.blend")
    try:
        exporters.save_blend(path)
    except Exception:
        return ""
    return path if os.path.isfile(path) else ""


def export_model(settings, formats=None) -> list:
    """Exporte et VERIFIE chaque format demande. GLB par defaut."""
    wanted = formats if formats is not None else settings.get("export_formats")
    if not wanted:
        wanted = [str(settings.get("format") or "glb")]
    if isinstance(wanted, str):
        wanted = [wanted]
    options = {
        "animations": bool(settings.get("export_animations", True)),
        "textures": bool(settings.get("export_textures", True)),
        "morph": bool(settings.get("export_morph", True)),
        "draco": bool(settings.get("draco", False)),
    }
    results = []
    for fmt in wanted:
        fmt = str(fmt).lower().lstrip(".")
        if fmt == "blend":
            continue
        stage("exporting", "Export %s" % fmt.upper())
        results.append(exporters.export(common.out("model." + fmt), fmt, options))
    return results


def make_preview(settings) -> str:
    """Rendu d'apercu reel. Retourne le chemin, ou une chaine vide."""
    if not settings.get("preview", True):
        return ""
    import render as render_lib

    stage("preview", "Previsualisation")
    try:
        return render_lib.preview(
            common.out("preview.png"),
            size=int(settings.get("preview_size", 640) or 640),
            engine=str(settings.get("preview_engine", "eevee")),
            samples=int(settings.get("preview_samples", 32) or 32),
            transparent=bool(settings.get("preview_transparent", False)))
    except Exception as exc:
        common.progress(0.96, "Apercu indisponible : %s" % str(exc)[:120], stage="preview")
        return ""


def finalize(settings, meta, started: float) -> dict:
    """Sauvegarde, exporte, previsualise puis ecrit metadata.json (verifie)."""
    stage("finalizing", "Finalisation")
    blend = save_project(settings)
    exports = export_model(settings)
    preview = make_preview(settings)
    inventory = sceneio.inventory()

    glb = next((e for e in exports if e.get("format") == "glb" and e.get("ok")), None)
    ok_exports = [e for e in exports if e.get("ok")]
    failed = [e for e in exports if not e.get("ok")]

    meta.update({
        "blender_version": ".".join(str(v) for v in bpy.app.version),
        "blend": os.path.basename(blend) if blend else "",
        "preview": os.path.basename(preview) if preview else "",
        "exports": [{"format": e.get("format"), "name": e.get("name", ""),
                     "size": e.get("size", 0), "ok": bool(e.get("ok")),
                     "error": e.get("error", "")} for e in exports],
        "polycount": inventory["counts"]["triangles"],
        "vertices": sum(o.get("vertices", 0) for o in inventory["objects"]
                        if o.get("type") == "MESH"),
        "objects": inventory["counts"]["objects"],
        "materials": [m["name"] for m in inventory["materials"]],
        "textures": [t["name"] for t in inventory["textures"]],
        "animations": [a["name"] for a in inventory["animations"]],
        "rig": inventory["rig"],
        "lights": inventory["lights"],
        "dimensions": inventory["framing"]["dimensions"],
        "duration_s": round(time.time() - started, 2),
        "verified": {
            "blend": bool(blend),
            "preview": bool(preview),
            "exports_ok": len(ok_exports),
            "exports_failed": [e.get("error", "") for e in failed],
            "glb": glb or {},
        },
    })
    if glb:
        meta["glb"] = {"name": glb.get("name", ""), "size": glb.get("size", 0),
                       "meshes": glb.get("meshes", 0), "vertices": glb.get("vertices", 0),
                       "materials": glb.get("materials", []),
                       "animations": glb.get("animations", [])}
    return common.finish(meta, "Modele pret")
