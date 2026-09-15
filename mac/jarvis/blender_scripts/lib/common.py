"""Primitives partagées par les scripts Blender de JARVIS (exécutés DANS Blender).

Path d'accès aux réglages du job : variable d'environnement
JARVIS_BLENDER_SETTINGS (prioritaire) ou dernier argument passé après `--`.
"""
from __future__ import annotations

import json
import os
import struct
import sys
import time

SETTINGS_PATH = ""
SETTINGS: dict = {}
_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def boot() -> dict:
    """Charge le JSON de réglages et le rend accessible via `settings()`."""
    global SETTINGS_PATH, SETTINGS
    env = os.environ.get("JARVIS_BLENDER_SETTINGS", "").strip()
    if env and os.path.exists(env):
        SETTINGS_PATH = env
    else:
        tail = []
        if "--" in sys.argv:
            idx = sys.argv.index("--")
            tail = [a for a in sys.argv[idx + 1:] if a]
        if tail and os.path.exists(tail[-1]):
            SETTINGS_PATH = tail[-1]
    if SETTINGS_PATH and os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as fh:
                SETTINGS = json.load(fh)
        except Exception:
            SETTINGS = {}
    return SETTINGS


def progress(value: float, message: str = "", stage: str = "") -> None:
    """Écrit la progression dans le fichier sondé par le manager (SSE)."""
    value = max(0.0, min(1.0, float(value)))
    SETTINGS["__progress"] = value
    SETTINGS["__message"] = message
    if stage:
        SETTINGS["__stage"] = stage
    path = SETTINGS.get("__progress_file")
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"progress": value, "message": str(message)[:200],
                       "stage": stage, "ts": time.time()}, fh, ensure_ascii=False)
    except Exception:
        pass


def out(name: str) -> str:
    return os.path.join(str(SETTINGS.get("output_dir") or "."), name)


def settings() -> dict:
    return SETTINGS


def write_metadata(meta: dict) -> dict:
    meta.setdefault("job_id", SETTINGS.get("__job_id", ""))
    meta["tool"] = SETTINGS.get("__tool", "")
    meta["action"] = SETTINGS.get("__action", "")
    try:
        with open(out("metadata.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return meta


def finish(meta: dict, message: str = "Terminé") -> dict:
    progress(1.0, message, stage="completed")
    write_metadata(meta)
    _remove(SETTINGS.get("__progress_file"))
    return meta


def fail(message: str) -> None:
    progress(1.0, str(message)[:400], stage="failed")
    try:
        with open(out("error.txt"), "w", encoding="utf-8") as fh:
            fh.write(str(message)[:2000])
    except Exception:
        pass
    _remove(SETTINGS.get("__progress_file"))


def _remove(path: str) -> None:
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Couleurs
# ---------------------------------------------------------------------------
_PALETTE = {
    "red": "d63031", "orange": "e17055", "amber": "fdcb6e", "yellow": "ffeaa7",
    "green": "00b894", "teal": "00cec9", "cyan": "00d2d3", "sky": "74b9ff",
    "blue": "0984e3", "indigo": "6c5ce7", "violet": "a29bfe", "magenta": "fd79a8",
    "pink": "e84393", "black": "12131a", "dark": "1e1f27", "grey": "636e72",
    "gray": "636e72", "silver": "b2bec3", "white": "f5f6fa", "gold": "f39c12",
    "bronze": "cd6133", "chrome": "dfe6e9", "neon": "00ffcc",
}


def hex_to_rgb(color) -> list[float]:
    """'#RRGGBB', tuple/list ou nom de couleur → [r, g, b] (0..1)."""
    if isinstance(color, (tuple, list)):
        vals = [float(c) for c in color[:3]]
        return [max(0.0, min(1.0, v)) for v in vals]
    name = str(color or "").strip()
    if name.lower() in _PALETTE:
        name = _PALETTE[name.lower()]
    c = name.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6:
        return [0.164, 0.137, 0.235]          # violet sombre par défaut
    try:
        return [int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    except Exception:
        return [0.164, 0.137, 0.235]


def rgba(color, alpha: float = 1.0) -> list[float]:
    return hex_to_rgb(color) + [max(0.0, min(1.0, float(alpha)))]


# ---------------------------------------------------------------------------
# Vérification réelle des exports (jamais simulée)
# ---------------------------------------------------------------------------
def verify_glb(path: str) -> dict:
    """Parse le conteneur GLB + morceau JSON : ok / meshes / skins / error."""
    result = {"ok": False, "meshes": 0, "skins": 0, "error": ""}
    try:
        with open(path, "rb") as fh:
            header = fh.read(12)
            if len(header) < 12:
                result["error"] = "fichier tronqué (en-tête incomplet)"
                return result
            magic, _version, length = struct.unpack("<III", header)
            if magic != 0x46546C67:
                result["error"] = "magic glTF absent (fichier corrompu)"
                return result
            chunk_header = fh.read(8)
            if len(chunk_header) < 8:
                result["error"] = "fichier tronqué (chunk manquant)"
                return result
            json_len, json_type = struct.unpack("<II", chunk_header)
            if json_type != 0x4E4F534A:
                result["error"] = "premier chunk non JSON"
                return result
            gltf = json.loads(fh.read(json_len))
            meshes = list(gltf.get("meshes") or [])
            result["meshes"] = len(meshes)
            result["skins"] = len(list(gltf.get("skins") or []))
            result["ok"] = len(meshes) > 0
            if not result["ok"]:
                result["error"] = "aucun maillage dans le GLB"
            result["scenes"] = len(list(gltf.get("scenes") or []))
    except Exception as exc:
        result["error"] = str(exc)[:200]
    return result


def polycount() -> int:
    """Nombre total de triangles (réel, via calc_loop_triangles)."""
    import bpy
    total = 0
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        try:
            obj.data.calc_loop_triangles()
            total += len(obj.data.loop_triangles)
        except Exception:
            try:
                total += sum(len(p.vertices) - 2 for p in obj.data.polygons)
            except Exception:
                pass
    return int(total)


def rig_summary() -> dict:
    """Armatures et poids réels de la scène."""
    import bpy
    skinned = []
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if not armatures:
        return {"rigged": False, "has_weights": False, "skinned_meshes": [],
                "armatures": 0, "bones": 0}
    bones = sum(len(getattr(a.data, "bones", [])) for a in armatures)
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        parent = getattr(obj, "parent", None)
        if parent and parent.type == "ARMATURE":
            skinned.append(obj.name)
    has_weights = False
    if skinned:
        for name in skinned:
            mesh = bpy.data.objects.get(name)
            if mesh is not None and getattr(mesh, "vertex_groups", None):
                has_weights = True
    return {"rigged": True, "armatures": len(armatures), "bones": int(bones),
            "skinned_meshes": skinned, "has_weights": has_weights}


def animations() -> list[str]:
    import bpy
    actions = []
    for obj in bpy.data.objects:
        try:
            adata = obj.animation_data
            if adata and adata.action:
                actions.append(adata.action.name)
        except Exception:
            pass
    return sorted(set(actions))