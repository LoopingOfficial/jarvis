"""Immutable official Z-Image-Turbo workflow adapter.

The adapter is intentionally boring.  It loads the checked-in API workflow,
asserts its fixed conditioning graph, and changes only the four inputs that
the golden workflow exposes for JARVIS.
"""
from __future__ import annotations

import copy
import json
import random
from pathlib import Path
from typing import Any

from .config import ROOT

GOLDEN_WORKFLOW = ROOT / "workflows" / "comfyui" / "z_image_turbo_golden.json"
GOLDEN_WORKFLOW_ID = "z_image_turbo_golden"
MODEL = "z_image_turbo_bf16.safetensors"
TEXT_ENCODER = "qwen_3_4b.safetensors"
VAE = "ae.safetensors"


class GoldenWorkflowError(ValueError):
    pass


class ZImageTurboAdapter:
    """Compile the official graph without reconstructing or auto-selecting nodes."""

    def __init__(self, path: Path | str = GOLDEN_WORKFLOW) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        try:
            workflow = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise GoldenWorkflowError(f"Workflow golden introuvable ou invalide : {exc}") from exc
        self.validate_fixed_graph(workflow)
        return workflow

    @staticmethod
    def validate_fixed_graph(workflow: dict[str, Any]) -> None:
        required = {"28", "30", "29", "27", "13", "11", "33", "3", "8", "9"}
        if set(workflow) != required:
            raise GoldenWorkflowError("Le workflow golden a été modifié : ensemble de nodes inattendu.")
        classes = {node_id: workflow[node_id].get("class_type") for node_id in required}
        expected = {"28": "UNETLoader", "30": "CLIPLoader", "29": "VAELoader",
                    "27": "CLIPTextEncode", "13": "EmptySD3LatentImage",
                    "11": "ModelSamplingAuraFlow", "33": "ConditioningZeroOut",
                    "3": "KSampler", "8": "VAEDecode", "9": "SaveImage"}
        if classes != expected:
            raise GoldenWorkflowError("Le workflow golden a été modifié : conditioning officiel absent.")
        if workflow["28"]["inputs"] != {"unet_name": MODEL, "weight_dtype": "default"}:
            raise GoldenWorkflowError("UNETLoader golden modifié.")
        if workflow["30"]["inputs"] != {"clip_name": TEXT_ENCODER, "type": "lumina2", "device": "default"}:
            raise GoldenWorkflowError("CLIPLoader golden modifié.")
        if workflow["29"]["inputs"] != {"vae_name": VAE}:
            raise GoldenWorkflowError("VAELoader golden modifié.")
        if workflow["11"]["inputs"] != {"model": ["28", 0], "shift": 3.0}:
            raise GoldenWorkflowError("ModelSamplingAuraFlow golden modifié.")
        if workflow["33"]["inputs"] != {"conditioning": ["27", 0]}:
            raise GoldenWorkflowError("ConditioningZeroOut golden modifié.")
        sampler = workflow["3"]["inputs"]
        if any(sampler.get(key) != value for key, value in {
            "cfg": 1.0, "sampler_name": "res_multistep", "scheduler": "simple", "denoise": 1.0,
            "model": ["11", 0], "positive": ["27", 0], "negative": ["33", 0], "latent_image": ["13", 0]
        }.items()):
            raise GoldenWorkflowError("KSampler golden modifié.")

    def compile(self, prompt: str, *, width: int = 1024, height: int = 1024,
                seed: int = 0, steps: int | None = None) -> dict[str, Any]:
        workflow = copy.deepcopy(self.load())
        workflow["27"]["inputs"]["text"] = str(prompt or "")
        workflow["13"]["inputs"]["width"] = int(width)
        workflow["13"]["inputs"]["height"] = int(height)
        workflow["3"]["inputs"]["seed"] = int(seed or random.SystemRandom().randint(1, 2**31 - 1))
        if steps is not None:
            workflow["3"]["inputs"]["steps"] = int(steps)
        self.validate_fixed_graph({**workflow, "27": {**workflow["27"], "inputs": {**workflow["27"]["inputs"], "text": ""}}})
        return workflow

    @staticmethod
    def metadata() -> dict[str, str]:
        return {"workflow": GOLDEN_WORKFLOW_ID, "diffusion_model": MODEL,
                "text_encoder": TEXT_ENCODER, "vae": VAE,
                "conditioning_loader": "CLIPLoader(type=lumina2)",
                "sampler": "res_multistep", "scheduler": "simple", "cfg": "1.0", "shift": "3.0"}

