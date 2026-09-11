"""Détection des moteurs de génération d'images ComfyUI.

ComfyUI moderne ne charge plus les modèles que via `CheckpointLoaderSimple`
(models/checkpoints) : Z-Image, Flux, Hunyuan… utilisent des modèles séparés
dans diffusion_models/, text_encoders/, vae/ et clip/.

Ce module répond à la question « quels workflows image sont réellement
exploitables ? » en croisant :
  1. l'API ComfyUI (/system_stats, /models/config quand elle existe),
  2. le contenu du dossier de modèles local (détection multi-fichiers).

Résultat : une liste d'`engines` avec les fichiers exacts à charger
(diffusion model, text encoder, VAE…) pour construire un workflow API valide.
"""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from .connectors import http_json

DEFAULT_BASE_URL = "http://127.0.0.1:8188"

MODEL_EXTS = (".safetensors", ".ckpt", ".sft", ".pt", ".pth", ".bin", ".gguf")

# Répertoires possibles des modèles ComfyUI Desktop/portable sur cette machine.
def _default_model_dirs() -> list[Path]:
    home = Path.home()
    localappdata = Path(os.getenv("LOCALAPPDATA", str(home)))
    candidates = [
        localappdata / "Comfy-Desktop" / "ComfyUI-Shared" / "models",
        home / "ComfyUI" / "models",
        home / "Documents" / "ComfyUI" / "models",
        home / "Desktop" / "Comfy-Desktop" / "ComfyUI-Shared" / "models",
    ]
    out: list[Path] = []
    seen: set[str] = set()
    for c in candidates:
        key = str(c).casefold()
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


_FOLDER_ALIASES = {
    "checkpoints": "checkpoints", "checkpoint": "checkpoints",
    "unet": "unet",
    "diffusion_models": "diffusion_models", "diffusion_model": "diffusion_models",
    "text_encoders": "text_encoders", "text_encoder": "text_encoders",
    "vae": "vae",
    "clip": "clip",
    "clip_vision": "clip_vision",
    "controlnet": "controlnet",
    "loras": "loras",
    "upscale_models": "upscale_models", "upscale_model": "upscale_models",
    "style_models": "style_models",
}


def _normalize_folder(folder: str) -> str:
    key = folder.strip().casefold().replace(" ", "_")
    return _FOLDER_ALIASES.get(key, key)


# ---------------------------------------------------------------------------
# Lecture des fichiers de modèles
# ---------------------------------------------------------------------------
def scan_models_dir(root: Path) -> dict[str, list[str]]:
    """Parcourt un dossier ComfyUI/models et renvoie « dossier → [fichiers] »."""
    result: dict[str, list[str]] = {}
    if not root:
        return result
    root = Path(root).resolve()
    if not root.is_dir():
        return result
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in MODEL_EXTS:
            continue
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        parts = rel.parts
        folder = _normalize_folder(parts[0] if len(parts) > 1 else "root")
        name = "/".join(parts[1:]) if len(parts) > 1 else rel.name
        result.setdefault(folder, []).append(name)
    return result


def files_from_api(payload: Any) -> dict[str, list[str]]:
    """Convertit la réponse de `GET /models/config` en « dossier → [fichiers] »."""
    result: dict[str, list[str]] = {}
    if not isinstance(payload, list):
        return result
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip().replace("\\", "/")
        folder = str(entry.get("type") or "").strip()
        if not name or name.startswith("."):
            continue
        if "/" in name:
            head, name = name.split("/", 1)
            folder = folder or head
        if not name:
            continue
        folder = _normalize_folder(folder or "root")
        result.setdefault(folder, []).append(name)
    return result


