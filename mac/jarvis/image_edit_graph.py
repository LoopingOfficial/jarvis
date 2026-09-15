"""Editing graphs: img2img, inpainting and outpainting.

Kept separate from :mod:`jarvis.zimage_graph` because editing answers a
different question.  Text-to-image asks "what should exist"; editing asks "what
must stay untouched".  The denoise strength and the mask are the whole contract,
so they are explicit parameters rather than a quality-mode side effect.

No inpainting-specific checkpoint is installed, so these graphs drive the same
Z-Image-Turbo weights through masked latents.  That works well for background
and colour changes and for filling small regions; it is weaker at inventing a
large new object from nothing, which :class:`EditPlan` records honestly in
``confidence``.
"""
from __future__ import annotations

import random
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .image_segmentation import (
    N_SEG_MASK as N_SEG_MASK_SUBJECT,
    MaskPlan, SegmentationUnavailable, build_mask_nodes, plan_mask,
    supports_request,
)
from .zimage_graph import (
    UPSCALE_MODEL_NATIVE_SCALE,
    N_CLIP, N_DECODE, N_NEG_ZERO, N_POS, N_SAMPLER, N_SAVE, N_SHIFT, N_UNET,
    N_VAE, GraphBuildError, ZImageGraphBuilder,
)

# Edit operations, each with the denoise that preserves the most while still
# doing the job.  Measured rather than guessed: above ~0.6 the subject drifts.
REMOVE = "remove"
REPLACE_BACKGROUND = "replace_background"
RECOLOR = "recolor"
ADD_OBJECT = "add_object"
RESTYLE = "restyle"
INPAINT = "inpaint"
OUTPAINT = "outpaint"
UPSCALE_ONLY = "upscale"

EDIT_OPERATIONS = (REMOVE, REPLACE_BACKGROUND, RECOLOR, ADD_OBJECT, RESTYLE,
                   INPAINT, OUTPAINT, UPSCALE_ONLY)


@dataclass(frozen=True)
class EditRecipe:
    id: str
    label: str
    denoise: float
    steps: int
    needs_mask: bool
    confidence: str
    guidance: str = ""


EDIT_RECIPES: dict[str, EditRecipe] = {
    REMOVE: EditRecipe(
        REMOVE, "Retirer un élément", denoise=0.85, steps=14, needs_mask=True,
        confidence="good",
        guidance="Fill the masked area with a natural continuation of the surrounding scene."),
    REPLACE_BACKGROUND: EditRecipe(
        REPLACE_BACKGROUND, "Changer le fond", denoise=0.90, steps=16, needs_mask=True,
        confidence="good",
        # No mention of a subject: the masked region is the background, and
        # naming a person there is what made the model draw a second one.
        guidance="An empty scene with no people and no characters in it."),
    RECOLOR: EditRecipe(
        RECOLOR, "Changer une couleur", denoise=0.55, steps=12, needs_mask=True,
        confidence="good",
        guidance="Change only the colour of the masked region; keep its shape, texture and shading."),
    ADD_OBJECT: EditRecipe(
        ADD_OBJECT, "Ajouter un objet", denoise=0.95, steps=18, needs_mask=True,
        confidence="moderate",
        guidance="Add the requested object inside the masked area, matching the scene's perspective and light."),
    RESTYLE: EditRecipe(
        RESTYLE, "Refaire dans un style", denoise=0.55, steps=16, needs_mask=False,
        confidence="good",
        guidance="Keep the composition and subject; restate them in the requested style."),
    INPAINT: EditRecipe(
        INPAINT, "Inpainting", denoise=0.90, steps=16, needs_mask=True, confidence="good"),
    OUTPAINT: EditRecipe(
        OUTPAINT, "Outpainting", denoise=1.0, steps=18, needs_mask=False,
        confidence="moderate",
        guidance="Extend the scene outwards, continuing the existing composition naturally."),
    UPSCALE_ONLY: EditRecipe(
        UPSCALE_ONLY, "Upscale", denoise=0.0, steps=0, needs_mask=False, confidence="good"),
}


