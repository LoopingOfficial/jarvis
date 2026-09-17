"""Image Generation Engine V2.

This module contains the decisions that must happen *before* a ComfyUI
workflow is submitted.  It deliberately has no dependency on an LLM: a short
request remains useful when the prompt-composition model is unavailable, and
the original user intent is kept in every plan and sidecar.
"""
from __future__ import annotations

import json
import os
import random
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

IMAGE_PIPELINE_BUILD_ID = "IMAGE_HYBRID_SDXL_20260911_A"


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = _norm(value)
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


@dataclass
class ImageIntent:
    original_prompt: str
    subject: str
    style: str = "high quality stylized realism"
    scene: str = ""
    lighting: str = ""
    composition: str = ""
    colors: list[str] = field(default_factory=list)
    camera: str = ""
    mood: str = ""
    quality: str = "high"
    aspect_ratio: str = "1:1"
    reference_images: list[str] = field(default_factory=list)
    editing_mode: str = "generate"
    profile: str = "cinematic"
    exact_text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ImageIntentAnalyzer:
    """Extract visual intent from French or English user wording.

    This is intentionally conservative: unknown nouns are retained instead of
    being replaced with generic "artwork".  The small vocabulary below covers
    common requests and is augmented by phrase cleanup for everything else.
    """

    _COLORS = {
        "rose": "pink", "pink": "pink", "bleu": "blue", "bleue": "blue",
        "blue": "blue", "rouge": "red", "red": "red", "vert": "green",
        "verte": "green", "green": "green", "jaune": "yellow", "yellow": "yellow",
        "noir": "black", "noire": "black", "black": "black", "blanc": "white",
        "blanche": "white", "white": "white", "violet": "purple", "violette": "purple",
        "orange": "orange", "doré": "gold", "dorée": "gold", "gold": "gold",
    }
    _NOUNS = {
        "requin": "shark", "sharks": "sharks", "chat": "cat", "chien": "dog",
        "loup": "wolf", "oiseau": "bird", "cheval": "horse", "lion": "lion",
        "tigre": "tiger", "homme": "man", "femme": "woman", "visage": "face",
        "portrait": "portrait", "voiture": "car", "bouteille": "bottle",
        "canette": "can", "robot": "robot", "maison": "house", "forêt": "forest",
        "chaton": "kitten", "paysage": "landscape", "produit": "product",
    }
    _STOP = re.compile(
        r"\b(?:montre(?:[- ]moi)?|show(?: me)?|cr[ée]e(?:[- ]moi)?|g[ée]n[èé]re(?:[- ]moi)?|"
        r"fais(?:[- ]moi)?|dessine(?:[- ]moi)?|produis|imagine|une?|un|des|the|a|an|image|"
        r"photo|illustration|visuel|rendu|s'il te plaît|please|qui montre|montrant|de|d')\b",
        re.IGNORECASE,
    )

    def analyze(self, prompt: str, *, mode: str = "generate",
                reference_images: list[str] | None = None) -> ImageIntent:
        original = _norm(prompt)
        low = original.casefold()
        colors = _unique(self._COLORS[word] for word in re.findall(r"[\wÀ-ÿ'-]+", low)
                         if word in self._COLORS)

        translated = original
        for source, target in sorted({**self._COLORS, **self._NOUNS}.items(), key=lambda pair: -len(pair[0])):
            translated = re.sub(rf"\b{re.escape(source)}\b", target, translated, flags=re.IGNORECASE)
        subject = self._STOP.sub(" ", translated)
        subject = re.sub(r"\s+", " ", subject).strip(" .,;:!?-\"")
        subject = subject or translated.strip() or "abstract visual"

        is_poster = bool(re.search(r"\b(affiche|poster|flyer)\b", low))
        is_product = bool(re.search(r"\b(produit|packshot|bouteille|canette|product)\b", low))
        is_portrait = bool(re.search(r"\b(portrait|visage|face)\b", low))
        is_banner = bool(re.search(r"\b(banni[èe]re|banner|wallpaper|fond d'écran)\b", low))
        is_transparent = bool(re.search(r"\b(transparent|fond transparent|alpha|d[ée]tour[ée])\b", low))
        cartoon = bool(re.search(r"\b(cartoon|cartoon|dessin anim[ée]|stylis[ée]|illustration)\b", low))
        cinematic = bool(re.search(r"\b(cinematic|cin[ée]matique|film|cin[ée])\b", low))
        edit = mode in {"edit", "variation", "improve"} or bool(re.search(
            r"\b(retouche|modifie|change|rends|am[ée]liore|remplace|edit|variation)\b", low))

        style = "high quality stylized realism"
        profile = "cinematic"
        if cartoon:
            style, profile = "clean expressive 3D cartoon illustration", "3d_animation"
        elif is_poster:
            style, profile = "polished editorial poster background", "poster"
        elif is_product:
            style, profile = "photorealistic commercial product photography", "product"
        elif is_portrait:
            style, profile = "cinematic photorealistic portrait", "portrait"
        elif is_transparent:
            style, profile = "clean isolated game-ready asset", "transparent_asset"
        elif cinematic:
            style = "cinematic stylized realism"

        scene = "underwater ocean" if re.search(r"\b(shark|requin|poisson|fish|sous l'eau)\b", subject, re.I) else ""
        if scene == "underwater ocean" and "blue" not in colors:
            colors.append("blue")
        # French commonly places the adjective after the noun; keep the
        # canonical English subject as "pink shark", which helps both CLIP
        # and the quality evaluator preserve the requested object.
        for color in _unique(self._COLORS.values()):
            for noun in _unique(self._NOUNS.values()):
                subject = re.sub(rf"\b{re.escape(noun)}\s+{re.escape(color)}\b",
                                 f"{color} {noun}", subject, flags=re.IGNORECASE)
        if re.search(r"\b(espace|space|galaxie|cosmos)\b", low):
            scene = "deep space with distant stars"
        elif re.search(r"\b(for[êe]t|jungle|forest)\b", low):
            scene = "lush forest environment"
        elif re.search(r"\b(studio|fond blanc|white background)\b", low):
            scene = "clean studio background"
        if is_product:
            scene = "clean studio setup with seamless background"

        composition = "full body visible" if re.search(r"\b(shark|requin|animal|personnage|character)\b", subject, re.I) else ""
        if is_portrait:
            composition = "head and shoulders portrait, eyes clearly visible"
        if is_product:
            composition = "product centered, complete silhouette visible"
        if is_banner:
            composition = "wide balanced composition with safe space for layout"
        if is_poster:
            composition = "strong focal point with clean negative space for typography"

        lighting = "soft cinematic underwater lighting" if scene == "underwater ocean" else ""
        if is_product:
            lighting = "soft commercial studio lighting with controlled reflections"
        elif is_portrait:
            lighting = "soft cinematic key light with gentle rim light"
        elif cinematic and not lighting:
            lighting = "cinematic directional lighting with natural contrast"

        camera = "85mm portrait lens, shallow depth of field" if is_portrait else ""
        mood = "vibrant and playful" if colors else ("cinematic and atmospheric" if cinematic else "clean and engaging")
        ratio = "16:9" if is_banner or re.search(r"\b(paysage|landscape|horizontal|wide)\b", low) else "9:16" if re.search(r"\b(vertical|portrait|story)\b", low) else "1:1"
        exact_text = ""
        text_match = re.search(r"(?:texte|text|écrit|inscription)\s*[:\"]?(.+?)(?:[\"]|$)", original, re.I)
        if text_match:
            exact_text = text_match.group(1).strip(" .\"")
        quality = "high" if not re.search(r"\b(rapide|draft|preview|brouillon)\b", low) else "fast"
        if mode == "upscale":
            edit = True
        return ImageIntent(
            original_prompt=original, subject=subject, style=style, scene=scene,
            lighting=lighting, composition=composition, colors=colors,
            camera=camera, mood=mood, quality=quality, aspect_ratio=ratio,
            reference_images=reference_images or [], editing_mode=("edit" if edit else mode),
            profile=profile, exact_text=exact_text,
        )


