"""Build ComfyUI API graphs for the V2 image pipeline.

The graphs are assembled here rather than loaded from a frozen template file
because the V2 pipeline is *variable-shape*: FAST is a single pass, QUALITY
adds an ESRGAN + re-diffusion stage, ULTRA adds a delivery upscale.  A fixed
JSON per combination would multiply into a dozen near-identical files that
drift apart.

The conditioning core is nonetheless held fixed and asserted: it reproduces the
official Z-Image-Turbo graph (``CLIPLoader(type=lumina2)`` →
``ConditioningZeroOut`` → ``KSampler(cfg=1.0, res_multistep/simple)`` with
``ModelSamplingAuraFlow``).  :func:`assert_official_core` fails loudly if a
future edit breaks it.
"""
from __future__ import annotations

import copy
import random
from typing import Any

from .image_quality import IMAGE_EDIT, RenderPlan

MODEL = "z_image_turbo_bf16.safetensors"
TEXT_ENCODER = "qwen_3_4b.safetensors"
VAE = "ae.safetensors"
UPSCALE_MODEL = "4x-UltraSharp.pth"
UPSCALE_MODEL_NATIVE_SCALE = 4

# Node ids are kept stable and match the official golden workflow where they
# overlap, so a graph dumped from JARVIS stays readable in the ComfyUI editor.
N_UNET, N_CLIP, N_VAE = "28", "30", "29"
N_POS, N_NEG_ZERO, N_NEG_TEXT = "27", "33", "34"
N_SHIFT, N_LATENT, N_SAMPLER, N_DECODE = "11", "13", "3", "8"
N_LOAD_SRC, N_SRC_ENCODE = "20", "21"
N_UPMODEL, N_UP_APPLY, N_UP_FIT = "40", "41", "42"
N_RE_ENCODE, N_RE_SAMPLER, N_RE_DECODE = "43", "44", "45"
N_POST_MODEL, N_POST_APPLY, N_POST_FIT, N_SHARPEN = "50", "51", "52", "53"
N_SAVE = "9"


class GraphBuildError(ValueError):
    pass


def assert_official_core(graph: dict[str, Any]) -> None:
    """Guard the conditioning topology that makes Z-Image-Turbo behave."""
    expected = {
        N_UNET: "UNETLoader", N_CLIP: "CLIPLoader", N_VAE: "VAELoader",
        N_POS: "CLIPTextEncode", N_SHIFT: "ModelSamplingAuraFlow",
        N_SAMPLER: "KSampler", N_DECODE: "VAEDecode",
    }
    for node_id, class_type in expected.items():
        node = graph.get(node_id)
        if not isinstance(node, dict) or node.get("class_type") != class_type:
            raise GraphBuildError(f"Noyau officiel altéré : node {node_id} devrait être {class_type}.")
    if graph[N_CLIP]["inputs"].get("type") != "lumina2":
        raise GraphBuildError("CLIPLoader doit rester en type=lumina2 pour Z-Image.")
    sampler = graph[N_SAMPLER]["inputs"]
    if float(sampler.get("cfg", 0)) != 1.0:
        raise GraphBuildError("Z-Image-Turbo est distillé pour CFG 1.0.")
    if sampler.get("sampler_name") != "res_multistep" or sampler.get("scheduler") != "simple":
        raise GraphBuildError("Sampler officiel Z-Image attendu : res_multistep / simple.")
    if graph[N_SAMPLER]["inputs"].get("negative") != [N_NEG_ZERO, 0]:
        raise GraphBuildError("Le conditioning négatif officiel (ConditioningZeroOut) est absent.")


