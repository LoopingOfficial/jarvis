"""Outils de création visuelle exposés au modèle.

Ces trois outils sont la SEULE façon correcte de répondre à « crée-moi une
image ». Ils lancent un vrai job sur le moteur configuré, publient la
progression et les aperçus sur le bus d'événements, puis renvoient l'image.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..imagegen import ImageBackendUnavailable
from ..permissions import SAFE_WRITE
from .base import ToolContext, ToolResult, registry

_SIZES = {
    "square": (1024, 1024),
    "portrait": (832, 1216),
    "landscape": (1216, 832),
    "wide": (1344, 768),
}


def _dimensions(ctx: ToolContext, args: dict[str, Any]) -> tuple[int, int]:
    ratio = str(args.get("aspect_ratio") or "square").lower()
    width, height = _SIZES.get(ratio, _SIZES["square"])
    if args.get("width"):
        width = max(256, min(2048, int(args["width"])))
    if args.get("height"):
        height = max(256, min(2048, int(args["height"])))
    return width, height


def _result(ctx: ToolContext, job: dict[str, Any]) -> ToolResult:
    """Traduit un job terminé en résultat d'outil — jamais de faux succès."""
    if job.get("status") != "completed":
        return ToolResult(
            False,
            f"La génération a échoué. {job.get('error') or 'Le moteur image ne répond pas.'}",
            data={"job": job}, risk=SAFE_WRITE)
    ctx.core.imagegen.attach_message(job["id"], "")
    return ToolResult(
        True,
        f"Image générée ({job.get('width')}×{job.get('height')}, "
        f"{job.get('backend_label') or job.get('backend')}). "
        f"Elle est déjà affichée dans la conversation — job {job['id']}. "
        f"Réponds simplement, sans rien décrire d'autre.",
        data={"job_id": job["id"], "url": job.get("url"), "prompt": job.get("prompt"),
              "original_prompt": (job.get("meta") or {}).get("original_prompt", ""),
              "workflow": (job.get("meta") or {}).get("workflow", ""),
              "model": (job.get("meta") or {}).get("model", {})},
        risk=SAFE_WRITE,
        artifacts=[{"type": "image", "job_id": job["id"], "url": job.get("url"),
                    "prompt": job.get("prompt", ""),
                    "original_prompt": (job.get("meta") or {}).get("original_prompt", "")}])


# ---------------------------------------------------------------------------
# image.generate
# ---------------------------------------------------------------------------
def _generate(ctx: ToolContext) -> ToolResult:
    args = ctx.arguments
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return ToolResult(False, "Aucun prompt fourni pour la génération d'image.")
    width, height = _dimensions(ctx, args)
    try:
        job = ctx.core.imagegen.generate(
            prompt, mode="generate", conversation_id=ctx.conversation_id,
            negative_prompt=str(args.get("negative_prompt") or ""),
            width=width, height=height, steps=int(args.get("steps") or 0),
            seed=int(args.get("seed") or 0), raw_request=str(args.get("prompt") or ""),
            engine_mode=str(args.get("engine_mode") or args.get("mode")
                            or ctx.core.settings.get("image", "default_mode", "auto")))
    except ImageBackendUnavailable as exc:
        return ToolResult(False, str(exc))
    return _result(ctx, job)


registry.add(
    id="image.generate", name="Générer une image", category="Création visuelle",
    description=(
        "Génère une VRAIE image à partir d'une description (packshot produit, affiche, "
        "bannière, miniature, fond d'écran, illustration, logo, visuel marketing…). "
        "À utiliser dès que l'utilisateur demande de créer/générer/faire une image ou un "
        "visuel. N'utilise JAMAIS web.search pour cela. Le prompt doit être descriptif, "
        "en anglais de préférence, et inclure sujet, style, éclairage et rendu."),
    handler=_generate, risk=SAFE_WRITE, permissions=("execute",), confirmation_policy="never",
    input_schema={
        "type": "object",
        "properties": {
            "prompt": {"type": "string",
                       "description": "Description visuelle complète de l'image à produire."},
            "engine_mode": {"type": "string", "enum": ["auto", "fast", "quality"],
                            "description": "Moteur : auto, aperçu rapide fast, ou rendu final quality."},
            "negative_prompt": {"type": "string", "description": "Ce qu'il faut éviter."},
            "aspect_ratio": {"type": "string", "enum": ["square", "portrait", "landscape", "wide"]},
            "width": {"type": "integer"}, "height": {"type": "integer"},
            "steps": {"type": "integer"}, "seed": {"type": "integer"},
        },
        "required": ["prompt"],
    },
    output_schema={"type": "object", "properties": {"job_id": {"type": "string"},
                                                    "url": {"type": "string"}}},
)


# ---------------------------------------------------------------------------
# image.edit
# ---------------------------------------------------------------------------
def _source_job(ctx: ToolContext) -> dict[str, Any] | None:
    job_id = str(ctx.arguments.get("source_job_id") or "").strip()
    if job_id:
        return ctx.core.imagegen.get(job_id)
    return ctx.core.imagegen.last_image(ctx.conversation_id)


def _edit(ctx: ToolContext) -> ToolResult:
    args = ctx.arguments
    source = _source_job(ctx)
    if not source or not source.get("file_path") or not Path(source["file_path"]).is_file():
        return ToolResult(False, "Aucune image précédente à retoucher dans cette conversation.")
    instruction = str(args.get("instruction") or args.get("prompt") or "").strip()
    if not instruction:
        return ToolResult(False, "Précise ce qu'il faut modifier sur l'image.")
    prompt = f"{source.get('prompt', '')}, {instruction}".strip(", ")
    width = int(source.get("width") or 1024)
    height = int(source.get("height") or 1024)
    try:
        job = ctx.core.imagegen.generate(
            prompt, mode="edit", conversation_id=ctx.conversation_id,
            negative_prompt=str(args.get("negative_prompt") or ""),
            width=width, height=height, source_job_id=source["id"],
            source_path=source["file_path"], raw_request=instruction, engine_mode="quality")
    except ImageBackendUnavailable as exc:
        return ToolResult(False, str(exc))
    return _result(ctx, job)


registry.add(
    id="image.edit", name="Retoucher une image", category="Création visuelle",
    description=("Retouche ou modifie une image déjà générée dans la conversation "
                 "(« retouche cette image », « modifie l'image », « change le fond »). "
                 "Reprend la dernière image si aucun source_job_id n'est donné."),
    handler=_edit, risk=SAFE_WRITE, permissions=("execute",), confirmation_policy="never",
    input_schema={
        "type": "object",
        "properties": {
            "instruction": {"type": "string", "description": "Modification demandée."},
            "source_job_id": {"type": "string", "description": "Image à retoucher (facultatif)."},
            "negative_prompt": {"type": "string"},
        },
        "required": ["instruction"],
    },
)


# ---------------------------------------------------------------------------
# image.upscale
# ---------------------------------------------------------------------------
def _upscale(ctx: ToolContext) -> ToolResult:
    source = _source_job(ctx)
    if not source or not source.get("file_path") or not Path(source["file_path"]).is_file():
        return ToolResult(False, "Aucune image précédente à agrandir dans cette conversation.")
    factor = float(ctx.arguments.get("scale") or 1.5)
    factor = max(1.1, min(2.0, factor))
    width = min(2048, int((source.get("width") or 1024) * factor))
    height = min(2048, int((source.get("height") or 1024) * factor))
    try:
        job = ctx.core.imagegen.generate(
            source.get("prompt") or "high quality render", mode="upscale",
            conversation_id=ctx.conversation_id, width=width, height=height,
            source_job_id=source["id"], source_path=source["file_path"],
            raw_request="upscale", engine_mode="quality")
    except ImageBackendUnavailable as exc:
        return ToolResult(False, str(exc))
    return _result(ctx, job)


registry.add(
    id="image.upscale", name="Agrandir une image", category="Création visuelle",
    description="Augmente la définition de la dernière image générée (ou de source_job_id) avec un workflow dédié.",
    handler=_upscale, risk=SAFE_WRITE, permissions=("execute",), confirmation_policy="never",
    input_schema={
        "type": "object",
        "properties": {"source_job_id": {"type": "string"},
                       "scale": {"type": "number", "description": "Facteur, 1.1 à 2.0."}},
        "required": [],
    },
)


# ---------------------------------------------------------------------------
# image.variation / image.improve
# ---------------------------------------------------------------------------
def _variation(ctx: ToolContext) -> ToolResult:
    source = _source_job(ctx)
    if not source:
        return ToolResult(False, "Aucune image précédente pour créer une variante.")
    try:
        job = ctx.core.imagegen.generate(
            (source.get("meta") or {}).get("original_prompt") or source.get("prompt", ""),
            mode="variation", conversation_id=ctx.conversation_id,
            width=int(source.get("width") or 1024), height=int(source.get("height") or 1024),
            source_job_id=source["id"], raw_request=(source.get("meta") or {}).get("original_prompt", ""),
            engine_mode="fast", context={"iteration": True})
    except ImageBackendUnavailable as exc:
        return ToolResult(False, str(exc))
    return _result(ctx, job)


registry.add(
    id="image.variation", name="Créer une variante", category="Création visuelle",
    description="Régénère la dernière image en conservant son intention et en changeant la seed.",
    handler=_variation, risk=SAFE_WRITE, permissions=("execute",), confirmation_policy="never",
    input_schema={"type": "object", "properties": {"source_job_id": {"type": "string"}}},
)


def _improve(ctx: ToolContext) -> ToolResult:
    source = _source_job(ctx)
    if not source:
        return ToolResult(False, "Aucune image précédente à améliorer.")
    try:
        job = ctx.core.imagegen.generate(
            (source.get("meta") or {}).get("original_prompt") or source.get("prompt", ""),
            mode="improve", conversation_id=ctx.conversation_id,
            width=int(source.get("width") or 1024), height=int(source.get("height") or 1024),
            source_job_id=source["id"], source_path=source.get("file_path", ""),
            raw_request=(source.get("meta") or {}).get("original_prompt", ""), engine_mode="quality")
    except ImageBackendUnavailable as exc:
        return ToolResult(False, str(exc))
    return _result(ctx, job)


registry.add(
    id="image.improve", name="Améliorer automatiquement", category="Création visuelle",
    description="Relance une image avec une intention conservée et un profil de raffinement.",
    handler=_improve, risk=SAFE_WRITE, permissions=("execute",), confirmation_policy="never",
    input_schema={"type": "object", "properties": {"source_job_id": {"type": "string"}}},
)