@dataclass(frozen=True)
class StyleProfile:
    id: str
    prompt_additions: tuple[str, ...]
    negative_prompt: tuple[str, ...]
    model_preference: tuple[str, ...] = ()
    sampler: str = "dpmpp_2m"
    scheduler: str = "karras"
    steps: int = 28
    cfg: float = 6.0
    resolution: tuple[int, int] = (1024, 1024)
    upscale: bool = False
    refiner: bool = False


STYLE_PROFILES: dict[str, StyleProfile] = {
    "cinematic": StyleProfile("cinematic", ("natural detail", "coherent forms", "clean professional composition"), ("plastic texture", "muddy lighting")),
    "photorealistic": StyleProfile("photorealistic", ("photorealistic", "natural material detail", "accurate proportions"), ("cartoon", " CGI", "plastic skin")),
    "3d_animation": StyleProfile("3d_animation", ("clean 3D character design", "expressive silhouette", "polished render"), ("photorealistic", "muddy details")),
    "illustration": StyleProfile("illustration", ("clean illustrated shapes", "intentional linework", "controlled color palette"), ("photorealistic", "messy lines")),
    "concept_art": StyleProfile("concept_art", ("production concept art", "clear silhouette", "rich environmental storytelling"), ("flat composition", "unfinished sketch")),
    "product": StyleProfile("product", ("commercial product photography", "accurate shape", "centered packshot", "clean background"), ("crooked product", "misshapen packaging")),
    "poster": StyleProfile("poster", ("editorial poster background", "strong hierarchy", "clean negative space"), ("unreadable text", "random letters", "busy typography")),
    "logo": StyleProfile("logo", ("simple vector-like mark", "flat clean geometry", "isolated background"), ("photorealistic", "complex background")),
    "transparent_asset": StyleProfile("transparent_asset", ("isolated asset", "complete silhouette", "clean edges"), ("background", "floor", "shadow cut-off")),
    "game_asset": StyleProfile("game_asset", ("game-ready asset", "readable silhouette", "clean materials"), ("cropped", "incomplete object")),
    "portrait": StyleProfile("portrait", ("detailed face", "clear eyes", "natural skin texture", "balanced portrait lighting"), ("asymmetrical eyes", "cross-eyed", "deformed face"), steps=30, cfg=5.5, resolution=(832, 1216)),
    "discord_banner": StyleProfile("discord_banner", ("wide banner composition", "subject weighted to one side", "clean space for UI"), ("cropped subject", "tiny subject"), resolution=(1344, 768)),
}