_SUBJECT_CLAUSE = re.compile(
    r"(?:garde|conserve|pr[ée]serve|keep)[^.;]*?"
    r"(?:sujet|personnage|produit|objet|machine|subject|character)[^.;]*[.;]?",
    re.IGNORECASE)
_ONLY_BACKGROUND = re.compile(
    r"(?:remplace|change|modifie|replace|change)\s+(?:uniquement\s+|seulement\s+|only\s+)?"
    r"(?:l'|le |la |les |the )?(?:arri[èe]re[- ]?plan|fond|background)\s*"
    r"(?:par|avec|by|with|pour)?\s*",
    re.IGNORECASE)


def _strip_subject_clauses(prompt: str) -> str:
    """Reduce a background request to a description of the background alone."""
    text = _SUBJECT_CLAUSE.sub(" ", str(prompt or ""))
    text = _ONLY_BACKGROUND.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" .,;:")
    return text


@dataclass
class EditPlan:
    operation: str
    source_image: str
    prompt: str = ""
    mask_image: str = ""
    denoise: float = 0.0
    steps: int = 0
    seed: int = 0
    shift: float = 3.0
    grow_mask: int = 12
    pad: tuple[int, int, int, int] = (0, 0, 0, 0)
    upscale_to: tuple[int, int] = (0, 0)
    source_size: tuple[int, int] = (0, 0)   # lets the upscale bound itself
    confidence: str = "good"
    auto_mask: MaskPlan | None = None   # set when segmentation supplies the mask
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def plan_edit(operation: str, *, source_image: str, prompt: str = "", mask_image: str = "",
              seed: int = 0, denoise: float = 0.0, pad: tuple[int, int, int, int] = (0, 0, 0, 0),
              upscale_to: tuple[int, int] = (0, 0),
              source_size: tuple[int, int] = (0, 0)) -> EditPlan:
    op = str(operation or "").strip().casefold()
    if op not in EDIT_RECIPES:
        raise GraphBuildError(f"Opération d'édition inconnue : {operation}")
    recipe = EDIT_RECIPES[op]
    if not source_image:
        raise GraphBuildError("Une image source est requise pour l'édition.")
    notes: list[str] = []
    auto_mask: MaskPlan | None = None
    if recipe.needs_mask and not mask_image:
        # Segment automatically rather than making the user paint a mask.  This
        # only works for subject/background separation; plan_mask refuses (and
        # says why) when the request names some other region.
        try:
            auto_mask = plan_mask(op, prompt)
            notes.append(f"auto_mask:{auto_mask.target}")
        except SegmentationUnavailable as exc:
            raise GraphBuildError(str(exc)) from exc
    if op == OUTPAINT and not any(pad):
        raise GraphBuildError("L'outpainting exige une marge d'extension non nulle.")
    if auto_mask is not None and auto_mask.target == "background":
        # Measured: a background replacement whose prompt still said "keep the
        # subject" drew a *second person* into the background, because the
        # masked region is where the model is free and the word "subject" was
        # in its conditioning.  The background prompt must describe only the
        # background, so subject-preservation phrasing is stripped out.
        prompt = _strip_subject_clauses(prompt)
    if recipe.guidance and prompt:
        prompt = f"{prompt.rstrip('.')}. {recipe.guidance}"
    elif recipe.guidance:
        prompt = recipe.guidance
    if recipe.confidence == "moderate":
        notes.append("no_inpainting_checkpoint_installed")
    return EditPlan(
        operation=op, source_image=source_image, prompt=prompt, mask_image=mask_image,
        denoise=float(denoise or recipe.denoise), steps=recipe.steps,
        seed=int(seed or random.SystemRandom().randint(1, 2**31 - 1)),
        pad=pad, upscale_to=upscale_to, source_size=source_size,
        confidence=recipe.confidence,
        auto_mask=auto_mask, notes=notes)