class ZImageGraphBuilder:
    """Assemble the API graph for a :class:`RenderPlan`."""

    def __init__(self, *, model: str = MODEL, text_encoder: str = TEXT_ENCODER,
                 vae: str = VAE, upscale_model: str = UPSCALE_MODEL) -> None:
        self.model = model
        self.text_encoder = text_encoder
        self.vae = vae
        self.upscale_model = upscale_model

    # -- core ---------------------------------------------------------------
    def _core(self, plan: RenderPlan) -> dict[str, Any]:
        seed = int(plan.seed or random.SystemRandom().randint(1, 2**31 - 1))
        graph: dict[str, Any] = {
            N_UNET: {"class_type": "UNETLoader",
                     "inputs": {"unet_name": self.model, "weight_dtype": "default"}},
            N_CLIP: {"class_type": "CLIPLoader",
                     "inputs": {"clip_name": self.text_encoder, "type": "lumina2",
                                "device": "default"}},
            N_VAE: {"class_type": "VAELoader", "inputs": {"vae_name": self.vae}},
            N_POS: {"class_type": "CLIPTextEncode",
                    "inputs": {"text": plan.prompt, "clip": [N_CLIP, 0]}},
            N_NEG_ZERO: {"class_type": "ConditioningZeroOut",
                         "inputs": {"conditioning": [N_POS, 0]}},
            N_SHIFT: {"class_type": "ModelSamplingAuraFlow",
                      "inputs": {"model": [N_UNET, 0], "shift": float(plan.shift)}},
            N_SAMPLER: {"class_type": "KSampler", "inputs": {
                "seed": seed, "steps": int(plan.steps), "cfg": 1.0,
                "sampler_name": "res_multistep", "scheduler": "simple",
                "denoise": 1.0, "model": [N_SHIFT, 0],
                "positive": [N_POS, 0], "negative": [N_NEG_ZERO, 0],
                "latent_image": [N_LATENT, 0]}},
            N_DECODE: {"class_type": "VAEDecode",
                       "inputs": {"samples": [N_SAMPLER, 0], "vae": [N_VAE, 0]}},
        }
        return graph

    def _latent(self, plan: RenderPlan, graph: dict[str, Any], source_image: str) -> None:
        if plan.image_type == IMAGE_EDIT:
            if not source_image:
                raise GraphBuildError("Le pipeline IMAGE_EDIT exige une image source.")
            graph[N_LOAD_SRC] = {"class_type": "LoadImage", "inputs": {"image": source_image}}
            graph[N_SRC_ENCODE] = {"class_type": "VAEEncode",
                                   "inputs": {"pixels": [N_LOAD_SRC, 0], "vae": [N_VAE, 0]}}
            graph[N_SAMPLER]["inputs"]["latent_image"] = [N_SRC_ENCODE, 0]
            # Editing must not redraw the picture: the denoise is the whole
            # difference between "change the background" and "new image".
            graph[N_SAMPLER]["inputs"]["denoise"] = float(plan.hires_denoise or 0.55)
        else:
            graph[N_LATENT] = {"class_type": "EmptySD3LatentImage",
                               "inputs": {"width": int(plan.width),
                                          "height": int(plan.height), "batch_size": 1}}

    # -- optional stages ----------------------------------------------------
    def _upscale_pair(self, graph: dict[str, Any], *, loader_id: str, apply_id: str,
                      fit_id: str, source: list[Any], width: int, height: int) -> list[Any]:
        """ESRGAN upscale then lanczos fit to the exact target size.

        ESRGAN only produces a fixed 4x; fitting back down is what turns it
        into arbitrary-ratio detail rather than a size change.
        """
        graph[loader_id] = {"class_type": "UpscaleModelLoader",
                            "inputs": {"model_name": self.upscale_model}}
        graph[apply_id] = {"class_type": "ImageUpscaleWithModel",
                           "inputs": {"upscale_model": [loader_id, 0], "image": source}}
        graph[fit_id] = {"class_type": "ImageScale",
                         "inputs": {"upscale_method": "lanczos", "width": int(width),
                                    "height": int(height), "crop": "disabled",
                                    "image": [apply_id, 0]}}
        return [fit_id, 0]

    def _hires(self, plan: RenderPlan, graph: dict[str, Any], tail: list[Any]) -> list[Any]:
        tail = self._upscale_pair(graph, loader_id=N_UPMODEL, apply_id=N_UP_APPLY,
                                  fit_id=N_UP_FIT, source=tail,
                                  width=plan.hires_width, height=plan.hires_height)
        graph[N_RE_ENCODE] = {"class_type": "VAEEncode",
                              "inputs": {"pixels": tail, "vae": [N_VAE, 0]}}
        graph[N_RE_SAMPLER] = {"class_type": "KSampler", "inputs": {
            "seed": int(graph[N_SAMPLER]["inputs"]["seed"]) + 1,
            "steps": int(plan.hires_steps), "cfg": 1.0,
            "sampler_name": "res_multistep", "scheduler": "simple",
            "denoise": float(plan.hires_denoise), "model": [N_SHIFT, 0],
            "positive": [N_POS, 0], "negative": [N_NEG_ZERO, 0],
            "latent_image": [N_RE_ENCODE, 0]}}
        graph[N_RE_DECODE] = {"class_type": "VAEDecode",
                              "inputs": {"samples": [N_RE_SAMPLER, 0], "vae": [N_VAE, 0]}}
        return [N_RE_DECODE, 0]

    # -- public -------------------------------------------------------------
    def build(self, plan: RenderPlan, *, source_image: str = "",
              filename_prefix: str = "jarvis_v2") -> dict[str, Any]:
        graph = self._core(plan)
        self._latent(plan, graph, source_image)
        tail: list[Any] = [N_DECODE, 0]

        if plan.hires_width and plan.image_type != IMAGE_EDIT:
            tail = self._hires(plan, graph, tail)

        if plan.post_upscale:
            width = int((plan.hires_width or plan.width) * plan.post_upscale)
            height = int((plan.hires_height or plan.height) * plan.post_upscale)
            tail = self._upscale_pair(graph, loader_id=N_POST_MODEL, apply_id=N_POST_APPLY,
                                      fit_id=N_POST_FIT, source=tail,
                                      width=width, height=height)

        if plan.sharpen:
            # Deliberately gentle: sigma 1.0 with a small alpha adds acutance
            # without the bright halos that read as "over-processed".
            graph[N_SHARPEN] = {"class_type": "ImageSharpen", "inputs": {
                "image": tail, "sharpen_radius": 1, "sigma": 1.0,
                "alpha": float(plan.sharpen)}}
            tail = [N_SHARPEN, 0]

        graph[N_SAVE] = {"class_type": "SaveImage",
                         "inputs": {"filename_prefix": filename_prefix, "images": tail}}
        assert_official_core(graph)
        self.validate(graph)
        return graph

    @staticmethod
    def validate(graph: dict[str, Any]) -> None:
        """Reject a graph before ComfyUI has to."""
        if not graph:
            raise GraphBuildError("Graphe vide.")
        for node_id, node in graph.items():
            if not isinstance(node, dict) or "class_type" not in node or "inputs" not in node:
                raise GraphBuildError(f"Node {node_id} invalide.")
            for key, value in node["inputs"].items():
                if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                    if value[0] not in graph:
                        raise GraphBuildError(f"Lien mort : {node_id}.{key} → {value[0]}")
                if key in {"width", "height"} and isinstance(value, int):
                    if not 256 <= value <= 4096:
                        raise GraphBuildError(f"Dimension hors limites : {key}={value}")
        if not any(n.get("class_type") == "SaveImage" for n in graph.values()):
            raise GraphBuildError("Aucun SaveImage dans le graphe.")

    def metadata(self, plan: RenderPlan) -> dict[str, Any]:
        return {
            "engine": "zimage_v2", "build_id": "JARVIS_IMAGE_GENERATION_REBUILD_V1",
            "diffusion_model": self.model, "text_encoder": self.text_encoder,
            "vae": self.vae,
            "upscale_model": self.upscale_model if (plan.hires_width or plan.post_upscale) else "",
            "sampler": "res_multistep", "scheduler": "simple", "cfg": 1.0,
            "shift": plan.shift, "mode": plan.mode, "image_type": plan.image_type,
            "passes": 2 if plan.hires_width else 1,
        }


def graph_for(plan: RenderPlan, **kwargs: Any) -> dict[str, Any]:
    return ZImageGraphBuilder().build(copy.deepcopy(plan), **kwargs)