class PromptComposer:
    def compose(self, intent: ImageIntent, *, correction: str = "") -> str:
        profile = STYLE_PROFILES.get(intent.profile, STYLE_PROFILES["cinematic"])
        parts = [intent.subject]
        if intent.scene: parts.append(f"in {intent.scene}")
        if intent.composition: parts.append(intent.composition)
        if intent.style: parts.append(intent.style)
        if intent.colors: parts.append("color palette: " + ", ".join(intent.colors))
        if intent.lighting: parts.append(intent.lighting)
        if intent.camera: parts.append(intent.camera)
        if intent.mood: parts.append(f"{intent.mood} mood")
        parts.extend(profile.prompt_additions)
        if correction: parts.append(correction)
        # Keep the prompt natural and bounded; no universal 8k/masterpiece spam.
        return ", ".join(_unique(parts))[:1800]


class NegativePromptBuilder:
    BASE = ("blurry", "low quality", "distorted", "deformed", "duplicate", "cropped",
            "text", "watermark", "logo", "oversaturated", "messy background")

    def build(self, intent: ImageIntent, extra: str = "") -> str:
        profile = STYLE_PROFILES.get(intent.profile, STYLE_PROFILES["cinematic"])
        values = list(self.BASE) + list(profile.negative_prompt)
        if re.search(r"\b(human|man|woman|portrait|face|person|homme|femme)\b", intent.subject, re.I):
            values += ["bad anatomy", "asymmetrical eyes", "bad hands", "extra fingers"]
        if re.search(r"\b(animal|shark|requin|cat|chat|dog|chien|bird|oiseau)\b", intent.subject, re.I):
            values += ["bad anatomy", "extra fins", "extra limbs", "unnatural body proportions"]
        return ", ".join(_unique([*values, extra]))[:1400]