class EditGraphBuilder(ZImageGraphBuilder):
    """Build the ComfyUI graph for an :class:`EditPlan`."""

    N_SRC, N_MASK, N_GROW = "20", "22", "23"
    N_ENCODE, N_NOISE_MASK, N_PAD = "21", "24", "25"
    N_COMPOSITE = "26"

    def _conditioning(self, plan: EditPlan) -> dict[str, Any]:
        return {
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
        }

    def build_edit(self, plan: EditPlan, *, filename_prefix: str = "jarvis_edit_v2") -> dict[str, Any]:
        if plan.operation == UPSCALE_ONLY:
            return self._build_upscale_only(plan, filename_prefix)

        graph = self._conditioning(plan)
        graph[self.N_SRC] = {"class_type": "LoadImage", "inputs": {"image": plan.source_image}}
        pixels: list[Any] = [self.N_SRC, 0]

        if plan.operation == OUTPAINT:
            left, top, right, bottom = plan.pad
            graph[self.N_PAD] = {"class_type": "ImagePadForOutpaint", "inputs": {
                "image": pixels, "left": int(left), "top": int(top),
                "right": int(right), "bottom": int(bottom), "feathering": 40}}
            graph[self.N_ENCODE] = {"class_type": "VAEEncodeForInpaint", "inputs": {
                "pixels": [self.N_PAD, 0], "vae": [N_VAE, 0],
                "mask": [self.N_PAD, 1], "grow_mask_by": int(plan.grow_mask)}}
            latent: list[Any] = [self.N_ENCODE, 0]
        elif plan.auto_mask is not None:
            seg_nodes, mask_link = build_mask_nodes(plan.auto_mask, plan.source_image)
            # The segmentation sub-graph loads the source itself; reuse that
            # node rather than loading the same file twice.
            seg_nodes.pop(self.N_SRC, None)
            graph.update(seg_nodes)
            graph[self.N_ENCODE] = {"class_type": "VAEEncode",
                                    "inputs": {"pixels": pixels, "vae": [N_VAE, 0]}}
            graph[self.N_NOISE_MASK] = {"class_type": "SetLatentNoiseMask", "inputs": {
                "samples": [self.N_ENCODE, 0], "mask": mask_link}}
            latent = [self.N_NOISE_MASK, 0]
        elif plan.mask_image:
            graph[self.N_MASK] = {"class_type": "LoadImage", "inputs": {"image": plan.mask_image}}
            # The mask arrives as an image; its alpha channel is the selection.
            graph[self.N_GROW] = {"class_type": "ImageToMask",
                                  "inputs": {"image": [self.N_MASK, 0], "channel": "red"}}
            graph[self.N_ENCODE] = {"class_type": "VAEEncode",
                                    "inputs": {"pixels": pixels, "vae": [N_VAE, 0]}}
            graph[self.N_NOISE_MASK] = {"class_type": "SetLatentNoiseMask", "inputs": {
                "samples": [self.N_ENCODE, 0], "mask": [self.N_GROW, 0]}}
            latent = [self.N_NOISE_MASK, 0]
        else:
            graph[self.N_ENCODE] = {"class_type": "VAEEncode",
                                    "inputs": {"pixels": pixels, "vae": [N_VAE, 0]}}
            latent = [self.N_ENCODE, 0]

        graph[N_SAMPLER] = {"class_type": "KSampler", "inputs": {
            "seed": int(plan.seed), "steps": int(plan.steps), "cfg": 1.0,
            "sampler_name": "res_multistep", "scheduler": "simple",
            "denoise": float(plan.denoise), "model": [N_SHIFT, 0],
            "positive": [N_POS, 0], "negative": [N_NEG_ZERO, 0],
            "latent_image": latent}}
        graph[N_DECODE] = {"class_type": "VAEDecode",
                           "inputs": {"samples": [N_SAMPLER, 0], "vae": [N_VAE, 0]}}
        tail: list[Any] = [N_DECODE, 0]

        if plan.auto_mask is not None and plan.auto_mask.target == "background":
            # A masked latent is not a guarantee: the decode still drifts inside
            # the protected region.  Compositing the original pixels back over
            # the result makes the subject bit-identical, which is the contract
            # for a local edit -- it must not be a convincing re-generation.
            graph[self.N_COMPOSITE] = {"class_type": "ImageCompositeMasked", "inputs": {
                "destination": tail, "source": [self.N_SRC, 0],
                "x": 0, "y": 0, "resize_source": False,
                "mask": [N_SEG_MASK_SUBJECT, 0]}}
            tail = [self.N_COMPOSITE, 0]

        graph[N_SAVE] = {"class_type": "SaveImage",
                         "inputs": {"filename_prefix": filename_prefix,
                                    "images": tail}}
        self.validate(graph)
        return graph

    # ESRGAN always upscales x4.  Left uncapped, a 1280x1792 source came back
    # as 5120x7168 -- 36 megapixels and a 40 MB PNG that nobody asked for.
    # The delivery upscale is therefore bounded on the long edge unless the
    # caller names an explicit target size.
    MAX_DELIVERY_EDGE = 4096

    def _build_upscale_only(self, plan: EditPlan, filename_prefix: str) -> dict[str, Any]:
        """Pure ESRGAN delivery upscale; no diffusion, so nothing is invented."""
        graph: dict[str, Any] = {
            self.N_SRC: {"class_type": "LoadImage", "inputs": {"image": plan.source_image}},
            "40": {"class_type": "UpscaleModelLoader",
                   "inputs": {"model_name": self.upscale_model}},
            "41": {"class_type": "ImageUpscaleWithModel",
                   "inputs": {"upscale_model": ["40", 0], "image": [self.N_SRC, 0]}},
        }
        tail: list[Any] = ["41", 0]
        width, height = plan.upscale_to
        if not (width and height) and plan.source_size:
            src_w, src_h = plan.source_size
            long_edge = max(src_w, src_h) * UPSCALE_MODEL_NATIVE_SCALE
            if long_edge > self.MAX_DELIVERY_EDGE:
                ratio = self.MAX_DELIVERY_EDGE / long_edge
                width = int(src_w * UPSCALE_MODEL_NATIVE_SCALE * ratio)
                height = int(src_h * UPSCALE_MODEL_NATIVE_SCALE * ratio)
        if width and height:
            graph["42"] = {"class_type": "ImageScale", "inputs": {
                "upscale_method": "lanczos", "width": int(width), "height": int(height),
                "crop": "disabled", "image": tail}}
            tail = ["42", 0]
        graph[N_SAVE] = {"class_type": "SaveImage",
                         "inputs": {"filename_prefix": filename_prefix, "images": tail}}
        self.validate(graph)
        return graph


