"""Engine selection and SDXL workflow adapter.

The fast engine remains the checked-in Z-Image-Turbo graph.  Quality uses a
separate, configurable ComfyUI API workflow and never falls back implicitly.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ROOT

FAST_ENGINE = "fast"
QUALITY_ENGINE = "quality"
AUTO_ENGINE = "auto"

QUALITY_PRESETS: dict[str, dict[str, Any]] = {
    "quality_standard": {"width": 1024, "height": 1024, "steps": 32, "cfg": 7.0},
    "quality_high": {"width": 1024, "height": 1536, "steps": 40, "cfg": 7.0},
    "quality_ultra": {"width": 1536, "height": 1536, "steps": 45, "cfg": 7.0},
}


@dataclass(frozen=True)
class ImageEngineDecision:
    mode: str
    reason: str
    label: str


def choose_image_engine(user_request: str, context: dict[str, Any] | None = None,
                        requested_mode: str = AUTO_ENGINE) -> ImageEngineDecision:
    """Choose fast/quality deterministically, with explicit mode precedence."""
    requested = str(requested_mode or AUTO_ENGINE).strip().casefold()
    if requested not in {AUTO_ENGINE, FAST_ENGINE, QUALITY_ENGINE}:
        requested = AUTO_ENGINE
    if requested == FAST_ENGINE:
        return ImageEngineDecision(FAST_ENGINE, "mode explicite fast", "Prévisualisation rapide")
    if requested == QUALITY_ENGINE:
        return ImageEngineDecision(QUALITY_ENGINE, "mode explicite quality", "Rendu final qualité SDXL")

    text = str(user_request or "").casefold()
    ctx = context or {}
    if ctx.get("preview") or ctx.get("draft") or ctx.get("iteration"):
        return ImageEngineDecision(FAST_ENGINE, "contexte preview/brouillon/itération", "Prévisualisation rapide")
    fast_terms = ("aperçu", "apercu", "brouillon", "test rapide", "preview", "version rapide", "mode rapide", "en fast")
    if any(term in text for term in fast_terms):
        return ImageEngineDecision(FAST_ENGINE, "mot-clé de rapidité détecté", "Prévisualisation rapide")

    quality_terms = (
        "visuel final", "image de qualité", "qualité", "quality", "ultra réaliste",
        "ultra realiste", "beau rendu", "final", "affiche", "poster", "avatar",
        "produit", "packshot", "portrait", "scène détaillée", "scene detaillee",
    )
    if any(term in text for term in quality_terms):
        return ImageEngineDecision(QUALITY_ENGINE, "intention de rendu final/qualité détectée",
                                   "Rendu final qualité SDXL")
    # A generic image request is a final render unless the user explicitly
    # asked for a preview. This matches the product priority: quality first.
    return ImageEngineDecision(QUALITY_ENGINE, "demande d'image finale par défaut",
                               "Rendu final qualité SDXL")


class SDXLWorkflowError(ValueError):
    pass


class SDXLWorkflowAdapter:
    """Compile a ComfyUI API workflow while preserving custom graph topology."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        try:
            workflow = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SDXLWorkflowError(f"Workflow SDXL illisible : {self.path} ({exc})") from exc
        if not isinstance(workflow, dict) or not workflow:
            raise SDXLWorkflowError("Workflow SDXL vide ou invalide.")
        return workflow

    @staticmethod
    def _nodes(workflow: dict[str, Any], class_type: str) -> list[tuple[str, dict[str, Any]]]:
        return [(str(k), v) for k, v in workflow.items()
                if isinstance(v, dict) and v.get("class_type") == class_type]

    @staticmethod
    def _replace_link(workflow: dict[str, Any], old: list[Any], new: list[Any]) -> None:
        for node in workflow.values():
            inputs = node.get("inputs", {}) if isinstance(node, dict) else {}
            for key, value in list(inputs.items()):
                if value == old:
                    inputs[key] = new

    def compile(self, *, prompt: str, negative_prompt: str, width: int, height: int,
                seed: int, steps: int, cfg: float, sampler: str, scheduler: str,
                checkpoint: str, vae: str = "", input_image: str = "") -> dict[str, Any]:
        workflow = copy.deepcopy(self.load())
        if not checkpoint:
            raise SDXLWorkflowError("Aucun checkpoint SDXL configuré ou détecté.")
        checkpoint_nodes = self._nodes(workflow, "CheckpointLoaderSimple")
        positive_nodes = self._nodes(workflow, "CLIPTextEncode")
        latent_nodes = self._nodes(workflow, "EmptyLatentImage")
        encode_nodes = self._nodes(workflow, "VAEEncode")
        sampler_nodes = self._nodes(workflow, "KSampler")
        decode_nodes = self._nodes(workflow, "VAEDecode")
        if not (checkpoint_nodes and len(positive_nodes) >= 2 and (latent_nodes or encode_nodes)
                and sampler_nodes and decode_nodes):
            raise SDXLWorkflowError("Le workflow SDXL doit contenir Checkpoint, deux CLIPTextEncode, latent/VAEEncode, KSampler et VAEDecode.")

        checkpoint_nodes[0][1]["inputs"]["ckpt_name"] = checkpoint
        positive_nodes[0][1]["inputs"]["text"] = str(prompt or "")
        positive_nodes[1][1]["inputs"]["text"] = str(negative_prompt or "")
        if latent_nodes:
            latent_nodes[0][1]["inputs"].update({"width": int(width), "height": int(height), "batch_size": 1})
        sampler_inputs = sampler_nodes[0][1]["inputs"]
        sampler_inputs.update({"seed": int(seed), "steps": int(steps), "cfg": float(cfg),
                               "sampler_name": str(sampler), "scheduler": str(scheduler)})

        vae_source: list[Any] = [checkpoint_nodes[0][0], 2]
        vae_nodes = self._nodes(workflow, "VAELoader")
        if vae:
            if vae_nodes:
                vae_nodes[0][1]["inputs"]["vae_name"] = vae
                vae_source = [vae_nodes[0][0], 0]
            else:
                raise SDXLWorkflowError("Un VAE est configuré mais le workflow SDXL n'a pas de VAELoader.")
        elif vae_nodes:
            # The example graph uses a sentinel so an empty setting falls back
            # to the checkpoint VAE without sending an invalid loader request.
            sentinel = str(vae_nodes[0][1].get("inputs", {}).get("vae_name") or "")
            if sentinel.startswith("__JARVIS_"):
                node_id = vae_nodes[0][0]
                workflow.pop(node_id, None)
                self._replace_link(workflow, [node_id, 0], vae_source)
            else:
                vae_source = [vae_nodes[0][0], 0]
        for node in workflow.values():
            if not isinstance(node, dict) or node.get("class_type") not in {"VAEDecode", "VAEEncode"}:
                continue
            if "vae" in node.get("inputs", {}):
                node["inputs"]["vae"] = vae_source

        if input_image:
            load_nodes = self._nodes(workflow, "LoadImage")
            if not load_nodes:
                raise SDXLWorkflowError("Le workflow SDXL img2img doit contenir LoadImage.")
            load_nodes[0][1]["inputs"]["image"] = input_image
        return workflow

    def metadata(self) -> dict[str, str]:
        return {"workflow": "sdxl_quality", "workflow_path": str(self.path),
                "architecture": "SDXL", "conditioning": "positive + negative CLIP"}

    @staticmethod
    def resolve_path(value: str, default_name: str) -> Path:
        candidate = Path(str(value or default_name)).expanduser()
        return candidate if candidate.is_absolute() else ROOT / candidate