@dataclass
class ModelMetadata:
    id: str
    name: str
    type: str
    architecture: str
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    recommended_resolution: tuple[int, int] = (1024, 1024)
    supports_negative_prompt: bool = True
    supports_img2img: bool = False
    supports_inpainting: bool = False
    supports_controlnet: bool = False
    vram_estimate: int = 0
    checkpoint: str = ""
    diffusion_model: str = ""
    text_encoder: str = ""
    vae: str = ""
    engine_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ImageModelRegistry:
    def __init__(self, detection: dict[str, Any] | None = None) -> None:
        self.detection = detection or {}
        self.models: list[ModelMetadata] = []
        self.refresh(self.detection)

    def refresh(self, detection: dict[str, Any] | None = None) -> list[ModelMetadata]:
        if detection is not None: self.detection = detection
        models: list[ModelMetadata] = []
        for engine in self.detection.get("engines") or []:
            eid = str(engine.get("id") or "")
            if eid == "zimage":
                name = str(engine.get("model") or engine.get("label") or "Z-Image-Turbo")
                models.append(ModelMetadata(
                    id=name, name=name, type="diffusion_model", architecture="Z-Image-Turbo / Lumina2",
                    strengths=["fast high-quality stylized realism", "good natural language adherence"],
                    weaknesses=["Turbo model is less suited to long iterative refinement"],
                    recommended_resolution=(1024, 1024), supports_img2img=False, vram_estimate=7000,
                    diffusion_model=name, text_encoder=str(engine.get("text_encoder") or ""),
                    vae=str(engine.get("vae") or ""), engine_id=eid))
            elif eid in {"sd", "sdxl"}:
                checkpoint = str(engine.get("checkpoint") or "")
                models.append(ModelMetadata(
                    id=checkpoint, name=Path(checkpoint).stem, type="checkpoint",
                    architecture="SDXL" if eid == "sdxl" else "SD checkpoint",
                    strengths=["high quality text-to-image"] if eid == "sdxl" else ["general text-to-image"],
                    weaknesses=["high VRAM use"] if eid == "sdxl" else ["quality depends on checkpoint"],
                    recommended_resolution=(1024, 1024), supports_img2img=True, supports_inpainting=eid == "sd",
                    vram_estimate=5000, checkpoint=checkpoint, engine_id=eid))
            elif eid == "flux":
                model = str(engine.get("model") or "")
                models.append(ModelMetadata(
                    id=model, name=Path(model).stem, type="diffusion_model", architecture="Flux",
                    strengths=["prompt adherence", "graphic compositions"], weaknesses=["high VRAM use"],
                    recommended_resolution=(1024, 1024), supports_img2img=True, vram_estimate=11000,
                    diffusion_model=model, text_encoder=str(engine.get("text_encoder") or ""),
                    vae=str(engine.get("vae") or ""), engine_id=eid))
        self.models = models
        return models

    def available(self) -> list[ModelMetadata]: return list(self.models)
    def get(self, model_id: str) -> ModelMetadata | None:
        return next((m for m in self.models if m.id == model_id), None)


class ModelSelector:
    def select(self, intent: ImageIntent, registry: ImageModelRegistry, *, free_vram_mb: int | None = None,
               quality: str = "BALANCED") -> ModelMetadata:
        candidates = registry.available()
        if not candidates:
            raise ValueError("Aucun modèle image installé et détecté dans ComfyUI.")
        scored: list[tuple[int, ModelMetadata]] = []
        for model in candidates:
            score = 0
            if intent.profile == "portrait" and "portrait" in model.name.casefold(): score += 30
            if intent.profile in {"poster", "product", "cinematic"} and model.architecture.startswith("Z-Image"): score += 12
            if intent.editing_mode in {"edit", "improve"} and model.supports_img2img: score += 25
            if quality.upper() == "FAST" and model.vram_estimate <= 8000: score += 10
            if free_vram_mb is not None and model.vram_estimate > free_vram_mb: score -= 100
            scored.append((score, model))
        scored.sort(key=lambda item: (-item[0], item[1].id.casefold()))
        return scored[0][1]