_OPERATION_PATTERNS: tuple[tuple[str, str], ...] = (
    (OUTPAINT, r"\b(outpaint|[ée]tend[sr]?|agrandi[sr]? le cadre|elargi[sr]?|zoom arri[èe]re)\b"),
    (REMOVE, r"\b(retire|enl[èe]ve|supprime|efface|remove|delete|sans le|sans la)\b"),
    (REPLACE_BACKGROUND, r"\b(fond|arri[èe]re[- ]plan|background|d[ée]cor)\b"),
    (RECOLOR, r"\b(couleur|colorie|recolor|en (?:rouge|bleu|vert|jaune|noir|blanc|rose)|repeins)\b"),
    (ADD_OBJECT, r"\b(ajoute|rajoute|add|place un|mets un|insere|ins[èe]re)\b"),
    (UPSCALE_ONLY, r"\b(upscale|agrandi[sr]? la r[ée]solution|plus grande r[ée]solution|4k)\b"),
    (RESTYLE, r"\b(style|refais|recr[ée]e|transforme|version|dans le style)\b"),
)


def detect_edit_operation(request: str) -> str:
    """Map a phrasing to an edit operation; defaults to a conservative restyle."""
    text = str(request or "").casefold()
    for operation, pattern in _OPERATION_PATTERNS:
        import re
        if re.search(pattern, text):
            return operation
    return RESTYLE
