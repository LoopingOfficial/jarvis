"""Outils haut niveau AvatarEngine — le LLM choisit, le moteur exécute."""
from __future__ import annotations

from ..avatar_engine.schema import OPERATIONS
from ..permissions import SAFE_WRITE
from .base import ToolContext, ToolResult, registry

CATEGORY = "Avatar Engine"


def _inspect(ctx: ToolContext) -> ToolResult:
    result = ctx.core.avatar_engine.inspect(conversation_id=ctx.conversation_id)
    inv = result.get("inventory") or {}
    identity = inv.get("identity") or {}
    lines = [
        f"AvatarEngine {inv.get('build', '')}",
        f"CURRENT : {identity.get('current_glb') or 'absent'}",
        f"CANDIDATE : {identity.get('candidate_glb') or 'aucun'}",
        f"MASTER : {identity.get('master_blend') or 'aucun'}",
        f"Source : {identity.get('source_blend') or 'génération MPFB requise'}",
        "L'avatar live n'est pas modifié.",
    ]
    scene = inv.get("scene") or {}
    counts = scene.get("counts") or {}
    if counts:
        lines.append(
            f"Scène : {counts.get('objects', 0)} objets · "
            f"{counts.get('meshes', 0)} meshes · {counts.get('triangles', 0)} triangles")
    return ToolResult(True, "\n".join(lines), data=result, risk=SAFE_WRITE)


registry.add(
    id="avatar.engine.inspect", name="Inspecter l'avatar canonique", category=CATEGORY,
    description=(
        "Inventaire FACTUEL de l'avatar JARVIS (CURRENT / CANDIDATE / MASTER). "
        "À utiliser pour « inspecte ton avatar ». N'affiche pas de JSON : exécute."
    ),
    handler=_inspect, risk=SAFE_WRITE, permissions=("read",), confirmation_policy="never",
    input_schema={"type": "object", "properties": {}, "required": []},
)


def _apply(ctx: ToolContext) -> ToolResult:
    args = ctx.arguments
    operation = str(args.get("operation") or "").strip()
    parameters = args.get("parameters") if isinstance(args.get("parameters"), dict) else {}
    result = ctx.core.avatar_engine.apply(
        operation, parameters, conversation_id=ctx.conversation_id,
        features=args.get("features") if isinstance(args.get("features"), dict) else None)
    if not result.get("ok"):
        return ToolResult(False, result.get("error") or "Opération refusée.", data=result,
                          risk=SAFE_WRITE)
    return ToolResult(
        True,
        f"Opération {operation} appliquée sur le CANDIDAT. Avatar live inchangé.",
        data=result, risk=SAFE_WRITE)


registry.add(
    id="avatar.engine.apply", name="Appliquer une opération avatar", category=CATEGORY,
    description=(
        "Exécute une opération déterministe d'AvatarEngine "
        f"({', '.join(OPERATIONS)}). Le LLM fournit operation + parameters ; "
        "le moteur applique. Jamais de script bpy improvisé. "
        "Le résultat est un CANDIDAT, pas l'avatar live."
    ),
    handler=_apply, risk=SAFE_WRITE, permissions=("execute", "write"),
    confirmation_policy="never",
    input_schema={
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": list(OPERATIONS)},
            "parameters": {"type": "object"},
            "features": {"type": "object"},
        },
        "required": ["operation"],
    },
)


def _build(ctx: ToolContext) -> ToolResult:
    features = ctx.arguments.get("features") if isinstance(ctx.arguments.get("features"), dict) else {}
    if not features.get("analysis_success"):
        ref_id = str(ctx.arguments.get("reference_id") or "").strip()
        if ref_id:
            features = ctx.core.avatar_ref.analyze(ref_id)
        else:
            latest = ctx.core.avatar_ref.list(limit=1)
            if latest:
                features = ctx.core.avatar_ref.analyze(latest[0]["id"])
    if not features.get("analysis_success"):
        return ToolResult(
            False,
            "Vision obligatoire : aucune analyse de référence exploitable. STOP.",
            risk=SAFE_WRITE)
    result = ctx.core.avatar_engine.build_from_reference(
        features, conversation_id=ctx.conversation_id,
        milestone=str(ctx.arguments.get("milestone") or "appearance"))
    if not result.get("ok"):
        return ToolResult(False, result.get("error") or "Build en échec.", data=result,
                          risk=SAFE_WRITE)
    return ToolResult(
        True,
        "Candidat AvatarEngine construit. En attente d'acceptation. Avatar live inchangé.",
        data=result, risk=SAFE_WRITE)


registry.add(
    id="avatar.engine.build", name="Construire le candidat avatar", category=CATEGORY,
    description=(
        "Crée le candidat humain avec MPFB2, applique morphs / cheveux / tenue / "
        "matériaux à partir des contraintes Vision, exporte un CANDIDAT GLB statique. "
        "Le rig, le facial rig, les visèmes et les animations sont bloqués au premier jalon. "
        "N'écrase jamais l'avatar live."
    ),
    handler=_build, risk=SAFE_WRITE, permissions=("execute", "write"),
    confirmation_policy="never",
    input_schema={
        "type": "object",
        "properties": {
            "reference_id": {"type": "string"},
            "features": {"type": "object"},
            "milestone": {"type": "string",
                          "enum": ["appearance", "rig", "animation", "integration"]},
        },
        "required": [],
    },
)


def _accept(ctx: ToolContext) -> ToolResult:
    result = ctx.core.avatar_engine.accept_candidate()
    if not result.get("ok"):
        return ToolResult(False, result.get("error") or "Acceptation impossible.",
                          data=result, risk=SAFE_WRITE)
    return ToolResult(True, "Candidat adopté : il devient l'identité 3D permanente.",
                      data=result, risk=SAFE_WRITE)


registry.add(
    id="avatar.engine.accept", name="Adopter le candidat avatar", category=CATEGORY,
    description="Remplace l'avatar live UNIQUEMENT après validation humaine du candidat.",
    handler=_accept, risk=SAFE_WRITE, permissions=("execute", "write"),
    confirmation_policy="never",
    input_schema={"type": "object", "properties": {}, "required": []},
)