@dataclass(frozen=True)
class WorkflowSpec:
    id: str
    modes: tuple[str, ...]
    template: str
    supports_preview: bool = True
    requires_source: bool = False


class WorkflowRegistry:
    SPECS = {
        "text2image_sdxl": WorkflowSpec("text2image_sdxl", ("generate", "variation"), "sdxl_text2image.json"),
        "text2image_fast": WorkflowSpec("text2image_fast", ("generate", "variation"), "fast_text2image.json"),
        "image_edit": WorkflowSpec("image_edit", ("edit", "improve"), "image_edit.json", requires_source=True),
        "inpainting": WorkflowSpec("inpainting", ("inpaint",), "image_edit.json", requires_source=True),
        "upscale": WorkflowSpec("upscale", ("upscale",), "upscale.json", requires_source=True),
        "transparent_asset": WorkflowSpec("transparent_asset", ("generate",), "transparent_asset.json"),
    }

    def get(self, workflow_id: str) -> WorkflowSpec | None: return self.SPECS.get(workflow_id)
    def all(self) -> list[WorkflowSpec]: return list(self.SPECS.values())


class WorkflowSelector:
    def select(self, intent: ImageIntent, model: ModelMetadata, *, mode: str = "generate") -> WorkflowSpec:
        if mode == "upscale": return WorkflowRegistry.SPECS["upscale"]
        if intent.profile == "transparent_asset": return WorkflowRegistry.SPECS["transparent_asset"]
        if mode in {"edit", "improve"}: return WorkflowRegistry.SPECS["image_edit"]
        if mode == "variation": return WorkflowRegistry.SPECS["text2image_sdxl"]
        return WorkflowRegistry.SPECS["text2image_fast" if model.engine_id == "zimage" else "text2image_sdxl"]


class WorkflowValidationError(ValueError): pass


class ComfyWorkflowCompiler:
    """Compile placeholder templates and validate API-format workflows."""
    REQUIRED = {"class_type", "inputs"}
    MODEL_KEYS = {"ckpt_name", "unet_name", "clip_name", "vae_name", "upscale_model"}

    def compile(self, template: dict[str, Any], values: dict[str, Any], *, registry: ImageModelRegistry | None = None) -> dict[str, Any]:
        def replace(value: Any) -> Any:
            if isinstance(value, str):
                exact = re.fullmatch(r"\{\{([A-Z_]+)\}\}", value)
                if exact and exact.group(1) in values:
                    return values[exact.group(1)]
                for key, replacement in values.items():
                    value = value.replace("{{" + key + "}}", str(replacement))
                return value
            if isinstance(value, dict): return {k: replace(v) for k, v in value.items()}
            if isinstance(value, list): return [replace(v) for v in value]
            return value
        workflow = replace(template)
        self.validate(workflow, registry=registry)
        return workflow

    def validate(self, workflow: dict[str, Any], *, registry: ImageModelRegistry | None = None) -> None:
        if not isinstance(workflow, dict) or not workflow:
            raise WorkflowValidationError("Workflow ComfyUI vide ou invalide.")
        for node_id, node in workflow.items():
            if not isinstance(node, dict) or not self.REQUIRED.issubset(node):
                raise WorkflowValidationError(f"Node {node_id} invalide : class_type et inputs sont requis.")
            if not isinstance(node["inputs"], dict):
                raise WorkflowValidationError(f"Node {node_id} : inputs doit être un objet.")
            for key, value in node["inputs"].items():
                if key in self.MODEL_KEYS and isinstance(value, str) and value:
                    available = registry.detection.get("files") if registry else None
                    if available is not None and not any(value in names for names in available.values()):
                        raise WorkflowValidationError(f"Modèle absent de ComfyUI : {value}")
                if isinstance(value, list) and len(value) == 2 and str(value[0]) not in workflow:
                    raise WorkflowValidationError(f"Lien invalide : {node_id}.{key} → {value[0]}")
        dimensions = [n["inputs"] for n in workflow.values() if isinstance(n, dict)]
        for inputs in dimensions:
            for key in ("width", "height"):
                if key in inputs and (int(inputs[key]) < 256 or int(inputs[key]) > 2048):
                    raise WorkflowValidationError(f"Dimension {key} hors limites : {inputs[key]}")