def _merge_files(*sources: dict[str, list[str]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for src in sources:
        for folder, names in (src or {}).items():
            pool = merged.setdefault(folder, [])
            for n in names:
                if n not in pool:
                    pool.append(n)
    return merged


def _pick(files: list[str], pattern: str) -> str:
    rx = re.compile(pattern, re.IGNORECASE)
    matches = [f for f in files if rx.search(Path(f).name)]
    return sorted(matches)[0] if matches else ""


# ---------------------------------------------------------------------------
# Inférence des moteurs exploitables
# ---------------------------------------------------------------------------
def infer_engines(files: dict[str, list[str]]) -> list[dict[str, Any]]:
    """Détermine quels workflows image sont réellement utilisables.

    - "zimage" : diffusion_models (z_image*) + text_encoders (qwen*) + vae (ae*)
    - "sd"     : chaque checkpoint classique (CheckpointLoaderSimple)
    - "flux"   : diffusion_models + vae + clip (t5 + clip_l) éventuel
    """
    engines: list[dict[str, Any]] = []

    diffusion = files.get("diffusion_models", []) + files.get("unet", [])
    text_encoders = files.get("text_encoders", [])
    vaes = files.get("vae", [])

    model = _pick(diffusion, r"z[-_ ]?image")
    encoder = _pick(text_encoders, r"qwen")
    ae = _pick(vaes, r"^ae")
    if model and encoder and ae:
        engines.append({
            "id": "zimage", "label": "Z-Image-Turbo", "workflow": "zimage",
            "model": model, "text_encoder": encoder, "vae": ae,
        })

    clips = files.get("clip", [])
    t5 = _pick(clips, r"t5|t5xxl")
    clip_l = _pick(clips, r"clip[-_]?l\b|clip_l")
    if not engines and diffusion and vaes and (t5 or clip_l):
        engines.append({
            "id": "flux", "label": "Flux (multi-fichiers)", "workflow": "flux",
            "model": _pick(diffusion, r"flux|schnell|dev|fill") or diffusion[0],
            "text_encoder": t5 or "", "clip_l": clip_l or "",
            "vae": _pick(vaes, r"^ae") or vaes[0],
        })

    for checkpoint in sorted(files.get("checkpoints", [])):
        is_sdxl = bool(re.search(r"(?:sdxl|sd[_ -]?xl|xl)", Path(checkpoint).name, re.IGNORECASE))
        engines.append({
            "id": "sdxl" if is_sdxl else "sd",
            "label": ("SDXL Qualité · " if is_sdxl else "") + Path(checkpoint).stem,
            "workflow": "sdxl_quality" if is_sdxl else "sd",
            "checkpoint": checkpoint, "architecture": "SDXL" if is_sdxl else "SD",
        })

    return engines


# ---------------------------------------------------------------------------
# Détection complète (API + disque), avec cache court
# ---------------------------------------------------------------------------
_cache: dict[str, Any] = {"ts": 0.0, "url": "", "result": None}
_lock = threading.Lock()
CACHE_TTL = 10.0


def detect_comfy_image_engines(
    base_url: str = DEFAULT_BASE_URL, models_dir: Any = None,
) -> dict[str, Any]:
    """Interroge ComfyUI (et le disque) pour lister les moteurs exploités.

    Retourne un dict stable : ok, reachable, base_url, detail, models_dir,
    files, engines. Ne lève jamais.
    """
    base = str(base_url or DEFAULT_BASE_URL).rstrip("/")

    with _lock:
        if _cache["result"] is not None and _cache["url"] == base \
                and time.time() - _cache["ts"] < CACHE_TTL:
            return _cache["result"]

    detail = ""
    reachable = False
    files: dict[str, list[str]] = {}

    ok, payload = http_json(f"{base}/system_stats", timeout=5)
    if ok:
        reachable = True
        api_files = files_from_api(http_json(f"{base}/models/config", timeout=5)[1])
        files = _merge_files(files, api_files)
        try:
            version = (payload or {}).get("system", {}).get("comfyui_version", "")
        except Exception:
            version = ""
        detail = f"ComfyUI {version}".strip()
    else:
        detail = f"ComfyUI injoignable : {str(payload)[:160]}"

    for d in _candidate_model_dirs(models_dir):
        found = scan_models_dir(d)
        if found:
            merged = _merge_files(files, found)
            if merged != files:
                detail += f" · modèles lus sur {d}"
            files = merged
            break

    engines = infer_engines(files)
    result = {
        "ok": reachable or bool(files),
        "reachable": reachable,
        "base_url": base,
        "detail": detail,
        "models_dir": next((str(d) for d in _candidate_model_dirs(models_dir) if d.is_dir()), ""),
        "files": files,
        "engines": engines,
    }
    with _lock:
        _cache.update(ts=time.time(), url=base, result=result)
    return result


def _candidate_model_dirs(explicit: Any) -> list[Path]:
    dirs: list[Path] = []
    if explicit:
        dirs.append(Path(str(explicit)).expanduser().resolve())
    env = os.getenv("JARVIS_COMFY_MODELS_DIR", "").strip()
    if env:
        dirs.append(Path(env).expanduser().resolve())
    dirs.extend(_default_model_dirs())
    unique: list[Path] = []
    seen: set[str] = set()
    for d in dirs:
        key = str(d).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique
