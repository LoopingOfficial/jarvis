"""Image quality modes, typed pipelines and prompt enrichment (V2).

Design notes that come from measured behaviour, not assumption:

* The only installed engine is Z-Image-Turbo (Lumina2 architecture) whose text
  encoder is Qwen3-4B.  It reads *natural language sentences*.  Feeding it the
  SD-style comma tag soup produced by the V1 ``PromptComposer`` measurably
  degrades output, so the builder here writes prose.
* Z-Image-Turbo is distilled for CFG 1.0.  At CFG 1.0 the negative prompt has
  no effect at all.  This module therefore never pretends a negative prompt is
  active: :attr:`RenderPlan.negative_active` says whether it is, and the
  unusable text is kept out of the graph instead of being silently ignored.
* Real quality above the 8-step baseline comes from a second low-denoise pass
  over an ESRGAN-upscaled image ("hi-res fix"), not from raising the step count
  of the distilled sampler.

This module makes the decisions; :mod:`jarvis.zimage_graph` turns them into a
ComfyUI API graph.
"""
from __future__ import annotations

import random
import re
from dataclasses import asdict, dataclass, field
from typing import Any

IMAGE_QUALITY_BUILD_ID = "JARVIS_IMAGE_GENERATION_REBUILD_V1"

FAST, BALANCED, QUALITY, ULTRA = "FAST", "BALANCED", "QUALITY", "ULTRA"
QUALITY_MODES = (FAST, BALANCED, QUALITY, ULTRA)

PORTRAIT = "PORTRAIT"
PRODUCT = "PRODUCT"
POSTER = "POSTER"
GAMING = "GAMING"
UI_CONCEPT = "UI_CONCEPT"
ILLUSTRATION = "ILLUSTRATION"
PHOTOREAL = "PHOTOREAL"
IMAGE_EDIT = "IMAGE_EDIT"
IMAGE_TYPES = (PORTRAIT, PRODUCT, POSTER, GAMING, UI_CONCEPT,
               ILLUSTRATION, PHOTOREAL, IMAGE_EDIT)


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


# ---------------------------------------------------------------------------
# Quality modes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class QualityMode:
    """One quality profile.

    ``hires_scale`` of 0 disables the second diffusion pass entirely.
    ``post_upscale`` runs ESRGAN on the finished image without re-diffusing it,
    which sharpens delivery resolution without inventing detail.
    """

    id: str
    label: str
    base_steps: int
    hires_scale: float = 0.0
    hires_denoise: float = 0.0
    hires_steps: int = 0
    post_upscale: float = 0.0
    sharpen: float = 0.0
    resolution_scale: float = 1.0
    shift: float = 3.0
    budget_s: int = 60


# Every value below comes from the Quality Validation V2 sweeps
# (tools/quality_validation.py, see docs/JARVIS_IMAGE_QUALITY_VALIDATION_V2.md).
# The four results that set these numbers:
#
#   Test A (steps 8/10/12/16/20) — quality plateaus at ~12 steps.  Beyond that
#     the distilled sampler does not add detail, it *over-densifies* texture:
#     at 16 and 20 steps the portrait's freckles merge into blotches, and the
#     machine is indistinguishable from 12.  20 steps costs +62% for a worse
#     face.  So no profile exceeds 12 steps, ULTRA included.
#   Test B (hi-res denoise 0.20→0.50) — the useful band is 0.20-0.30.  At 0.35
#     faces over-texture; at 0.50 the machine's internal geometry drifts
#     (different mechanism, different feet).  Hence 0.25 / 0.30, not 0.35-0.38.
#   Test C (base 768/896/1024/1152) — all four stay coherent, unlike the 1536
#     native render that degraded in V1.  1024 is the safe default; 1152 gives
#     slightly richer geometry for +25% time, which is what separates ULTRA.
#   Test D (none / lanczos / ESRGAN / +sharpen) — ESRGAN clearly beats lanczos
#     on edges.  Sharpening at 0.10 is invisible and at 0.25 produces dark
#     halos around neon and metal, so sharpening is removed entirely.
QUALITY_MODE_SPECS: dict[str, QualityMode] = {
    FAST: QualityMode(
        FAST, "Aperçu rapide", base_steps=8, budget_s=60),
    BALANCED: QualityMode(
        BALANCED, "Usage quotidien", base_steps=12, post_upscale=1.5, budget_s=120),
    QUALITY: QualityMode(
        QUALITY, "Rendu soigné", base_steps=12, hires_scale=1.5,
        hires_denoise=0.25, hires_steps=10, budget_s=240),
    ULTRA: QualityMode(
        # ULTRA differs from QUALITY only where a measurement justified it:
        # a larger first pass (Test C) and an ESRGAN delivery upscale (Test D).
        # It deliberately does NOT raise steps or denoise, because Test A and
        # Test B showed both make the image worse, not better.
        ULTRA, "Pièce maîtresse", base_steps=12, resolution_scale=1.125,
        hires_scale=1.5, hires_denoise=0.30, hires_steps=12,
        post_upscale=1.30, sharpen=0.0, budget_s=600),
}



# A flat per-mode budget was wrong: BALANCED at 1024² finishes in ~15 s, but a
# BALANCED poster was killed at its 120 s budget.  Investigation showed the
# render itself was not the cost — it was the first job after an idle period,
# where ComfyUI reloads the 12 GB checkpoint from disk.  A cold load alone can
# take longer than a warm render, and it is not work the budget should police.
#
# So the budget is a runaway-hang guard, not a performance target:
#   model-load allowance + (measured workload x safety factor).
# Calibrated on the Quality Validation sweeps (RTX 3080, card otherwise idle):
# ~1.2 s per megapixel-step of diffusion, ~7 s per ESRGAN stage per source
# megapixel.  The safety factor covers a card shared with Ollama or Blender,
# which tripled these times in an earlier session.
MODEL_LOAD_ALLOWANCE_S = 150
SECONDS_PER_MP_STEP = 1.2
SECONDS_PER_UPSCALE_MP = 7.0
BUDGET_SAFETY_FACTOR = 3.0
MIN_BUDGET_S = 180


def estimate_budget_s(width: int, height: int, steps: int, *,
                      hires_width: int = 0, hires_height: int = 0,
                      hires_steps: int = 0, post_upscale: float = 0.0) -> int:
    """Time budget for a plan, derived from its real work rather than its name."""
    work = (width * height / 1e6) * max(1, steps)
    upscale_mp = 0.0
    if hires_width:
        work += (hires_width * hires_height / 1e6) * max(1, hires_steps)
        upscale_mp += width * height / 1e6          # ESRGAN runs on the base pass
    if post_upscale:
        upscale_mp += (hires_width or width) * (hires_height or height) / 1e6
    seconds = work * SECONDS_PER_MP_STEP + upscale_mp * SECONDS_PER_UPSCALE_MP
    return max(MIN_BUDGET_S,
               MODEL_LOAD_ALLOWANCE_S + int(seconds * BUDGET_SAFETY_FACTOR))


# ---------------------------------------------------------------------------
# Typed pipelines
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TypePipeline:
    """Per-image-type rendering recipe.

    ``framing``/``lighting``/``rendering`` are prose fragments, deliberately
    short, that are appended as full sentences rather than tags.
    """

    id: str
    label: str
    width: int
    height: int
    framing: str = ""
    lighting: str = ""
    rendering: str = ""
    negative: tuple[str, ...] = ()
    shift_bonus: float = 0.0
    min_mode: str = FAST
    reserve_text_space: bool = False


TYPE_PIPELINES: dict[str, TypePipeline] = {
    PORTRAIT: TypePipeline(
        PORTRAIT, "Portrait", 832, 1216,
        framing="Framed as a head-and-shoulders portrait with the eyes on the upper third",
        lighting="Lit by a soft key light with a gentle rim light separating the subject",
        rendering="Photographed on an 85mm lens at f/2, natural skin texture with visible pores",
        negative=("deformed face", "asymmetrical eyes", "extra fingers", "waxy plastic skin"),
    ),
    PRODUCT: TypePipeline(
        PRODUCT, "Visuel produit", 1024, 1024,
        framing="The product is centred, complete and unclipped, seen slightly above eye level",
        lighting="Lit by a large soft studio box with controlled specular highlights and a clean falloff",
        rendering="Commercial packshot photography on a seamless background, accurate materials",
        negative=("crooked product", "misshapen packaging", "distracting background clutter"),
    ),
    POSTER: TypePipeline(
        POSTER, "Affiche", 832, 1216,
        framing="Composed as a poster with a single strong focal point and deliberate empty space "
                "in the upper third and along the lower edge for typography",
        lighting="Dramatic directional lighting with a clear value hierarchy",
        rendering="Polished editorial key art with a controlled palette and clean graphic shapes",
        negative=("garbled lettering", "random text", "busy typography", "cluttered layout"),
        reserve_text_space=True, min_mode=QUALITY,
    ),
    GAMING: TypePipeline(
        GAMING, "Illustration gaming", 1344, 768,
        framing="Dynamic wide composition with a readable silhouette and clear foreground separation",
        lighting="High contrast rim lighting with saturated coloured accents and atmospheric haze",
        rendering="Polished game key art, painterly detail, strong sense of energy",
        negative=("flat lighting", "muddy silhouette", "washed out colours"),
        shift_bonus=0.5,
    ),
    UI_CONCEPT: TypePipeline(
        UI_CONCEPT, "Concept UI", 1344, 768,
        framing="Presented straight on as a flat interface mockup with an aligned grid and generous margins",
        lighting="Even diffuse lighting with no photographic glare",
        rendering="Clean product design concept, crisp geometric panels, restrained palette, "
                  "placeholder blocks instead of readable body copy",
        negative=("garbled text", "fake lorem letters", "photographic depth of field", "skeuomorphic clutter"),
    ),
    ILLUSTRATION: TypePipeline(
        ILLUSTRATION, "Illustration", 1024, 1024,
        framing="Balanced illustrative composition with a clear focal point",
        lighting="Stylised lighting with intentional colour temperature contrast",
        rendering="Clean expressive illustration with confident shapes and a controlled palette",
        negative=("messy linework", "muddy colours"),
    ),
    PHOTOREAL: TypePipeline(
        PHOTOREAL, "Photoréaliste", 1216, 832,
        framing="Naturalistic composition with believable perspective and depth layering",
        lighting="Physically plausible lighting with soft shadow gradients and accurate colour bounce",
        rendering="Photorealistic capture, fine material detail, subtle lens imperfections",
        negative=("cgi look", "plastic surfaces", "over-sharpened halos"),
    ),
    IMAGE_EDIT: TypePipeline(
        IMAGE_EDIT, "Édition d'image", 1024, 1024,
        framing="Preserve the original framing, subject identity and perspective",
        rendering="Seamless edit that matches the existing grain, lighting and colour response",
        negative=("mismatched lighting", "visible seam", "duplicated subject"),
    ),
}


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
_TYPE_PATTERNS: tuple[tuple[str, str], ...] = (
    (IMAGE_EDIT, r"\b(retouche|modifie|remplace|enl[èe]ve|retire|supprime|change (?:le|la|les)|"
                 r"detoure|d[ée]toure|inpaint|outpaint|edit this|remove the|replace the)\b"),
    (POSTER, r"\b(affiche|poster|flyer|key ?art|couverture|placard|banni[èe]re promo)\b"),
    (UI_CONCEPT, r"\b(ui|interface|dashboard|tableau de bord|app design|maquette|wireframe|"
                 r"landing page|concept ui|ux)\b"),
    (GAMING, r"\b(gaming|jeu vid[ée]o|game ?art|esport|fps|rpg|splash art|skin|boss|"
             r"personnage de jeu|game key art)\b"),
    (PRODUCT, r"\b(produit|packshot|bouteille|canette|flacon|packaging|product shot|mockup produit)\b"),
    (PORTRAIT, r"\b(portrait|visage|t[êe]te|selfie|headshot|face of)\b"),
    (PHOTOREAL, r"\b(photo ?r[ée]aliste|photorealistic|r[ée]aliste|photo r[ée]elle|"
                r"comme une photo|photographie)\b"),
    (ILLUSTRATION, r"\b(illustration|dessin|cartoon|anim[ée]|stylis[ée]|artwork|peinture|concept art)\b"),
)

_FAST_PATTERNS = r"\b(aper[çc]u|brouillon|rapide|vite|preview|draft|test rapide|quick|esquisse)\b"
_ULTRA_PATTERNS = (r"\b(premium|ultra|chef[- ]d'?[œo]euvre|maximum|impeccable|"
                   r"la meilleure|tr[èe]s haute qualit[ée]|print|impression|4k|8k|"
                   r"pour impression|haute d[ée]finition maximale)\b")
_QUALITY_PATTERNS = (r"\b(qualit[ée]|soign[ée]|d[ée]taill[ée]|beau|belle|final|propre|"
                     r"pro(?:fessionnel)?|net|haute r[ée]solution)\b")


def detect_image_type(request: str, *, has_source_image: bool = False) -> str:
    """Pick the typed pipeline for a request; never guesses silently.

    A supplied source image wins over wording: editing an image the user handed
    us is unambiguous.
    """
    text = _norm(request).casefold()
    if has_source_image and re.search(_TYPE_PATTERNS[0][1], text):
        return IMAGE_EDIT
    for type_id, pattern in _TYPE_PATTERNS:
        if type_id == IMAGE_EDIT and not has_source_image:
            continue
        if re.search(pattern, text):
            return type_id
    return ILLUSTRATION if not has_source_image else IMAGE_EDIT


def detect_quality_mode(request: str, *, image_type: str = "", default: str = BALANCED) -> str:
    """Choose FAST/BALANCED/QUALITY/ULTRA from the wording.

    Explicit speed wording wins over everything, because a user asking for a
    draft should not be made to wait for a multi-pass render.
    """
    text = _norm(request).casefold()
    if re.search(_FAST_PATTERNS, text):
        return FAST
    mode = default
    if re.search(_QUALITY_PATTERNS, text):
        mode = QUALITY
    if re.search(_ULTRA_PATTERNS, text):
        mode = ULTRA
    floor = TYPE_PIPELINES.get(image_type, TYPE_PIPELINES[ILLUSTRATION]).min_mode
    if QUALITY_MODES.index(mode) < QUALITY_MODES.index(floor):
        mode = floor
    return mode


def resolve_mode(requested: str, request: str = "", *, image_type: str = "",
                 default: str = BALANCED) -> tuple[str, str]:
    """Return ``(mode, reason)``; an explicit mode is always honoured."""
    wanted = _norm(requested).upper()
    if wanted in QUALITY_MODES:
        return wanted, "mode forcé par l'utilisateur"
    auto = detect_quality_mode(request, image_type=image_type, default=default)
    floor = TYPE_PIPELINES.get(image_type, TYPE_PIPELINES[ILLUSTRATION]).min_mode
    if auto == floor and floor != FAST:
        return auto, f"plancher qualité du pipeline {image_type}"
    return auto, "déduit de la formulation de la demande"


# ---------------------------------------------------------------------------
# Prompt enrichment
# ---------------------------------------------------------------------------
# The Z-Image text encoder is Qwen3-4B, which is natively multilingual.  V1
# hand-translated a small French vocabulary and produced franglais subjects
# ("moi un shark yellow neon"), degrading adherence.  We therefore keep the
# user's own wording verbatim and never translate it here.

_COMMAND_PREFIX = re.compile(
    r"^\s*(?:s'il te pla[îi]t[,\s]*)?(?:peux[-\s]tu\s+)?(?:me\s+)?"
    r"(?:cr[ée]e|g[ée]n[èe]re|fais|dessine|montre|donne|produis|imagine|refais|"
    r"make|create|generate|draw|show|give|design)"
    r"(?:[-\s]*moi)?\s*"
    r"(?:une?\s+|des\s+|the\s+|an?\s+|le\s+|la\s+|les\s+)?",
    re.IGNORECASE)

# "une image de", "a picture of" - a wrapper with no visual content.  Removed
# only when something else follows, so "crée une image" keeps its noun.
_MEDIUM_WRAPPER = re.compile(
    r"^(?:image|photo|photographie|illustration|visuel|rendu|picture|render|dessin)\s+"
    r"(?:de\s+|d'|du\s+|des\s+|of\s+|avec\s+|montrant\s+|qui montre\s+)\s*(?=\S)",
    re.IGNORECASE)

# Wording that exists only to request quality; it carries no visual meaning and
# survives as mode selection instead of polluting the subject.
_QUALITY_NOISE = re.compile(
    r"\b(?:en\s+)?(?:tr[èe]s\s+)?(?:haute\s+)?(?:qualit[ée]|premium|ultra|"
    r"4k|8k|hd|hq|soign[ée]e?|d[ée]taill[ée]e?|magnifique|superbe|"
    r"high quality|masterpiece|best quality)\b",
    re.IGNORECASE)


@dataclass
class EnrichedPrompt:
    subject: str
    prompt: str
    negative: str
    negative_active: bool
    exact_text: str = ""
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class PromptEnricher:
    """Turn a raw user request into a natural-language generation prompt.

    The contract is strict: everything the user asked for survives verbatim in
    :attr:`EnrichedPrompt.subject`, which always opens the final prompt.  The
    pipeline only *appends* framing, lighting and rendering sentences.  Nothing
    the user wrote is ever replaced or reinterpreted.
    """

    BASE_NEGATIVE = ("blurry", "low quality", "distorted anatomy", "extra limbs",
                     "duplicated subject", "watermark", "signature", "jpeg artifacts")

    def extract_subject(self, request: str) -> tuple[str, str]:
        """Return ``(subject, exact_text)`` in the user's own words.

        Only the imperative wrapper ("crée-moi une image de ...") and pure
        quality wording are removed.  The subject itself is never translated or
        paraphrased: Qwen3-4B reads French directly, and rewriting it is how V1
        lost the user's meaning.
        """
        original = _norm(request)
        exact_text = ""
        quoted = re.search(
            r"""(?:texte|text|[ée]crit|inscription|titre|slogan)\s*[:=]?\s*["«“']([^"»”']+)["»”']""",
            original, re.IGNORECASE)
        if quoted:
            exact_text = quoted.group(1).strip()
        elif (bare := re.search(r"""["«“']([^"»”']{2,60})["»”']""", original)):
            exact_text = bare.group(1).strip()

        subject = _COMMAND_PREFIX.sub("", original, count=1)
        subject = _MEDIUM_WRAPPER.sub("", subject, count=1)
        subject = _QUALITY_NOISE.sub(" ", subject)
        subject = _norm(subject).strip(" .,;:!?-")
        return (subject or original), exact_text

    def build(self, request: str, *, image_type: str, mode: str,
              user_negative: str = "", negative_active: bool = False,
              instruction: str = "") -> EnrichedPrompt:
        pipeline = TYPE_PIPELINES.get(image_type, TYPE_PIPELINES[ILLUSTRATION])
        subject, exact_text = self.extract_subject(request)
        notes: list[str] = []

        sentences = [subject if subject.endswith((".", "!", "?")) else subject + "."]
        if instruction:
            sentences.append(_norm(instruction).rstrip(".") + ".")
        for fragment in (pipeline.framing, pipeline.lighting, pipeline.rendering):
            if fragment:
                sentences.append(fragment.rstrip(".") + ".")
        if pipeline.reserve_text_space:
            # Measured: a giveaway poster came back with "GIVAWAY" stamped twice
            # in the very zones that were reserved for typography.  The model
            # fills empty space with invented, misspelled lettering unless told
            # not to -- and it cannot letter reliably anyway, so JARVIS always
            # composites the wording afterwards (see compose_poster_text).
            sentences.append(
                "Do not draw any text, letters, words, numbers or logos anywhere "
                "in the image; leave those areas as clean empty background.")
            notes.append("text_composited_downstream")

        prompt = " ".join(sentences)
        negatives = list(self.BASE_NEGATIVE) + list(pipeline.negative)
        if user_negative:
            negatives.append(_norm(user_negative))
        if not negative_active:
            notes.append("negative_prompt_inactive_at_cfg_1")
        return EnrichedPrompt(
            subject=subject, prompt=prompt[:1800],
            negative=", ".join(dict.fromkeys(negatives))[:1000],
            negative_active=negative_active, exact_text=exact_text, notes=notes)


# ---------------------------------------------------------------------------
# Render plan
# ---------------------------------------------------------------------------
@dataclass
class RenderPlan:
    """Everything needed to build a graph, and to explain it to the user."""

    mode: str
    mode_reason: str
    image_type: str
    width: int
    height: int
    steps: int
    cfg: float
    shift: float
    sampler: str
    scheduler: str
    seed: int
    hires_width: int = 0
    hires_height: int = 0
    hires_denoise: float = 0.0
    hires_steps: int = 0
    post_upscale: float = 0.0
    sharpen: float = 0.0
    prompt: str = ""
    negative: str = ""
    negative_active: bool = False
    subject: str = ""
    exact_text: str = ""
    notes: list[str] = field(default_factory=list)
    vram_adapted: bool = False
    budget_s: int = 60
    stages: list[str] = field(default_factory=list)

    @property
    def final_width(self) -> int:
        base = self.hires_width or self.width
        return int(base * self.post_upscale) if self.post_upscale else base

    @property
    def final_height(self) -> int:
        base = self.hires_height or self.height
        return int(base * self.post_upscale) if self.post_upscale else base

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["final_width"] = self.final_width
        data["final_height"] = self.final_height
        return data


def _round64(value: float) -> int:
    return max(256, int(round(value / 64.0)) * 64)


# Measured on an RTX 3080 10 GB: a 1536x1536 hi-res pass peaks at ~9.7 GB and
# completes, because ComfyUI streams the 12 GB checkpoint from system RAM.  So
# the ceiling is keyed on *total* VRAM, not on free VRAM, which merely reflects
# whatever else is resident at that instant.  Free VRAM is only consulted when
# it is critically low; the real protection against over-reaching is the OOM
# fallback in :mod:`jarvis.image_runtime`, which re-renders one mode lower.
_TOTAL_VRAM_CEILING_PX = {
    8000: 1280 * 1280,
    12000: 1792 * 1792,
}
_DEFAULT_CEILING_PX = 2048 * 2048

# Below this, another process holds so much of the card that even streaming
# will thrash; the plan is scaled down before ComfyUI is asked.
CRITICAL_FREE_VRAM_MB = 1500


def _ceiling_px(total_vram_mb: int | None, free_vram_mb: int | None) -> int | None:
    """Maximum pixel count for the heaviest diffusion pass."""
    limit = None
    if total_vram_mb and total_vram_mb > 0:
        limit = _DEFAULT_CEILING_PX
        for threshold, pixels in sorted(_TOTAL_VRAM_CEILING_PX.items()):
            if total_vram_mb <= threshold:
                limit = pixels
                break
    if free_vram_mb is not None and 0 < free_vram_mb <= CRITICAL_FREE_VRAM_MB:
        limit = min(limit or _DEFAULT_CEILING_PX, 1024 * 1024)
    return limit


class RenderPlanner:
    """Compose a quality mode and a typed pipeline into a concrete plan."""

    def __init__(self, enricher: PromptEnricher | None = None) -> None:
        self.enricher = enricher or PromptEnricher()

    def plan(self, request: str, *, requested_mode: str = "", image_type: str = "",
             seed: int = 0, width: int = 0, height: int = 0,
             has_source_image: bool = False, free_vram_mb: int | None = None,
             total_vram_mb: int | None = None,
             user_negative: str = "", instruction: str = "",
             default_mode: str = BALANCED) -> RenderPlan:
        resolved_type = (_norm(image_type).upper()
                         if _norm(image_type).upper() in IMAGE_TYPES
                         else detect_image_type(request, has_source_image=has_source_image))
        mode, reason = resolve_mode(requested_mode, request,
                                    image_type=resolved_type, default=default_mode)
        spec = QUALITY_MODE_SPECS[mode]
        pipeline = TYPE_PIPELINES[resolved_type]

        base_w = _round64((width or pipeline.width) * spec.resolution_scale)
        base_h = _round64((height or pipeline.height) * spec.resolution_scale)

        hires_w = hires_h = 0
        if spec.hires_scale:
            hires_w = _round64(base_w * spec.hires_scale)
            hires_h = _round64(base_h * spec.hires_scale)

        vram_adapted = False
        limit = _ceiling_px(total_vram_mb, free_vram_mb)
        if limit:
            target = hires_w * hires_h if hires_w else base_w * base_h
            if target > limit:
                ratio = (limit / target) ** 0.5
                if hires_w:
                    hires_w, hires_h = _round64(hires_w * ratio), _round64(hires_h * ratio)
                    if hires_w * hires_h <= base_w * base_h:
                        hires_w = hires_h = 0  # a shrunk second pass buys nothing
                else:
                    base_w, base_h = _round64(base_w * ratio), _round64(base_h * ratio)
                vram_adapted = True

        enriched = self.enricher.build(
            request, image_type=resolved_type, mode=mode,
            user_negative=user_negative, negative_active=False, instruction=instruction)

        # Only the stages this plan will actually execute.  A FAST render must
        # not display a HI-RES step it will never run.
        stages = ["PREPARING", "PROMPTING", "LOADING_MODEL", "GENERATING"]
        if hires_w:
            stages.append("HI_RES")
        if spec.post_upscale:
            stages.append("UPSCALE")
        stages += ["SAVING", "COMPLETE"]

        notes = list(enriched.notes)
        if vram_adapted:
            notes.append("resolution_reduced_for_vram")

        # Resolve the seed here, not inside the graph builder: a plan whose
        # seed is still 0 would be recorded as 0 while the render used a random
        # one, making an image the user liked impossible to reproduce.
        resolved_seed = int(seed) or random.SystemRandom().randint(1, 2**31 - 1)
        return RenderPlan(
            mode=mode, mode_reason=reason, image_type=resolved_type,
            width=base_w, height=base_h, steps=spec.base_steps,
            cfg=1.0, shift=spec.shift + pipeline.shift_bonus,
            sampler="res_multistep", scheduler="simple", seed=resolved_seed,
            hires_width=hires_w, hires_height=hires_h,
            hires_denoise=spec.hires_denoise if hires_w else 0.0,
            hires_steps=spec.hires_steps if hires_w else 0,
            post_upscale=spec.post_upscale, sharpen=spec.sharpen,
            prompt=enriched.prompt, negative=enriched.negative,
            negative_active=enriched.negative_active, subject=enriched.subject,
            exact_text=enriched.exact_text, notes=notes,
            vram_adapted=vram_adapted,
            budget_s=estimate_budget_s(
                base_w, base_h, spec.base_steps,
                hires_width=hires_w, hires_height=hires_h,
                hires_steps=spec.hires_steps if hires_w else 0,
                post_upscale=spec.post_upscale),
            stages=stages)
