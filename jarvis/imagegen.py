"""Moteur hybride de génération d'images de JARVIS.

ComfyUI fournit deux profils : Z-Image-Turbo golden pour les previews et SDXL
Quality pour les rendus finaux. Le choix est déterministe (`auto`, `fast`,
`quality`) et les jobs persistés indiquent toujours le moteur réellement utilisé.

Rien n'est simulé : si ComfyUI n'est pas disponible, `available()` est faux et
JARVIS le dit clairement au lieu de partir en recherche web.

Chaîne d'un job :
    create() → running → (progress / preview)* → completed | failed
Chaque transition émet un événement SSE `image.generation.*` consommé par l'UI.
"""
from __future__ import annotations

import base64
import json
import random
import re
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .comfyui_detect import detect_comfy_image_engines
from .connectors import http_json
from .db import dumps, loads, new_id
from .image_pipeline import (
    IMAGE_PIPELINE_BUILD_ID, ComfyWorkflowCompiler, GenerationProfile,
    GenerationProfileFactory, GPUResourceManager, ImageIntent, ImageIntentAnalyzer,
    ImageModelRegistry, ImageQualityChecker, ImageQualityEvaluation, ModelMetadata,
    ModelSelector, NegativePromptBuilder, PromptComposer, WorkflowRegistry,
    STYLE_PROFILES, WorkflowSelector,
)
from .image_engines import (FAST_ENGINE, QUALITY_ENGINE, QUALITY_PRESETS,
                             SDXLWorkflowAdapter, choose_image_engine)
from .zimage_adapter import GOLDEN_WORKFLOW_ID, ZImageTurboAdapter

IMAGE_DIR = DATA_DIR / "generated" / "images"

# Étapes affichées dans l'UI (loader façon ChatGPT).
STAGE_LABELS = {
    "queued": "Préparation de l'image",
    "starting": "Préparation de l'image",
    "loading": "Chargement du modèle",
    "sampling": "Sampling",
    "decoding": "Décodage",
    "running": "Génération en cours",
    "rendering": "Rendu final",
    "upscaling": "Agrandissement",
    "evaluating": "Contrôle qualité",
    "finalizing": "Finalisation",
    "completed": "Image prête",
    "failed": "Échec",
    "cancelled": "Annulée",
}

BACKEND_LABELS = {
    "comfyui": "ComfyUI",
    "automatic1111": "AUTOMATIC1111",
    "openai_image": "OpenAI Images",
}


def _log(message: str) -> None:
    """Trace de diagnostic. Une console cp1252 ne doit jamais casser un job."""
    line = f"[IMAGEGEN] {message}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)
    except Exception:
        pass


class ImageBackendUnavailable(Exception):
    pass


class MediaHistory:
    """Persistent image-generation history backed by SQLite sidecars."""
    def __init__(self, core) -> None:
        self.core = core

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.core.db.query("SELECT * FROM media_history ORDER BY created_at DESC LIMIT ?",
                                  (max(1, min(500, int(limit))),))
        return [{**dict(row), "meta": loads(row["meta"], {}),
                 "quality_score": loads(row["quality_score"], {})} for row in rows]


# ---------------------------------------------------------------------------
# Construction de prompt : une demande courte devient un prompt exploitable.
# ---------------------------------------------------------------------------
_STYLE_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\baffiche|poster\b", re.I), "poster design, bold composition, print quality"),
    (re.compile(r"\bbanni[èe]re|banner\b", re.I), "wide banner composition, centred subject"),
    (re.compile(r"\bminiature|thumbnail\b", re.I), "youtube thumbnail, high contrast, punchy"),
    (re.compile(r"\bfond d[' ]?[ée]cran|wallpaper\b", re.I), "wallpaper, wide cinematic composition"),
    (re.compile(r"\blogo\b", re.I), "vector logo, flat, clean background"),
    (re.compile(r"\bpuff|vape|e-?cig|packshot|produit|bouteille|canette|flacon|packaging\b", re.I),
     "product packshot, studio lighting, clean seamless background, sharp focus, "
     "premium commercial product photography, high detail"),
]
_QUALITY = "highly detailed, 8k, sharp focus, professional lighting"

_REQUEST_NOISE = re.compile(
    r"^\s*(?:est[- ]ce que tu peux|peux[- ]tu|pourrais[- ]tu|tu peux|j'?aimerais(?: que tu)?|"
    r"je (?:veux|voudrais|souhaite)|s'?il te pla[îi]t|stp)\s+", re.I)
_VERB = re.compile(
    r"\b(?:cr[ée]{1,2}(?:r|e|es|ez|é|er)?[- ]?moi|cr[ée]e|cr[ée]er|g[ée]n[èé]re(?:r|s)?|g[ée]n[ée]rer|"
    r"fais(?:[- ]moi)?|faire|dessine(?:[- ]moi)?|produis|con[çc]ois|imagine|r[ée]alise|retouche|"
    r"modifie|am[ée]liore|upscale|agrandis)\b", re.I)
_OBJECT = re.compile(
    r"\b(?:une?|des|le|la|les|du|de la|l')\s+(?:image|photo|illustration|visuel|rendu|affiche|"
    r"poster|banni[èe]re|miniature|thumbnail|fond d'?[ée]cran|wallpaper|dessin|logo|mockup|"
    r"packshot|art|artwork)s?\b", re.I)
_LINK_WORDS = re.compile(r"^\s*(?:de|d'|du|des|pour|avec|repr[ée]sentant|montrant|qui montre|:)\s*", re.I)


def build_image_prompt(text: str) -> str:
    """Transforme « Crée-moi une image d'une puff JNR Black Ice » en prompt utilisable."""
    subject = _REQUEST_NOISE.sub("", (text or "").strip())
    subject = _VERB.sub(" ", subject)
    subject = _OBJECT.sub(" ", subject)
    subject = re.sub(r"\s+", " ", subject).strip(" .,;:!?-")
    subject = _LINK_WORDS.sub("", subject).strip(" .,;:!?-")
    if not subject:
        subject = re.sub(r"\s+", " ", (text or "").strip()) or "abstract artwork"

    styles = [hint for rx, hint in _STYLE_HINTS if rx.search(text or "")]
    parts = [subject] + styles + [_QUALITY]
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        key = part.lower()
        if key not in seen:
            seen.add(key)
            out.append(part)
    return ", ".join(out)[:1200]


DEFAULT_NEGATIVE = ("lowres, blurry, jpeg artifacts, watermark, text, signature, deformed, "
                    "bad anatomy, extra limbs, duplicate, cropped")


# Compatibility entry point.  Older callers can still import
# ``build_image_prompt`` but now receive the contextual V2 composition.
_INTENT_ANALYZER = ImageIntentAnalyzer()
_PROMPT_COMPOSER = PromptComposer()
_NEGATIVE_BUILDER = NegativePromptBuilder()


def build_image_prompt(text: str) -> str:
    # Compatibility name only. Golden execution must preserve the user's
    # text byte-for-byte apart from surrounding whitespace.
    return str(text or "").strip()


# ---------------------------------------------------------------------------
# Workflows API ComfyUI.
# Un workflow n'est PAS « SD CheckpointLoaderSimple » systématiquement :
# Z-Image-Turbo charge diffusion model (UNETLoader) + text encoder
# (CLIPLoader, type lumina2) + VAE (VAELoader), latent SD3 16 canaux.
# ---------------------------------------------------------------------------
def zimage_workflow(*, prompt: str, negative_prompt: str, width: int, height: int,
                    seed: int, steps: int, cfg: float, sampler: str, scheduler: str,
                    model: str, text_encoder: str, vae: str,
                    weight_dtype: str = "default", prefix: str = "jarvis") -> dict[str, Any]:
    return {
        "10": {"class_type": "UNETLoader", "inputs": {
            "unet_name": model, "weight_dtype": weight_dtype}},
        "11": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": text_encoder, "type": "lumina2", "device": "default"}},
        "12": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "13": {"class_type": "EmptySD3LatentImage", "inputs": {
            "width": int(width), "height": int(height), "batch_size": 1}},
        "14": {"class_type": "CLIPTextEncode",
               "inputs": {"text": prompt, "clip": ["11", 0]}},
        "15": {"class_type": "CLIPTextEncode",
               "inputs": {"text": negative_prompt, "clip": ["11", 0]}},
        "16": {"class_type": "KSampler", "inputs": {
            "seed": int(seed), "steps": int(steps), "cfg": float(cfg),
            "sampler_name": sampler, "scheduler": scheduler, "denoise": 1.0,
            "model": ["10", 0], "positive": ["14", 0], "negative": ["15", 0],
            "latent_image": ["13", 0]}},
        "17": {"class_type": "VAEDecode",
               "inputs": {"samples": ["16", 0], "vae": ["12", 0]}},
        "18": {"class_type": "SaveImage",
               "inputs": {"filename_prefix": prefix, "images": ["17", 0]}},
    }


def sd_workflow(*, prompt: str, negative_prompt: str, width: int, height: int,
                seed: int, steps: int, cfg: float, sampler: str, scheduler: str,
                checkpoint: str, prefix: str = "jarvis") -> dict[str, Any]:
    return {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "5": {"class_type": "EmptyLatentImage",
              "inputs": {"width": int(width), "height": int(height), "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode",
              "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode",
              "inputs": {"text": negative_prompt, "clip": ["4", 1]}},
        "3": {"class_type": "KSampler", "inputs": {
            "seed": int(seed), "steps": int(steps), "cfg": float(cfg),
            "sampler_name": sampler, "scheduler": scheduler, "denoise": 1.0,
            "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0],
            "latent_image": ["5", 0]}},
        "8": {"class_type": "VAEDecode",
              "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage",
              "inputs": {"filename_prefix": prefix, "images": ["8", 0]}},
    }


# Cache court de l'auto-détection d'un ComfyUI local (disponible sans connecteur).
_AUTODETECT: dict[str, Any] = {"ts": 0.0, "result": None}
_AUTODETECT_LOCK = threading.Lock()
_AUTODETECT_TTL = 15.0


def _public_engine_summaries(detection: dict[str, Any]) -> list[dict[str, Any]]:
    """Expose the two supported profiles without exposing legacy selectors."""
    out: list[dict[str, Any]] = []
    for engine in detection.get("engines") or []:
        if engine.get("id") == "zimage":
            out.append({"id": FAST_ENGINE, "label": "Z-Image-Turbo Fast",
                        "workflow": GOLDEN_WORKFLOW_ID,
                        "model": engine.get("model", "z_image_turbo_bf16.safetensors"),
                        "text_encoder": engine.get("text_encoder", "qwen_3_4b.safetensors"),
                        "vae": engine.get("vae", "ae.safetensors")})
        elif engine.get("id") == "sdxl":
            out.append({"id": QUALITY_ENGINE, "label": "SDXL Quality",
                        "workflow": "sdxl_quality",
                        "checkpoint": engine.get("checkpoint", ""),
                        "architecture": "SDXL"})
    return out


def _auto_detect_local_comfy() -> dict[str, Any] | None:
    """Un ComfyUI local qui répond est un moteur utilisable, sans connecteur."""
    now = time.time()
    with _AUTODETECT_LOCK:
        if _AUTODETECT["result"] is not None and now - _AUTODETECT["ts"] < _AUTODETECT_TTL:
            return _AUTODETECT["result"]
    import os as _os
    url = _os.getenv("JARVIS_COMFYUI_URL", "http://127.0.0.1:8188").strip() or "http://127.0.0.1:8188"
    det = detect_comfy_image_engines(url)
    result = None
    if det.get("ok") and det.get("reachable"):
        result = {"kind": "comfyui", "connector_id": "", "name": "ComfyUI (local)",
                  "label": "ComfyUI", "base_url": url,
                  "engines": _public_engine_summaries(det),
                  "config": {"models_dir": det.get("models_dir") or ""}}
    with _AUTODETECT_LOCK:
        _AUTODETECT.update(ts=now, result=result)
    return result


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------
class ImageGenManager:
    def __init__(self, core) -> None:
        self._core = core
        self._lock = threading.RLock()
        self.history = MediaHistory(core)
        self.intent_analyzer = ImageIntentAnalyzer()
        self.prompt_composer = PromptComposer()
        self.negative_builder = NegativePromptBuilder()
        self.model_selector = ModelSelector()
        self.workflow_selector = WorkflowSelector()
        self.profile_factory = GenerationProfileFactory()
        self.workflow_compiler = ComfyWorkflowCompiler()
        self.quality_checker = ImageQualityChecker(self._vision_quality)
        self.pipeline_build_id = IMAGE_PIPELINE_BUILD_ID
        try:
            IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def _vision_quality(self, *, image_path: str, intent: Any) -> dict[str, Any] | None:
        """Ask the configured Vision provider for a bounded quality report."""
        try:
            status = self._core.llm.vision_status()
            if not status.get("available"):
                return None
            raw = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
            response = self._core.llm.analyze_images(
                [raw],
                "Return JSON only with numeric values from 0 to 1 for "
                "subject_match, anatomy, sharpness, composition, style_match, overall, "
                "and a defects array. Evaluate whether the image matches this intent: "
                + json.dumps(intent, ensure_ascii=False),
                system="You are a strict image quality evaluator. Do not invent details; use JSON only.",
                temperature=0.0, max_tokens=512, timeout=180.0)
            if not response.ok:
                return None
            text = (response.text or "").strip()
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                return None
            parsed = json.loads(text[start:end + 1])
            if not isinstance(parsed, dict):
                return None
            fields = ("subject_match", "anatomy", "sharpness", "composition", "style_match", "overall")
            values = {key: max(0.0, min(1.0, float(parsed[key]))) for key in fields if parsed.get(key) is not None}
            if not values:
                return None
            values["defects"] = [str(x) for x in (parsed.get("defects") or [])][:12]
            return values
        except Exception as exc:
            _log(f"QUALITY CHECK unavailable: {str(exc)[:160]}")
            return None

    def analyze(self, prompt: str, *, mode: str = "generate") -> dict[str, Any]:
        """Public diagnostic endpoint used by Settings and integration tests."""
        return self.intent_analyzer.analyze(prompt, mode=mode).as_dict()

    def _plan(self, request: str, *, mode: str, backend: dict[str, Any], width: int,
              height: int, steps: int, seed: int, negative_prompt: str,
              engine_mode: str = "auto", source_path: str = "",
              context: dict[str, Any] | None = None) -> dict[str, Any]:
        if backend.get("kind") == "comfyui":
            settings = self._core.settings.section("image")
            decision = choose_image_engine(request, context=context, requested_mode=engine_mode)
            if decision.mode == QUALITY_ENGINE:
                if not settings.get("quality_enabled", True):
                    raise ValueError("Le moteur SDXL qualité est désactivé dans Settings → Image Generation.")
                if mode not in {"generate", "variation", "edit", "improve", "upscale"}:
                    raise ValueError("Le moteur SDXL qualité ne supporte pas cette opération.")
                endpoint = str(settings.get("quality_endpoint") or backend.get("base_url") or "").rstrip("/")
                if endpoint == "http://127.0.0.1:8188" and backend.get("base_url") not in {"", endpoint}:
                    endpoint = str(backend.get("base_url")).rstrip("/")
                detection = detect_comfy_image_engines(
                    endpoint, (backend.get("config") or {}).get("models_dir"))
                files = detection.get("files") or {}
                configured_checkpoint = str(settings.get("quality_checkpoint") or "").strip()
                sdxl = [e for e in (detection.get("engines") or []) if e.get("id") == "sdxl"]
                checkpoint = configured_checkpoint or str((sdxl[0] if sdxl else {}).get("checkpoint") or "")
                known_checkpoints = files.get("checkpoints", [])
                if configured_checkpoint and configured_checkpoint not in known_checkpoints:
                    raise ValueError(f"Checkpoint SDXL configuré introuvable dans ComfyUI : {configured_checkpoint}")
                if not checkpoint:
                    raise ValueError("Moteur SDXL qualité indisponible : installe un checkpoint SDXL et configure-le dans Settings → Image Generation.")
                preset_name = str(settings.get("quality_preset") or "quality_standard")
                preset = QUALITY_PRESETS.get(preset_name, QUALITY_PRESETS["quality_standard"])
                use_preset_size = int(width or 1024) == 1024 and int(height or 1024) == 1024
                if use_preset_size and preset_name == "quality_standard":
                    final_width = int(settings.get("quality_width") or preset["width"])
                    final_height = int(settings.get("quality_height") or preset["height"])
                else:
                    final_width = int(preset["width"] if use_preset_size else width or preset["width"])
                    final_height = int(preset["height"] if use_preset_size else height or preset["height"])
                final_steps = int(steps or settings.get("quality_steps") or preset["steps"])
                final_cfg = float(settings.get("quality_cfg") or preset["cfg"])
                workflow_key = "quality_img2img_workflow" if mode in {"edit", "improve", "upscale"} and source_path else "quality_txt2img_workflow"
                workflow_path = SDXLWorkflowAdapter.resolve_path(
                    str(settings.get(workflow_key) or ""),
                    "workflows/comfyui/sdxl_quality_img2img.json" if workflow_key.endswith("img2img_workflow")
                    else "workflows/comfyui/sdxl_quality_txt2img.json")
                adapter = SDXLWorkflowAdapter(workflow_path)
                generated_seed = int(seed or random.SystemRandom().randint(1, 2**31 - 1))
                if not (256 <= final_width <= 2048 and 256 <= final_height <= 2048):
                    raise ValueError("Dimensions hors limites pour le moteur SDXL qualité.")
                profile = {"quality_mode": preset_name, "engine_mode": QUALITY_ENGINE,
                           "engine_label": decision.label, "engine_reason": decision.reason,
                           "engine_endpoint": endpoint, "width": final_width, "height": final_height,
                           "steps": final_steps, "cfg": final_cfg,
                           "sampler": str(settings.get("quality_sampler") or "dpmpp_2m"),
                           "scheduler": str(settings.get("quality_scheduler") or "karras"),
                           "seed": generated_seed, "checkpoint": checkpoint,
                           "vae": str(settings.get("quality_vae") or ""),
                           "timeout": int(settings.get("quality_timeout_s") or 900),
                           "workflow_path": str(workflow_path)}
                return {"intent": None, "registry": None, "model": None,
                        "workflow": "sdxl_quality", "adapter": adapter, "profile": profile,
                        "prompt": request, "negative_prompt": str(negative_prompt or ""),
                        "engine_mode": QUALITY_ENGINE, "engine_decision": decision.__dict__,
                        "gpu": {}}

            if mode not in {"generate", "variation"}:
                raise ValueError("Le moteur rapide Z-Image-Turbo supporte la génération et les variantes text-to-image.")
            detection = detect_comfy_image_engines(backend.get("base_url"),
                                                    (backend.get("config") or {}).get("models_dir"))
            files = detection.get("files") or {}
            missing = [name for folder, name in (("diffusion_models", "z_image_turbo_bf16.safetensors"),
                                                   ("text_encoders", "qwen_3_4b.safetensors"),
                                                   ("vae", "ae.safetensors"))
                       if name not in files.get(folder, [])]
            if missing:
                raise ValueError("Modèle(s) du moteur rapide absent(s) : " + ", ".join(missing))
            adapter = ZImageTurboAdapter()
            generated_seed = int(seed or random.SystemRandom().randint(1, 2**31 - 1))
            final_width, final_height = int(width or 1024), int(height or 1024)
            if not (256 <= final_width <= 2048 and 256 <= final_height <= 2048):
                raise ValueError("Dimensions hors limites pour le moteur rapide.")
            endpoint = str(settings.get("fast_endpoint") or backend.get("base_url") or "").rstrip("/")
            profile = {"quality_mode": "fast", "engine_mode": FAST_ENGINE,
                       "engine_label": decision.label, "engine_reason": decision.reason,
                       "engine_endpoint": endpoint, "width": final_width, "height": final_height,
                       "steps": int(steps or 8), "cfg": 1.0, "sampler": "res_multistep",
                       "scheduler": "simple", "seed": generated_seed}
            return {"intent": None, "registry": None, "model": None,
                    "workflow": GOLDEN_WORKFLOW_ID, "adapter": adapter, "profile": profile,
                    "prompt": request, "negative_prompt": "", "engine_mode": FAST_ENGINE,
                    "engine_decision": decision.__dict__, "gpu": {}}
        intent = self.intent_analyzer.analyze(request, mode=mode)
        detection = {"engines": backend.get("engines") or [], "files": {}}
        if backend.get("kind") == "comfyui":
            detection = detect_comfy_image_engines(backend.get("base_url"),
                                                    (backend.get("config") or {}).get("models_dir"))
        registry = ImageModelRegistry(detection)
        snapshot = GPUResourceManager.snapshot()
        free_mb = (snapshot.get("vram") or {}).get("free_mb")
        quality = str(self._core.settings.get("image", "default_quality", "BALANCED"))
        settings = self._core.settings.section("image")
        preferred_style = str(settings.get("preferred_style") or "").strip()
        if preferred_style and preferred_style in STYLE_PROFILES:
            # Explicit request-specific profiles (portrait/product/poster…)
            # win over the global preference.
            if intent.profile == "cinematic":
                intent.profile = preferred_style
                intent.style = preferred_style
        preferred = str(settings.get("preferred_model") or "").strip()
        model = registry.get(preferred) if preferred else None
        if model is None:
            model = self.model_selector.select(intent, registry, free_vram_mb=free_mb, quality=quality)
        if mode in {"edit", "improve"} and not model.supports_img2img:
            raise ValueError(f"Le modèle installé ({model.name}) ne supporte pas l'édition img2img.")
        if mode == "upscale" and not detection.get("files", {}).get("upscale_models"):
            raise ValueError("Aucun vrai modèle d'upscale n'est installé dans ComfyUI.")
        if free_mb is not None and free_mb < 4000:
            # A 3070/8–10 GB must not attempt a heavyweight profile when the
            # measured free VRAM is already low.
            quality = "FAST"
        workflow = self.workflow_selector.select(intent, model, mode=mode)
        profile = self.profile_factory.create(intent, model, quality=quality, width=width, height=height,
                                              steps=steps, seed=seed, settings=settings)
        final_prompt = self.prompt_composer.compose(intent)
        final_negative = self.negative_builder.build(intent, negative_prompt)
        return {"intent": intent, "registry": registry, "model": model, "workflow": workflow,
                "profile": profile, "prompt": final_prompt, "negative_prompt": final_negative,
                "gpu": snapshot}

    # -- backends ----------------------------------------------------------
    def backends(self) -> list[dict[str, Any]]:
        """Backends réellement configurés (ou détectés), par ordre de préférence."""
        core = self._core
        image_settings = core.settings.section("image")
        out: list[dict[str, Any]] = []
        # ComfyUI is the transport. Fast and quality are two engine profiles
        # on that transport; the selected profile is decided per job.
        for ctype in ("comfyui",):
            conns = list(core.connectors.by_type(ctype, enabled_only=True))
            if ctype == "comfyui" and not conns:
                # Aucun connecteur : un ComfyUI local qui répond suffit.
                local = _auto_detect_local_comfy()
                if local and (image_settings.get("fast_enabled", True) or image_settings.get("quality_enabled", True)) and image_settings.get("auto_detect_comfy", True):
                    local["config"] = {**(local.get("config") or {}), **image_settings}
                    local["base_url"] = str(image_settings.get("fast_endpoint") or local.get("base_url") or "").rstrip("/")
                    out.append(local)
                continue
            for c in conns:
                raw = core.connectors.raw(c["id"]) or c
                cfg = raw.get("config") or {}
                merged_cfg = {**image_settings, **cfg}
                backend = {"kind": ctype, "connector_id": c["id"], "name": c["name"],
                           "label": BACKEND_LABELS[ctype],
                           "base_url": str(cfg.get("base_url") or cfg.get("url") or "").rstrip("/"),
                           "config": merged_cfg}
                if ctype == "comfyui":
                    detected = detect_comfy_image_engines(
                        backend["base_url"], merged_cfg.get("models_dir"))
                    backend["engines"] = _public_engine_summaries(detected)
                out.append(backend)
        preferred = str(core.settings.get("image", "backend", "auto") or "auto")
        if preferred != "auto":
            out.sort(key=lambda b: 0 if b["kind"] == preferred else 1)
        return out

    def available(self) -> bool:
        return bool(self.backends())

    def status(self) -> dict[str, Any]:
        backends = self.backends()
        detected_models: list[dict[str, Any]] = []
        for backend in backends:
            if backend.get("kind") != "comfyui":
                continue
            detection = detect_comfy_image_engines(backend.get("base_url"),
                                                   (backend.get("config") or {}).get("models_dir"))
            detected_models = [m.as_dict() for m in ImageModelRegistry(detection).available()]
            for model in detected_models:
                if model.get("engine_id") == "zimage":
                    model.update({"workflow": GOLDEN_WORKFLOW_ID,
                                  "supports_negative_prompt": False,
                                  "supports_img2img": False,
                                  "supports_inpainting": False,
                                  "supports_controlnet": False})
                elif model.get("engine_id") == "sdxl":
                    model.update({"workflow": "sdxl_quality",
                                  "supports_negative_prompt": True,
                                  "supports_img2img": True})
            break
        return {
            "available": bool(backends),
            "backends": [{k: v for k, v in b.items() if k != "config"} for b in backends],
            "pipeline": {"build_id": self.pipeline_build_id,
                         "mode": "hybrid",
                         "models": detected_models,
                         "workflows": [GOLDEN_WORKFLOW_ID, "sdxl_quality"]},
            "hint": ("" if backends else
                     "Aucun moteur image configuré. Ajoute ComfyUI et un moteur Fast "
                     "ou Quality dans Settings → Image Generation."),
        }

    # -- persistance des jobs ---------------------------------------------
    def _save(self, job: dict[str, Any]) -> None:
        job["updated_at"] = time.time()
        self._core.db.execute(
            "INSERT INTO image_jobs(id, conversation_id, message_id, mode, prompt, negative_prompt, "
            "backend, status, stage, progress, width, height, steps, seed, file_path, source_job_id, "
            "error, meta, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET prompt=excluded.prompt, negative_prompt=excluded.negative_prompt, "
            "width=excluded.width, height=excluded.height, steps=excluded.steps, seed=excluded.seed, "
            "status=excluded.status, stage=excluded.stage, "
            "progress=excluded.progress, file_path=excluded.file_path, error=excluded.error, "
            "backend=excluded.backend, meta=excluded.meta, updated_at=excluded.updated_at, "
            "message_id=excluded.message_id",
            (job["id"], job.get("conversation_id", ""), job.get("message_id", ""),
             job.get("mode", "generate"), job.get("prompt", ""), job.get("negative_prompt", ""),
             job.get("backend", ""), job.get("status", "queued"), job.get("stage", "queued"),
             float(job.get("progress") or 0), int(job.get("width") or 0), int(job.get("height") or 0),
             int(job.get("steps") or 0), int(job.get("seed") or 0), job.get("file_path", ""),
             job.get("source_job_id", ""), job.get("error", ""), dumps(job.get("meta") or {}),
             job.get("created_at") or time.time(), job["updated_at"]))

    def _save_history(self, job: dict[str, Any]) -> None:
        meta = job.get("meta") or {}
        model = meta.get("model") or {}
        evaluation = meta.get("quality_evaluation")
        self._core.db.execute(
            "INSERT OR REPLACE INTO media_history(id, job_id, media_type, original_prompt, final_prompt, "
            "negative_prompt, model, workflow, seed, width, height, steps, cfg, sampler, scheduler, "
            "output, quality_score, meta, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"history_{job['id']}", job["id"], "image", meta.get("original_prompt", ""),
             meta.get("final_prompt", job.get("prompt", "")), job.get("negative_prompt", ""),
             model.get("name") or model.get("id", ""), meta.get("workflow", ""), job.get("seed", 0),
             job.get("width", 0), job.get("height", 0), job.get("steps", 0),
             (meta.get("generation_profile") or {}).get("cfg", 0),
             (meta.get("generation_profile") or {}).get("sampler", ""),
             (meta.get("generation_profile") or {}).get("scheduler", ""), job.get("file_path", ""),
             dumps(evaluation or {}), dumps(meta), job.get("created_at") or time.time()))

    def get(self, job_id: str) -> dict[str, Any] | None:
        row = self._core.db.one("SELECT * FROM image_jobs WHERE id=?", (job_id,))
        if not row:
            return None
        job = {k: row[k] for k in row.keys()}
        job["meta"] = loads(job.get("meta"), {})
        job["url"] = f"/api/images/{job['id']}/file" if job.get("file_path") else ""
        job["stage_label"] = STAGE_LABELS.get(job.get("stage") or job.get("status", ""), "")
        job["backend_label"] = BACKEND_LABELS.get(job.get("backend", ""), job.get("backend", ""))
        return job

    def list(self, conversation_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        if conversation_id:
            rows = self._core.db.query(
                "SELECT id FROM image_jobs WHERE conversation_id=? ORDER BY created_at DESC LIMIT ?",
                (conversation_id, limit))
        else:
            rows = self._core.db.query(
                "SELECT id FROM image_jobs ORDER BY created_at DESC LIMIT ?", (limit,))
        return [j for j in (self.get(r["id"]) for r in rows) if j]

    def last_image(self, conversation_id: str = "") -> dict[str, Any] | None:
        """Dernière image réussie — base d'un image.edit / image.upscale."""
        if conversation_id:
            row = self._core.db.one(
                "SELECT id FROM image_jobs WHERE status='completed' AND file_path<>'' AND "
                "conversation_id=? ORDER BY created_at DESC LIMIT 1", (conversation_id,))
            if row:
                return self.get(row["id"])
        row = self._core.db.one(
            "SELECT id FROM image_jobs WHERE status='completed' AND file_path<>'' "
            "ORDER BY created_at DESC LIMIT 1")
        return self.get(row["id"]) if row else None

    def file(self, job_id: str) -> tuple[bytes, str] | None:
        job = self.get(job_id)
        if not job or not job.get("file_path"):
            return None
        path = Path(job["file_path"])
        if not path.is_file():
            return None
        ctype = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return path.read_bytes(), ctype

    # -- événements --------------------------------------------------------
    def _emit(self, event: str, job: dict[str, Any], **extra) -> None:
        meta = job.get("meta") or {}
        profile = meta.get("generation_profile") or {}
        payload = {
            "job_id": job["id"], "conversation_id": job.get("conversation_id", ""),
            "mode": job.get("mode", "generate"), "prompt": job.get("prompt", ""),
            "original_prompt": meta.get("original_prompt", meta.get("request", "")),
            "negative_prompt": job.get("negative_prompt", ""), "meta": meta,
            "model": meta.get("model", {}), "workflow": meta.get("workflow", ""),
            "engine_mode": meta.get("engine_mode", "auto"),
            "engine_label": meta.get("generation_profile", {}).get("engine_label", ""),
            "engine_reason": meta.get("generation_profile", {}).get("engine_reason", ""),
            "seed": job.get("seed", profile.get("seed", 0)),
            "steps": job.get("steps", profile.get("steps", 0)),
            "backend": job.get("backend", ""),
            "backend_label": BACKEND_LABELS.get(job.get("backend", ""), job.get("backend", "")),
            "status": job.get("status", ""), "stage": job.get("stage", ""),
            "stage_label": STAGE_LABELS.get(job.get("stage", ""), ""),
            "progress": round(float(job.get("progress") or 0), 3),
            "width": job.get("width"), "height": job.get("height"),
            "url": f"/api/images/{job['id']}/file" if job.get("file_path") else "",
            "error": job.get("error", ""),
        }
        payload.update(extra)
        self._core.events.emit(event, payload)

    def _set(self, job: dict[str, Any], *, status: str = "", stage: str = "",
             progress: float | None = None, emit: bool = True) -> None:
        if status:
            job["status"] = status
        if stage:
            job["stage"] = stage
        if progress is not None:
            job["progress"] = max(0.0, min(1.0, float(progress)))
        self._save(job)
        if emit:
            self._emit("image.generation.progress", job)
            _log(f"IMAGE JOB PROGRESS: {job['id']} {job.get('stage')} "
                 f"{round(float(job.get('progress') or 0) * 100)}%")

    # -- API principale ----------------------------------------------------
    def generate(
        self, prompt: str, *, mode: str = "generate", conversation_id: str = "",
        negative_prompt: str = "", width: int = 1024, height: int = 1024, steps: int = 0,
        seed: int = 0, source_job_id: str = "", source_path: str = "", raw_request: str = "",
        engine_mode: str = "auto", context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        backends = self.backends()
        request = (raw_request or prompt or "").strip()[:2000]
        initial_decision = choose_image_engine(request, context=context, requested_mode=engine_mode)
        job = {
            "id": new_id("img"), "conversation_id": conversation_id, "message_id": "",
            "mode": mode, "prompt": request,
            "negative_prompt": (negative_prompt or "")[:1400],
            "backend": backends[0]["kind"] if backends else "",
            "status": "queued", "stage": "queued", "progress": 0.0,
            "width": int(width or 1024), "height": int(height or 1024),
            "steps": int(steps or 0), "seed": int(seed or 0),
            "file_path": "", "source_job_id": source_job_id, "error": "",
            "meta": {"request": request, "build_id": self.pipeline_build_id,
                     "engine_mode": initial_decision.mode,
                     "engine_decision": initial_decision.__dict__}, "created_at": time.time(),
        }
        self._save(job)

        if not backends:
            job["error"] = ("ComfyUI n'est pas disponible pour le pipeline golden "
                            "Z-Image-Turbo (workflow officiel figé).")
            self._set(job, status="failed", stage="failed", emit=False)
            self._emit("image.generation.failed", job)
            _log(f"IMAGE JOB FAILED: {job['id']} aucun backend")
            raise ImageBackendUnavailable(job["error"])

        # Build a real execution plan from the first usable backend.  The
        # selected model/workflow/settings are persisted before submission so
        # failures remain diagnosable and never look like a generic prompt.
        plan = None
        planning_error = ""
        for candidate in backends:
            try:
                if candidate.get("kind") == "comfyui":
                    plan = self._plan(request, mode=mode, backend=candidate, width=width,
                                      height=height, steps=steps, seed=seed,
                                      negative_prompt=negative_prompt, engine_mode=engine_mode,
                                      source_path=source_path, context=context)
                else:
                    # Non-ComfyUI backends still receive contextual prompts;
                    # their own model registry is not exposed by this app.
                    intent = self.intent_analyzer.analyze(request, mode=mode)
                    final_prompt = self.prompt_composer.compose(intent)
                    final_negative = self.negative_builder.build(intent, negative_prompt)
                    plan = {"intent": intent, "prompt": final_prompt,
                            "negative_prompt": final_negative, "model": None,
                            "workflow": None, "profile": None, "gpu": {}}
                job["backend"] = candidate["kind"]
                break
            except Exception as exc:
                planning_error = str(exc)[:400]
                _log(f"IMAGE PLAN {candidate.get('kind')} KO: {planning_error}")
        if plan is None:
            job["error"] = planning_error or "Impossible de construire un plan de génération valide."
            self._set(job, status="failed", stage="failed", emit=False)
            self._emit("image.generation.failed", job)
            return self.get(job["id"]) or job

        job["prompt"] = plan["prompt"][:2000]
        job["negative_prompt"] = plan["negative_prompt"][:1400]
        profile = plan.get("profile")
        if profile is not None:
            job["width"] = int(profile.get("width") if isinstance(profile, dict) else profile.width)
            job["height"] = int(profile.get("height") if isinstance(profile, dict) else profile.height)
            job["steps"] = int(profile.get("steps") if isinstance(profile, dict) else profile.steps)
            job["seed"] = int(profile.get("seed") if isinstance(profile, dict) else profile.seed)
        model = plan.get("model")
        workflow = plan.get("workflow")
        job["meta"].update({
            "original_prompt": request,
            "final_prompt": job["prompt"],
            "negative_prompt": job["negative_prompt"],
            "intent": plan["intent"].as_dict() if plan.get("intent") else {},
            "model": model.as_dict() if model else (plan["adapter"].metadata() if plan.get("adapter") else {}),
            "workflow": workflow.id if hasattr(workflow, "id") else (workflow or ""),
            "generation_profile": profile.as_dict() if hasattr(profile, "as_dict") else (profile or {}),
            "engine_mode": plan.get("engine_mode") or engine_mode,
            "engine_decision": plan.get("engine_decision") or {},
            "gpu": plan.get("gpu") or {},
        })
        self._save(job)
        profile_label = (profile.get("engine_label") if isinstance(profile, dict) else "") or ""
        _log(f"IMAGE JOB STARTED: {job['id']} backend={job['backend']} engine={job['meta'].get('engine_mode')} "
             f"label={profile_label} mode={mode} model={(model.name if model else 'configured')} "
             f"workflow={(workflow.id if hasattr(workflow, 'id') else (workflow or 'native'))}")
        self._emit("image.generation.started", job)
        self._set(job, status="running", stage="loading", progress=0.02)

        last_error = ""
        for backend in backends:
            job["backend"] = backend["kind"]
            self._save(job)
            try:
                data = self._run_backend(backend, job, source_path=source_path)
            except Exception as exc:                     # backend indisponible → suivant
                current = self.get(job["id"]) or job
                if (current.get("meta") or {}).get("cancel_requested"):
                    job["status"], job["stage"] = "cancelled", "cancelled"
                    job["error"] = "Génération annulée par l'utilisateur."
                    self._set(job, emit=False)
                    self._emit("image.generation.cancelled", job)
                    return self.get(job["id"]) or job
                last_error = str(exc)[:400]
                _log(f"IMAGE BACKEND {backend['kind']} KO: {last_error}")
                continue
            if not data:
                last_error = last_error or "Le moteur n'a renvoyé aucune image."
                continue
            output_dir = IMAGE_DIR / job["id"]
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / "image.png"
            path.write_bytes(data)
            job["file_path"] = str(path)
            self._set(job, stage="evaluating", progress=0.97)
            evaluation = None if plan.get("engine_mode") == FAST_ENGINE else self.quality_checker.evaluate(str(path), plan.get("intent"))
            if evaluation is not None:
                job["meta"]["quality_evaluation"] = evaluation.as_dict()
                self._emit("image.generation.quality", job, quality=evaluation.as_dict())
            sidecar = output_dir / "image.json"
            sidecar.write_text(json.dumps({**job, "url": ""}, ensure_ascii=False,
                                           indent=2, default=str), encoding="utf-8")
            self._set(job, stage="finalizing", progress=0.98)
            self._set(job, status="completed", stage="completed", progress=1.0, emit=False)
            sidecar.write_text(json.dumps({**job, "url": ""}, ensure_ascii=False,
                                           indent=2, default=str), encoding="utf-8")
            self._save_history(job)
            try:
                self._core.active_task_context["active_media_asset"] = job["id"]
            except Exception:
                pass
            self._emit("image.generation.completed", job, bytes=len(data))
            _log(f"IMAGE JOB COMPLETED: {job['id']} ({len(data)} octets)")
            return self.get(job["id"]) or job

        job["error"] = last_error or "Le moteur image n'a pas répondu."
        self._set(job, status="failed", stage="failed", emit=False)
        self._emit("image.generation.failed", job)
        _log(f"IMAGE JOB FAILED: {job['id']} {job['error']}")
        return self.get(job["id"]) or job

    def _run_backend(self, backend: dict[str, Any], job: dict[str, Any],
                     source_path: str = "") -> bytes | None:
        kind = backend["kind"]
        if kind == "comfyui":
            return ComfyUIBackend(self, backend).run(job, source_path)
        if kind == "automatic1111":
            return A1111Backend(self, backend).run(job, source_path)
        if kind == "openai_image":
            return OpenAIImageBackend(self, backend).run(job, source_path)
        raise ImageBackendUnavailable(f"Backend inconnu : {kind}")

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        job = self.get(job_id)
        if not job or job.get("status") in {"completed", "failed", "cancelled"}:
            return job
        job["meta"] = job.get("meta") or {}
        job["meta"]["cancel_requested"] = True
        self._save(job)
        self._emit("image.generation.cancel_requested", job)
        return job

    # -- aperçu ------------------------------------------------------------
    def emit_preview(self, job: dict[str, Any], data: bytes, mime: str = "image/jpeg",
                     *, step: int = 0, total_steps: int = 0) -> None:
        if not data or len(data) > 3_000_000:
            return
        b64 = base64.b64encode(data).decode("ascii")
        preview = f"data:{mime};base64,{b64}"
        self._emit("image.generation.preview", job, preview=preview,
                   preview_url=preview, step=step, total_steps=total_steps)
        _log(f"IMAGE PREVIEW RECEIVED: {job['id']} ({len(data)} octets)")

    def attach_message(self, job_id: str, message_id: str) -> None:
        self._core.db.execute("UPDATE image_jobs SET message_id=? WHERE id=?", (message_id, job_id))


# ---------------------------------------------------------------------------
# Backend ComfyUI — progression + aperçus via WebSocket
# ---------------------------------------------------------------------------
class ComfyUIBackend:
    def __init__(self, manager: ImageGenManager, backend: dict[str, Any]) -> None:
        self.m = manager
        self.base = (backend.get("base_url") or "http://127.0.0.1:8188").rstrip("/")
        self.cfg = backend.get("config") or {}

    # -- helpers HTTP ------------------------------------------------------
    def _get_json(self, path: str, timeout: float = 10.0) -> Any:
        ok, payload = http_json(f"{self.base}{path}", timeout=timeout)
        if not ok:
            raise ImageBackendUnavailable(f"ComfyUI injoignable : {payload}")
        return payload

    # -- moteurs -----------------------------------------------------------
    def _engines(self) -> dict[str, Any]:
        if getattr(self, "_detection", None) is None:
            self._detection = detect_comfy_image_engines(self.base, self.cfg.get("models_dir"))
        return self._detection

    def engines(self) -> list[dict[str, Any]]:
        return list((self._engines() or {}).get("engines") or [])

    def _select_engine(self) -> dict[str, Any]:
        """Choisit le workflow réellement exploitable (Z-Image d'abord,
        sinon checkpoint classique). Échoue avec un message précis, jamais
        un faux « aucun checkpoint »."""
        det = self._engines()
        engines = det.get("engines") or []
        forced = str(self.cfg.get("engine") or "").strip().casefold()
        if forced:
            for e in engines:
                if e["id"] == forced:
                    return e
        requested = (((getattr(self, "_job", {}) or {}).get("meta") or {}).get("model") or {})
        requested_id = str(requested.get("engine_id") or "")
        requested_name = str(requested.get("id") or "")
        for e in engines:
            if requested_id and e.get("id") == requested_id:
                if not requested_name or requested_name in {e.get("model"), e.get("checkpoint")}:
                    return e
        for e in engines:
            if e["id"] == "zimage":
                return e
        for e in engines:
            if e["id"] == "sd":
                return e
        hint = det.get("detail") or ""
        raise ImageBackendUnavailable(
            "Aucun modèle de génération exploitable dans ComfyUI "
            "(ni checkpoint classique, ni trio diffusion_models + text_encoders + vae). "
            + (f"{hint}. " if hint else ""))

    def _workflow(self, job: dict[str, Any], engine: dict[str, Any]) -> dict[str, Any]:
        self._job = job
        profile = (job.get("meta") or {}).get("generation_profile") or {}
        steps = job.get("steps") or int(profile.get("steps") or self.cfg.get("steps") or 8)
        seed = job.get("seed") or int(profile.get("seed") or uuid.uuid4().int % 2**31)
        shared = {
            "prompt": job["prompt"], "negative_prompt": job["negative_prompt"],
            "width": job["width"], "height": job["height"],
            "seed": seed, "steps": steps,
            "sampler": str(profile.get("sampler") or self.cfg.get("sampler") or "euler"),
            "scheduler": str(profile.get("scheduler") or self.cfg.get("scheduler") or "simple"),
        }
        if engine["id"] == "zimage":
            shared["cfg"] = float(profile.get("cfg") or self.cfg.get("cfg") or 1.0)
            workflow = zimage_workflow(**shared, model=engine["model"],
                                   text_encoder=engine["text_encoder"], vae=engine["vae"],
                                   weight_dtype=str(self.cfg.get("weight_dtype") or "default"))
            self.m.workflow_compiler.validate(workflow, registry=ImageModelRegistry(self._engines()))
            return workflow
        shared["cfg"] = float(self.cfg.get("cfg") or 7.0)
        shared["sampler"] = str(profile.get("sampler") or self.cfg.get("sampler") or "dpmpp_2m")
        shared["scheduler"] = str(profile.get("scheduler") or self.cfg.get("scheduler") or "karras")
        workflow = sd_workflow(**shared, checkpoint=engine.get("checkpoint") or "")
        self.m.workflow_compiler.validate(workflow, registry=ImageModelRegistry(self._engines()))
        return workflow

    def _upload_image(self, source_path: str) -> str:
        """Upload a local source so a ComfyUI img2img workflow can load it."""
        source = Path(source_path)
        if not source.is_file():
            raise ImageBackendUnavailable(f"Image source introuvable pour img2img : {source}")
        boundary = "----JARVIS" + uuid.uuid4().hex
        content_type = "image/png" if source.suffix.casefold() == ".png" else "image/jpeg"
        body = (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"image\"; filename=\"jarvis_source{source.suffix or '.png'}\"\r\n"
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8") + source.read_bytes() + f"\r\n--{boundary}--\r\n".encode("ascii")
        request = urllib.request.Request(
            f"{self.base}/upload/image", data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                     "Content-Length": str(len(body))})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise ImageBackendUnavailable(f"ComfyUI : upload img2img impossible ({exc}).") from exc
        name = str((payload or {}).get("name") or "")
        if not name:
            raise ImageBackendUnavailable(f"ComfyUI : upload img2img sans nom de fichier ({payload}).")
        return name

    # -- exécution ---------------------------------------------------------
    def run(self, job: dict[str, Any], source_path: str = "") -> bytes | None:
        profile = (job.get("meta") or {}).get("generation_profile") or {}
        engine_mode = str((job.get("meta") or {}).get("engine_mode") or FAST_ENGINE)
        self.base = str(profile.get("engine_endpoint") or self.base).rstrip("/")
        # A configured connector is not proof that the service is alive.
        # Refuse before queueing when the health check fails.
        ok, payload = http_json(f"{self.base}/system_stats", timeout=8)
        if not ok:
            raise ImageBackendUnavailable(f"ComfyUI indisponible : {str(payload)[:250]}")
        self._job = job
        client_id = uuid.uuid4().hex
        if engine_mode == QUALITY_ENGINE and profile.get("timeout"):
            self.cfg = {**self.cfg, "timeout": int(profile["timeout"])}
        if engine_mode == QUALITY_ENGINE:
            adapter = SDXLWorkflowAdapter(profile.get("workflow_path") or "")
            uploaded_name = self._upload_image(source_path) if source_path and job.get("mode") in {"edit", "improve"} else ""
            workflow = adapter.compile(
                prompt=job["prompt"], negative_prompt=job.get("negative_prompt", ""),
                width=job["width"], height=job["height"], seed=job["seed"],
                steps=job["steps"] or int(profile.get("steps") or 32),
                cfg=float(profile.get("cfg") or 7.0),
                sampler=str(profile.get("sampler") or "dpmpp_2m"),
                scheduler=str(profile.get("scheduler") or "karras"),
                checkpoint=str(profile.get("checkpoint") or ""),
                vae=str(profile.get("vae") or ""), input_image=uploaded_name)
            info = adapter.metadata()
            job["meta"]["workflow"] = "sdxl_quality"
            job["meta"]["quality_input_image"] = uploaded_name
        else:
            adapter = ZImageTurboAdapter()
            workflow = adapter.compile(job["prompt"], width=job["width"], height=job["height"],
                                       seed=job["seed"], steps=job["steps"] or None)
            info = adapter.metadata()
            job["meta"]["workflow"] = GOLDEN_WORKFLOW_ID
        _log(f"[image] original_prompt={job.get('meta', {}).get('original_prompt', '')}")
        _log(f"[image] injected_prompt={job['prompt']}")
        _log(f"[image] engine={engine_mode} reason={profile.get('engine_reason', '')}")
        _log(f"[image] workflow={job['meta'].get('workflow', '')}")
        _log(f"[image] model={profile.get('checkpoint') or info.get('diffusion_model', '')}")
        _log(f"[image] vae={profile.get('vae') or info.get('vae', '')}")
        _log(f"[image] seed={job['seed']}")
        _log(f"[image] negative_prompt={'present' if job.get('negative_prompt') else 'empty'}")
        ws = None
        try:
            from .ws_client import WebSocketClient
            scheme = "wss" if self.base.startswith("https") else "ws"
            host = self.base.split("://", 1)[-1]
            ws = WebSocketClient(f"{scheme}://{host}/ws?clientId={client_id}", timeout=15)
            ws.connect()
            ws.settimeout(120)
        except Exception as exc:
            _log(f"ComfyUI : WebSocket indisponible ({exc}) — suivi par sondage HTTP.")
            ws = None

        ok, payload = http_json(f"{self.base}/prompt", method="POST",
                                body={"prompt": workflow, "client_id": client_id}, timeout=30)
        if not ok:
            if ws:
                ws.close()
            raise ImageBackendUnavailable(f"ComfyUI a refusé le workflow : {payload}")
        prompt_id = str((payload or {}).get("prompt_id") or "")
        if not prompt_id:
            if ws:
                ws.close()
            raise ImageBackendUnavailable("ComfyUI n'a pas renvoyé de prompt_id.")
        job["meta"]["prompt_id"] = prompt_id
        job["meta"]["workflow"] = GOLDEN_WORKFLOW_ID
        self.m._save(job)
        self.m._emit("image.generation.queued", job, prompt_id=prompt_id)
        self.m._set(job, stage="running", progress=0.08)

        try:
            if ws is not None:
                self._follow_ws(ws, job, prompt_id)
            else:
                self._follow_poll(job, prompt_id)
        finally:
            if ws is not None:
                ws.close()
        self.m._set(job, stage="rendering", progress=0.94)
        data = self._fetch_result(prompt_id)
        job["meta"]["output"] = getattr(self, "_last_output", {})
        self.m._save(job)
        return data

    def _follow_ws(self, ws, job: dict[str, Any], prompt_id: str) -> None:
        deadline = time.time() + float(self.cfg.get("timeout") or 600)
        while time.time() < deadline:
            current = self.m.get(job["id"])
            if current and (current.get("meta") or {}).get("cancel_requested"):
                try:
                    http_json(f"{self.base}/interrupt", method="POST", body={}, timeout=8)
                except Exception:
                    pass
                raise ImageBackendUnavailable("ComfyUI : interruption demandée")
            try:
                kind, payload = ws.recv()
            except ImageBackendUnavailable:
                raise
            except Exception:
                return
            if kind == "binary":
                # En-tête ComfyUI : 4 octets type d'événement + 4 octets type d'image.
                if len(payload) > 8:
                    image_type = int.from_bytes(payload[4:8], "big")
                    mime = "image/png" if image_type == 2 else "image/jpeg"
                    self.m.emit_preview(job, payload[8:], mime)
                continue
            try:
                message = json.loads(payload.decode("utf-8"))
            except Exception:
                continue
            mtype = message.get("type")
            data = message.get("data") or {}
            if data.get("prompt_id") and data["prompt_id"] != prompt_id:
                continue
            if mtype == "progress":
                value, maximum = float(data.get("value") or 0), float(data.get("max") or 1)
                if maximum > 0:
                    self.m._set(job, stage="sampling", progress=0.10 + 0.82 * (value / maximum))
            elif mtype == "executing" and data.get("node") is None:
                return                                    # exécution terminée
            elif mtype == "execution_error":
                detail = data.get("exception_message") or "erreur d'exécution"
                raise ImageBackendUnavailable(f"ComfyUI : {detail}")
        raise ImageBackendUnavailable("ComfyUI : délai dépassé.")

    def _follow_poll(self, job: dict[str, Any], prompt_id: str) -> None:
        deadline = time.time() + float(self.cfg.get("timeout") or 600)
        progress = 0.10
        while time.time() < deadline:
            current = self.m.get(job["id"])
            if current and (current.get("meta") or {}).get("cancel_requested"):
                try:
                    http_json(f"{self.base}/interrupt", method="POST", body={}, timeout=8)
                except Exception:
                    pass
                raise ImageBackendUnavailable("ComfyUI : interruption demandée")
            history = self._get_json(f"/history/{prompt_id}", timeout=15)
            if isinstance(history, dict) and prompt_id in history:
                return
            progress = min(0.90, progress + 0.03)
            self.m._set(job, stage="sampling", progress=progress)
            time.sleep(1.2)
        raise ImageBackendUnavailable("ComfyUI : délai dépassé.")

    def _fetch_result(self, prompt_id: str) -> bytes | None:
        for _ in range(30):
            history = self._get_json(f"/history/{prompt_id}", timeout=15)
            entry = (history or {}).get(prompt_id) if isinstance(history, dict) else None
            if entry:
                for node_output in (entry.get("outputs") or {}).values():
                    for image in node_output.get("images") or []:
                        self._last_output = {"prompt_id": prompt_id, **image}
                        return self._download(image)
            time.sleep(0.5)
        return None

    def _download(self, image: dict[str, Any]) -> bytes | None:
        from urllib.parse import urlencode
        query = urlencode({"filename": image.get("filename", ""),
                           "subfolder": image.get("subfolder", ""),
                           "type": image.get("type", "output")})
        try:
            with urllib.request.urlopen(f"{self.base}/view?{query}", timeout=60) as resp:
                return resp.read()
        except Exception as exc:
            raise ImageBackendUnavailable(f"ComfyUI : téléchargement impossible ({exc}).")


# ---------------------------------------------------------------------------
# Backend AUTOMATIC1111 — /sdapi/v1, aperçu live via /progress
# ---------------------------------------------------------------------------
class A1111Backend:
    def __init__(self, manager: ImageGenManager, backend: dict[str, Any]) -> None:
        self.m = manager
        self.base = (backend.get("base_url") or "http://127.0.0.1:7860").rstrip("/")
        self.cfg = backend.get("config") or {}

    def run(self, job: dict[str, Any], source_path: str = "") -> bytes | None:
        ok, _ = http_json(f"{self.base}/sdapi/v1/options", timeout=8)
        if not ok:
            raise ImageBackendUnavailable("AUTOMATIC1111 injoignable (API activée avec --api ?).")

        body: dict[str, Any] = {
            "prompt": job["prompt"], "negative_prompt": job["negative_prompt"],
            "width": job["width"], "height": job["height"],
            "steps": job.get("steps") or int(self.cfg.get("steps") or 25),
            "cfg_scale": float(self.cfg.get("cfg") or 7.0),
            "sampler_name": str(self.cfg.get("sampler") or "DPM++ 2M Karras"),
            "seed": job.get("seed") or -1,
        }
        endpoint = "/sdapi/v1/txt2img"
        if job.get("mode") in {"edit", "upscale"} and source_path:
            source = Path(source_path)
            if source.is_file():
                body["init_images"] = [base64.b64encode(source.read_bytes()).decode("ascii")]
                body["denoising_strength"] = float(
                    self.cfg.get("denoise") or (0.3 if job["mode"] == "upscale" else 0.6))
                endpoint = "/sdapi/v1/img2img"

        result: dict[str, Any] = {}
        done = threading.Event()

        def call() -> None:
            try:
                ok2, payload = http_json(f"{self.base}{endpoint}", method="POST", body=body, timeout=900)
                result["ok"], result["payload"] = ok2, payload
            except Exception as exc:
                result["ok"], result["payload"] = False, str(exc)
            finally:
                done.set()

        threading.Thread(target=call, daemon=True, name="jarvis-a1111").start()
        self.m._set(job, stage="running", progress=0.08)

        last_preview = 0.0
        while not done.wait(1.0):
            ok2, prog = http_json(f"{self.base}/sdapi/v1/progress?skip_current_image=false", timeout=8)
            if not ok2 or not isinstance(prog, dict):
                continue
            value = float(prog.get("progress") or 0)
            self.m._set(job, stage="running", progress=0.08 + 0.85 * value)
            current = prog.get("current_image")
            if current and time.time() - last_preview > 1.5:
                last_preview = time.time()
                try:
                    self.m.emit_preview(job, base64.b64decode(current), "image/png")
                except Exception:
                    pass
        if not result.get("ok"):
            raise ImageBackendUnavailable(f"AUTOMATIC1111 : {str(result.get('payload'))[:250]}")
        images = (result.get("payload") or {}).get("images") or []
        if not images:
            return None
        return base64.b64decode(images[0])


# ---------------------------------------------------------------------------
# Backend OpenAI Images — pas de progression native : étapes annoncées
# ---------------------------------------------------------------------------
class OpenAIImageBackend:
    def __init__(self, manager: ImageGenManager, backend: dict[str, Any]) -> None:
        self.m = manager
        self.base = (backend.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        self.cfg = backend.get("config") or {}
        self.connector_id = backend["connector_id"]

    def run(self, job: dict[str, Any], source_path: str = "") -> bytes | None:
        api_key = self.m._core.vault.get(self.connector_id, "api_key", "")
        if not api_key:
            raise ImageBackendUnavailable("Clé API OpenAI absente.")
        size = self._size(job["width"], job["height"])
        model = str(self.cfg.get("image_model") or "gpt-image-1")

        stop = threading.Event()

        def tick() -> None:
            progress = 0.10
            while not stop.wait(1.5):
                progress = min(0.88, progress + 0.05)
                self.m._set(job, stage="running", progress=progress)

        threading.Thread(target=tick, daemon=True, name="jarvis-openai-image").start()
        try:
            ok, payload = http_json(
                f"{self.base}/images/generations", method="POST",
                headers={"Authorization": f"Bearer {api_key}"},
                body={"model": model, "prompt": job["prompt"][:4000], "size": size, "n": 1},
                timeout=300)
        finally:
            stop.set()
        if not ok:
            raise ImageBackendUnavailable(f"OpenAI Images : {str(payload)[:250]}")
        items = (payload or {}).get("data") or []
        if not items:
            return None
        item = items[0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        url = item.get("url")
        if not url:
            return None
        with urllib.request.urlopen(url, timeout=120) as resp:
            return resp.read()

    @staticmethod
    def _size(width: int, height: int) -> str:
        ratio = (width or 1024) / max(1, height or 1024)
        if ratio > 1.2:
            return "1536x1024"
        if ratio < 0.85:
            return "1024x1536"
        return "1024x1024"
