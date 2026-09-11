"""Outils LLM pour la mise à jour d'avatar depuis une image de référence.

Ces outils permettent au modèle de :
- Ajouter une image de référence
- Analyser une image de référence
- Lancer la mise à jour de l'avatar
- Consulter les révisions
- Valider / rejeter une révision
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..permissions import SAFE_WRITE, SENSITIVE
from .base import ToolContext, ToolResult, registry

CATEGORY = "Avatar 3D"


# ---------------------------------------------------------------------------
# avatar.reference.add
# ---------------------------------------------------------------------------
def _ref_add(ctx: ToolContext) -> ToolResult:
    args = ctx.arguments
    path = str(args.get("path") or args.get("image_path") or "").strip()
    if not path:
        return ToolResult(False, "Chemin de l'image manquant.")
    if not Path(path).is_file():
        return ToolResult(False, f"Fichier introuvable : {path}")
    ref_type = str(args.get("reference_type") or "mixed").strip()
    tags = args.get("tags") or []
    try:
        ref = ctx.core.avatar_ref.add(
            path, reference_type=ref_type, tags=tags,
            conversation_id=ctx.conversation_id)
    except ValueError as exc:
        return ToolResult(False, str(exc))
    return ToolResult(
        True,
        f"Image de référence enregistrée (id: {ref['id']}, type: {ref['reference_type']}).",
        data={"reference": ref}, risk=SAFE_WRITE)


registry.add(
    id="avatar.reference.add", name="Ajouter une référence avatar", category=CATEGORY,
    description=(
        "Enregistre une image de référence pour modifier l'avatar 3D de JARVIS. "
        "L'image doit être un fichier local (PNG, JPG, WEBP). Le type de référence "
        "précise ce que l'image montre : human_face, full_body, outfit, "
        "hairstyle, stylized_character, color_style, pose_reference, ou mixed."
    ),
    handler=_ref_add, risk=SAFE_WRITE, permissions=("execute", "write"),
    confirmation_policy="never",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string",
                     "description": "Chemin local de l'image de référence."},
            "reference_type": {
                "type": "string",
                "enum": ["human_face", "full_body", "outfit",
                         "stylized_character", "hairstyle", "color_style",
                         "pose_reference", "mixed"],
                "description": "Type de contenu de l'image."},
            "tags": {"type": "array", "items": {"type": "string"},
                     "description": "Tags descriptifs (ex: ['roux', 'frange', 'yeux verts'])."},
        },
        "required": ["path"],
    },
)


# ---------------------------------------------------------------------------
# avatar.reference.analyze
# ---------------------------------------------------------------------------
def _ref_analyze(ctx: ToolContext) -> ToolResult:
    ref_id = str(ctx.arguments.get("reference_id") or "").strip()
    if not ref_id:
        return ToolResult(False, "reference_id manquant.")
    try:
        features = ctx.core.avatar_ref.analyze(ref_id)
    except ValueError as exc:
        return ToolResult(False, str(exc))
    lines = [f"Référence {ref_id} analysée :"]
    for key, val in features.items():
        if key == "modifications":
            if val:
                lines.append("  Modifications prévues :")
                for mk, mv in val.items():
                    lines.append(f"    - {mk}: {mv}")
        elif val:
            lines.append(f"  {key}: {val}")
    return ToolResult(True, "\n".join(lines), data={"features": features},
                      risk=SAFE_WRITE)


registry.add(
    id="avatar.reference.analyze", name="Analyser une référence avatar",
    category=CATEGORY,
    description=(
        "Analyse une image de référence déjà uploadée et extrait les "
        "caractéristiques visuelles (visage, coiffure, vêtements, couleurs, "
        "style). Doit être appelé avant avatar.update_from_reference."
    ),
    handler=_ref_analyze, risk=SAFE_WRITE, permissions=("read",),
    confirmation_policy="never",
    input_schema={
        "type": "object",
        "properties": {
            "reference_id": {"type": "string",
                             "description": "ID de la référence à analyser."},
        },
        "required": ["reference_id"],
    },
)


# ---------------------------------------------------------------------------
# avatar.reference.list
# ---------------------------------------------------------------------------
def _ref_list(ctx: ToolContext) -> ToolResult:
    refs = ctx.core.avatar_ref.list(
        limit=int(ctx.arguments.get("limit") or 20))
    if not refs:
        return ToolResult(True, "Aucune image de référence enregistrée.",
                          data={"references": []})
    lines = [f"{r['id']} · {r['original_name']} · {r['reference_type']} "
             f"· analysée: {'oui' if r.get('extracted_features') else 'non'}"
             for r in refs]
    return ToolResult(True, "\n".join(lines), data={"references": refs},
                      risk=SAFE_WRITE)


registry.add(
    id="avatar.reference.list", name="Lister les références avatar",
    category=CATEGORY,
    description="Liste les images de référence enregistrées pour l'avatar.",
    handler=_ref_list, risk=SAFE_WRITE, permissions=("read",),
    confirmation_policy="never",
    input_schema={"type": "object", "properties": {
        "limit": {"type": "integer"}}, "required": []},
)


# ---------------------------------------------------------------------------
# avatar.update_from_reference
# ---------------------------------------------------------------------------
def _update(ctx: ToolContext) -> ToolResult:
    args = ctx.arguments
    ref_id = str(args.get("reference_id") or "").strip()
    if not ref_id:
        return ToolResult(False, "reference_id manquant.")
    ref = ctx.core.avatar_ref.get(ref_id)
    if not ref:
        return ToolResult(False, f"Référence inconnue : {ref_id}")
    options = dict(args.get("options") or {})
    if not options:
        text_modifiers = str(args.get("instruction") or "")
        from ..avatar_update import AvatarUpdateOptions
        opts = AvatarUpdateOptions.from_text(text_modifiers)
        options = opts.to_dict()
    if not ref.get("extracted_features"):
        ctx.core.avatar_ref.analyze(ref_id)
    ctx.log(f"Début mise à jour avatar depuis référence {ref_id}")
    # Job LIVE : le worker Blender tourne en tâche de fond et publie ses
    # previews au fil de l'eau. La conversation n'est jamais bloquée, et le
    # job apparaît immédiatement dans Avatar Studio.
    quality = str(args.get("quality") or "balanced")
    result = ctx.core.avatar_live.start(
        ref_id, options=options, quality=quality,
        conversation_id=ctx.conversation_id,
        title="Refonte de l'avatar depuis une référence")
    if not result.get("ok"):
        return ToolResult(False, f"Échec : {result.get('error', 'inconnu')}",
                          risk=SAFE_WRITE)
    job = result["job"]
    stages = ", ".join(s["label"] for s in job.get("stages", []))
    lines = [
        f"Job avatar lancé ({job['id']}).",
        f"Étapes prévues : {stages}.",
        "Avatar Studio montre le modèle 3D évoluer en direct, étape par étape. "
        "Le Command Center garde l'avatar actuel tant que l'utilisateur n'a pas "
        "accepté le résultat. Réponds en une phrase courte, sans inventer de "
        "progression ni de résultat.",
    ]
    return ToolResult(
        True, "\n".join(lines),
        data={"job_id": job["id"], "status": job["status"],
              "stages": [s["id"] for s in job.get("stages", [])],
              "studio_url": f"/api/avatar/jobs/{job['id']}"},
        risk=SAFE_WRITE,
        artifacts=[{"type": "avatar_live_job", "job_id": job["id"]}])


registry.add(
    id="avatar.update_from_reference", name="Modifier l'avatar depuis une référence",
    category=CATEGORY,
    description=(
        "Lance le pipeline complet de mise à jour de l'avatar 3D de JARVIS "
        "en utilisant une image de référence. Analyse l'image, modifie le "
        "mesh/animation/materials avec Blender, rend des aperçus, évalue "
        "et améliore automatiquement. Répond à « Modifie ton avatar selon "
        "cette image », « Adapte ton visage à cette photo », « Change ta "
        "tenue selon cette image »."
    ),
    handler=_update, risk=SAFE_WRITE, permissions=("execute", "write"),
    confirmation_policy="never",
    input_schema={
        "type": "object",
        "properties": {
            "reference_id": {"type": "string",
                             "description": "ID de l'image de référence."},
            "instruction": {"type": "string",
                            "description": "Instructions supplémentaires (ex: 'seulement le visage', 'style cartoon')."},
            "quality": {"type": "string", "enum": ["low", "balanced", "high"],
                        "description": "Qualité des previews live (défaut: balanced)."},
            "options": {
                "type": "object",
                "description": ("Options de modification : "
                                "{modify_face, modify_hair, modify_outfit, "
                                "modify_colors, modify_materials, modify_pose, "
                                "modify_proportions, preserve_identity, "
                                "style_strength (0-1), realism_level "
                                "(realistic/balanced/cartoon/anime)}"),
            },
        },
        "required": ["reference_id"],
    },
)


# ---------------------------------------------------------------------------
# avatar.revision.list
# ---------------------------------------------------------------------------
def _rev_list(ctx: ToolContext) -> ToolResult:
    ref_id = str(ctx.arguments.get("reference_id") or "").strip()
    revs = ctx.core.avatar_ref.list_revisions(
        reference_id=ref_id, limit=int(ctx.arguments.get("limit") or 10))
    if not revs:
        return ToolResult(True, "Aucune révision.", data={"revisions": []})
    lines = []
    for r in revs:
        ev = r.get("evaluation", {})
        score = ev.get("overall_score", 0)
        status = "ACTIF" if r.get("active") else ("accepté" if r.get("accepted") else "en attente")
        lines.append(
            f"{r['id']} · {status} · score: {score:.0%} · "
            f"réf: {r['reference_id']}")
    return ToolResult(True, "\n".join(lines), data={"revisions": revs},
                      risk=SAFE_WRITE)


registry.add(
    id="avatar.revision.list", name="Lister les révisions avatar", category=CATEGORY,
    description="Affiche les révisions de l'avatar (historique des modifications).",
    handler=_rev_list, risk=SAFE_WRITE, permissions=("read",),
    confirmation_policy="never",
    input_schema={"type": "object", "properties": {
        "reference_id": {"type": "string"},
        "limit": {"type": "integer"}}, "required": []},
)


# ---------------------------------------------------------------------------
# avatar.revision.accept
# ---------------------------------------------------------------------------
def _rev_accept(ctx: ToolContext) -> ToolResult:
    rev_id = str(ctx.arguments.get("revision_id") or "").strip()
    if not rev_id:
        return ToolResult(False, "revision_id manquant.")
    if not ctx.core.avatar_ref.get_revision(rev_id):
        return ToolResult(False, f"Révision inconnue : {rev_id}")
    ok = ctx.core.avatar_ref.accept_revision(rev_id)
    return ToolResult(
        ok, f"Révision {rev_id} {'activée' if ok else 'introuvable'}.",
        risk=SAFE_WRITE)


registry.add(
    id="avatar.revision.accept", name="Activer une révision avatar",
    category=CATEGORY,
    description=(
        "Active une révision d'avatar comme avatar courant. "
        "L'ancienne version est conservée comme backup."
    ),
    handler=_rev_accept, risk=SAFE_WRITE, permissions=("execute", "write"),
    confirmation_policy="never",
    input_schema={"type": "object", "properties": {
        "revision_id": {"type": "string"}}, "required": ["revision_id"]},
)


# ---------------------------------------------------------------------------
# avatar.revision.rollback
# ---------------------------------------------------------------------------
def _rev_rollback(ctx: ToolContext) -> ToolResult:
    rev_id = str(ctx.arguments.get("revision_id") or "").strip()
    if not rev_id:
        return ToolResult(False, "revision_id manquant.")
    if not ctx.core.avatar_ref.get_revision(rev_id):
        return ToolResult(False, f"Révision inconnue : {rev_id}")
    ok = ctx.core.avatar_ref.rollback(rev_id)
    return ToolResult(
        ok, f"Révision {rev_id} {'désactivée' if ok else 'introuvable'}.",
        risk=SAFE_WRITE)


registry.add(
    id="avatar.revision.rollback", name="Annuler une révision avatar",
    category=CATEGORY,
    description="Désactive une révision d'avatar (rollback vers la précédente).",
    handler=_rev_rollback, risk=SAFE_WRITE, permissions=("execute", "write"),
    confirmation_policy="never",
    input_schema={"type": "object", "properties": {
        "revision_id": {"type": "string"}}, "required": ["revision_id"]},
)
