"""Automatic subject segmentation and mask shaping for Image Edit V3.

Removes the requirement that the user hand-paint a mask before every edit.
BiRefNet (ComfyUI's native `LoadBackgroundRemovalModel` / `RemoveBackground`,
no third-party nodes) produces a foreground mask; everything else here shapes
that mask into the selection a given edit actually needs.

The two directions matter and are easy to confuse:

  * "change the background"  → edit the **inverted** subject mask
  * "keep only the subject"  → edit, or cut out, the **subject** mask

So :class:`MaskPlan` states its target explicitly rather than leaving the
polarity implicit in whichever node happens to be wired up.

What this module does **not** do: find an arbitrary named object ("the car on
the left", "the sky").  That needs text-prompted segmentation
(GroundingDINO + SAM) and third-party nodes, which are not installed.
:func:`supports_request` says so honestly instead of returning a subject mask
that silently edits the wrong region.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

BG_REMOVAL_MODEL = "birefnet.safetensors"

# Node ids for the segmentation sub-graph; kept clear of the render graph's.
N_SEG_SRC = "60"
N_SEG_MODEL = "61"
N_SEG_MASK = "62"
N_SEG_INVERT = "63"
N_SEG_GROW = "64"
N_SEG_FEATHER = "65"
N_SEG_THRESHOLD = "66"
N_SEG_PREVIEW_IMG = "67"
N_SEG_PREVIEW_SAVE = "68"

SUBJECT = "subject"
BACKGROUND = "background"
TARGETS = (SUBJECT, BACKGROUND)


class SegmentationUnavailable(RuntimeError):
    """Raised when the requested selection cannot be produced honestly."""


@dataclass
class MaskPlan:
    """How to turn a BiRefNet foreground mask into the selection for one edit."""

    target: str = BACKGROUND
    grow: int = 0            # px; positive dilates, negative erodes
    feather: int = 0         # px; borders only, see build_rect_feather_nodes
    threshold: float = 0.0   # 0 keeps BiRefNet's soft alpha
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# Which side of the subject each operation edits, and how the edge should be
# shaped.  Background work wants the mask to bite slightly *into* the subject
# edge so no halo of old background survives; subject work wants the opposite.
_OPERATION_MASKS: dict[str, MaskPlan] = {
    "replace_background": MaskPlan(target=BACKGROUND, grow=3),
    "remove_background": MaskPlan(target=BACKGROUND, grow=3),
    "cutout": MaskPlan(target=SUBJECT, grow=-1, threshold=0.5),
    "recolor_subject": MaskPlan(target=SUBJECT, grow=-2),
    "restyle_background": MaskPlan(target=BACKGROUND, grow=2),
}

# Phrasings that are genuinely about the subject/background split, which is the
# only split BiRefNet can make.
_BACKGROUND_PATTERNS = (
    r"\b(fond|arri[èe]re[- ]?plan|background|d[ée]cor)\b",
    r"\bd[ée]tour(?:e|er|age)\b",
    r"\b(garde|conserve|pr[ée]serve)\s+(?:exactement\s+)?(?:le|la|les)\s+"
    r"(sujet|personnage|produit|objet|machine)\b",
)

# Phrasings that name some *other* region, which needs text-prompted
# segmentation we do not have.
_NEEDS_TEXT_SEGMENTATION = (
    r"\b(?:objet|[ée]l[ée]ment|truc|chose)\s+(?:[àa]\s+)?(?:gauche|droite|"
    r"en haut|en bas|au fond|devant|derri[èe]re)\b",
    r"\b(?:le|la|les)\s+(ciel|sol|mur|herbe|arbre|voiture|table|fen[êe]tre|"
    r"lampe|chaise|porte)\b",
    r"\b(?:uniquement|seulement)\s+(?:le|la|les)\s+(?!fond|arri)",
)


def wants_background_split(request: str) -> bool:
    text = str(request or "").casefold()
    return any(re.search(p, text) for p in _BACKGROUND_PATTERNS)


def needs_text_segmentation(request: str) -> bool:
    text = str(request or "").casefold()
    if wants_background_split(text):
        return False
    return any(re.search(p, text) for p in _NEEDS_TEXT_SEGMENTATION)


def supports_request(request: str) -> tuple[bool, str]:
    """Can automatic segmentation serve this request?  ``(ok, reason)``."""
    if needs_text_segmentation(request):
        return False, (
            "La segmentation automatique installée (BiRefNet) sépare le sujet "
            "de l'arrière-plan, mais ne sait pas isoler un objet nommé. "
            "Sélectionne la zone à modifier, ou installe GroundingDINO + SAM.")
    return True, "séparation sujet / arrière-plan"


def plan_mask(operation: str, request: str = "", *,
              target: str = "", grow: int | None = None,
              feather: int | None = None) -> MaskPlan:
    """Build the mask plan for an edit operation, refusing what we cannot do."""
    ok, reason = supports_request(request)
    if not ok:
        raise SegmentationUnavailable(reason)
    base = _OPERATION_MASKS.get(str(operation or "").casefold())
    if base is None:
        base = MaskPlan(target=BACKGROUND if wants_background_split(request) else SUBJECT)
    plan = MaskPlan(target=base.target, grow=base.grow, feather=base.feather,
                    threshold=base.threshold, notes=[f"auto:{reason}"])
    if target in TARGETS:
        plan.target = target
        plan.notes.append("target_forced_by_user")
    if grow is not None:
        plan.grow = int(grow)
    if feather is not None:
        plan.feather = max(0, int(feather))
    return plan


def build_mask_nodes(plan: MaskPlan, source_image: str, *,
                     model: str = BG_REMOVAL_MODEL) -> tuple[dict[str, Any], list[Any]]:
    """Return ``(nodes, mask_output)`` producing the shaped selection.

    ``mask_output`` is a ComfyUI link ready to feed ``SetLatentNoiseMask`` or
    ``VAEEncodeForInpaint``.
    """
    nodes: dict[str, Any] = {
        N_SEG_SRC: {"class_type": "LoadImage", "inputs": {"image": source_image}},
        N_SEG_MODEL: {"class_type": "LoadBackgroundRemovalModel",
                      "inputs": {"bg_removal_name": model}},
        N_SEG_MASK: {"class_type": "RemoveBackground",
                     "inputs": {"bg_removal_model": [N_SEG_MODEL, 0],
                                "image": [N_SEG_SRC, 0]}},
    }
    # BiRefNet returns the *foreground* (subject) mask.
    tail: list[Any] = [N_SEG_MASK, 0]

    if plan.threshold > 0:
        nodes[N_SEG_THRESHOLD] = {"class_type": "ThresholdMask",
                                  "inputs": {"mask": tail, "value": float(plan.threshold)}}
        tail = [N_SEG_THRESHOLD, 0]

    if plan.target == BACKGROUND:
        nodes[N_SEG_INVERT] = {"class_type": "InvertMask", "inputs": {"mask": tail}}
        tail = [N_SEG_INVERT, 0]

    if plan.grow:
        # GrowMask takes a signed amount: negative erodes.
        nodes[N_SEG_GROW] = {"class_type": "GrowMask",
                             "inputs": {"mask": tail, "expand": int(plan.grow),
                                        "tapered_corners": True}}
        tail = [N_SEG_GROW, 0]

    # Deliberately no FeatherMask here.  Measured: ComfyUI's FeatherMask fades
    # the mask along the *rectangular image borders*, not along the silhouette.
    # On a background mask it dropped the outer ~10 px from 254 to 22-50, which
    # would protect a frame of old background from the edit and leave a visible
    # border after a background replacement.  BiRefNet already returns a soft
    # alpha, and GrowMask's tapered corners smooth the rest, so the silhouette
    # edge needs nothing further.  `feather` is honoured only by
    # :func:`build_rect_feather_nodes`, where fading at the borders is the point.
    return nodes, tail


def build_rect_feather_nodes(nodes: dict[str, Any], tail: list[Any],
                             feather: int) -> list[Any]:
    """Feather a *rectangular* mask at the image borders (outpaint seams)."""
    if not feather:
        return tail
    nodes[N_SEG_FEATHER] = {"class_type": "FeatherMask", "inputs": {
        "mask": tail, "left": int(feather), "top": int(feather),
        "right": int(feather), "bottom": int(feather)}}
    return [N_SEG_FEATHER, 0]


def build_preview_graph(plan: MaskPlan, source_image: str, *,
                        model: str = BG_REMOVAL_MODEL,
                        filename_prefix: str = "jarvis_mask") -> dict[str, Any]:
    """A graph that only renders the mask, so it can be shown before editing.

    Seeing the selection before committing is the whole point: an edit applied
    to the wrong region wastes a full render and looks like a quality problem.
    """
    nodes, tail = build_mask_nodes(plan, source_image, model=model)
    nodes[N_SEG_PREVIEW_IMG] = {"class_type": "MaskToImage", "inputs": {"mask": tail}}
    nodes[N_SEG_PREVIEW_SAVE] = {"class_type": "SaveImage", "inputs": {
        "filename_prefix": filename_prefix, "images": [N_SEG_PREVIEW_IMG, 0]}}
    return nodes


def build_cutout_graph(source_image: str, *, plan: MaskPlan | None = None,
                       model: str = BG_REMOVAL_MODEL,
                       filename_prefix: str = "jarvis_cutout") -> dict[str, Any]:
    """Transparent PNG asset: subject kept, background made transparent.

    No diffusion is involved, so nothing about the subject can be invented or
    altered — it is the original pixels plus an alpha channel.
    """
    plan = plan or _OPERATION_MASKS["cutout"]
    nodes, tail = build_mask_nodes(plan, source_image, model=model)
    # JoinImageWithAlpha expects the mask to mark the *transparent* area, so
    # the subject mask is inverted here rather than in the plan, which would
    # also have flipped the grow/erode direction.
    nodes["70"] = {"class_type": "InvertMask", "inputs": {"mask": tail}}
    nodes["71"] = {"class_type": "JoinImageWithAlpha",
                   "inputs": {"image": [N_SEG_SRC, 0], "alpha": ["70", 0]}}
    nodes["72"] = {"class_type": "SaveImage",
                   "inputs": {"filename_prefix": filename_prefix, "images": ["71", 0]}}
    return nodes