@dataclass
class GenerationProfile:
    quality_mode: str = "BALANCED"
    width: int = 1024
    height: int = 1024
    steps: int = 28
    cfg: float = 6.0
    sampler: str = "dpmpp_2m"
    scheduler: str = "karras"
    seed: int = 0
    auto_upscale: bool = False
    refine: bool = False
    max_retries: int = 2

    def as_dict(self) -> dict[str, Any]: return asdict(self)


class GenerationProfileFactory:
    def create(self, intent: ImageIntent, model: ModelMetadata, *, quality: str = "BALANCED",
               width: int = 0, height: int = 0, steps: int = 0, seed: int = 0,
               settings: dict[str, Any] | None = None) -> GenerationProfile:
        quality = str(quality or "BALANCED").upper()
        settings = settings or {}
        base_w, base_h = STYLE_PROFILES.get(intent.profile, STYLE_PROFILES["cinematic"]).resolution
        if intent.aspect_ratio == "16:9": base_w, base_h = (1344, 768)
        elif intent.aspect_ratio == "9:16": base_w, base_h = (768, 1344)
        if model.recommended_resolution != (1024, 1024) and intent.aspect_ratio == "1:1":
            base_w, base_h = model.recommended_resolution
        if model.engine_id == "zimage":
            # Z-Image-Turbo is designed for short Euler sampling; do not apply
            # SDXL's DPM++/CFG assumptions to it.
            default_steps, default_cfg, sampler, scheduler = 8, 1.0, "euler", "simple"
        else:
            default_steps, default_cfg, sampler, scheduler = 28, 6.0, "dpmpp_2m", "karras"
        if quality == "FAST": default_steps = min(default_steps, 8); retries = 0
        elif quality == "HIGH": default_steps = max(default_steps, 32); retries = 3
        else: retries = 2
        return GenerationProfile(
            quality_mode=quality, width=int(width or base_w), height=int(height or base_h),
            steps=int(steps or default_steps), cfg=float(settings.get("cfg") or default_cfg),
            sampler=str(settings.get("sampler") or sampler), scheduler=str(settings.get("scheduler") or scheduler),
            seed=int(seed or random.SystemRandom().randint(1, 2**31 - 1)),
            auto_upscale=bool(settings.get("auto_upscale", quality == "HIGH")),
            refine=bool(settings.get("refine", quality == "HIGH")),
            max_retries=int(settings.get("max_retries", retries)),
        )


class GPUResourceManager:
    """Small image-specific adapter around nvidia-smi, with no guessed VRAM."""
    @staticmethod
    def snapshot() -> dict[str, Any]:
        try:
            proc = subprocess.run(["nvidia-smi", "--query-gpu=memory.total,memory.used,memory.free",
                                   "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                  timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            values = [int(x.strip()) for x in proc.stdout.strip().splitlines()[0].split(",")[:3]]
            return {"vram": {"total_mb": values[0], "used_mb": values[1], "free_mb": values[2]}, "measured": True}
        except Exception:
            return {"vram": None, "measured": False}


@dataclass
class ImageQualityEvaluation:
    subject_match: float | None = None
    anatomy: float | None = None
    sharpness: float | None = None
    composition: float | None = None
    style_match: float | None = None
    defects: list[str] = field(default_factory=list)
    overall: float | None = None
    provider: str = ""

    def as_dict(self) -> dict[str, Any]: return asdict(self)


class ImageQualityChecker:
    """Contract for an optional Vision evaluator; never invents a score."""
    def __init__(self, vision: Any = None) -> None: self.vision = vision
    def evaluate(self, image_path: str, intent: ImageIntent) -> ImageQualityEvaluation | None:
        if self.vision is None: return None
        try:
            result = self.vision(image_path=image_path, intent=intent.as_dict())
            return ImageQualityEvaluation(**result, provider="vision") if isinstance(result, dict) else None
        except Exception:
            return None
