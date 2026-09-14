"""Outils d'analyse de fichiers et d'images — capacités RÉELLES de JARVIS.

Ces outils opèrent sur les pièces jointes stockées (``attachment_id``) :
lecture du contenu réel, inspection, analyse vision d'image (modèle
multimodal — JAMAIS ComfyUI) et parsing de documents (PDF/CSV/XLSX/DOCX).

Un fichier joint n'est jamais exécuté : uniquement lu et analysé.
"""
from __future__ import annotations

from typing import Any

from ..attachments import AttachmentError
from ..permissions import READ_ONLY
from .base import ToolContext, ToolResult, registry

CATEGORY = "Analyse de fichiers"


def _require_attachment(ctx: ToolContext):
    store = ctx.core.attachments
    aid = str(ctx.arguments.get("attachment_id") or "").strip()
    if not aid:
        return None, ToolResult(False, "attachment_id manquant : fournis l'identifiant de la pièce jointe.")
    try:
        return store.require(aid), None
    except AttachmentError as exc:
        return None, ToolResult(False, str(exc))


def _file_inspect(ctx: ToolContext) -> ToolResult:
    att, err = _require_attachment(ctx)
    if err:
        return err
    x = ctx.core.attachments.extract(att)
    summary = x.get("text", "") if isinstance(x, dict) else ""
    if x.get("kind") in ("csv", "xlsx"):
        summary = ""
    public = att.public()
    lines = [
        f"Fichier : {public['filename']}",
        f"Type réel : {public['kind_label']} ({public['mime']})",
        f"Taille : {att.size // 1024} Ko",
        f"Identifiant : {att.id}",
    ]
    if x.get("kind") == "pdf":
        lines.append(f"Pages : {x.get('page_count', 0)}"
                     + ("" if x.get("text_extracted") else " — PDF image/scan, texte non extractible"))
    elif x.get("kind") == "csv":
        lines.append(f"Lignes (hors en-tête) : {x.get('row_count', 0)}")
        lines.append("Colonnes : " + ", ".join(x.get("columns") or []))
        lines.append("Types : " + ", ".join(f"{k} → {v}" for k, v in (x.get("col_types") or {}).items()))
    elif x.get("kind") == "xlsx":
        for s in x.get("sheets") or []:
            lines.append(f"Onglet « {s['name']} » : {s['rows']} ligne(s), {s['cols']} colonne(s)")
    elif x.get("kind") == "docx":
        lines.append(f"Paragraphes : {x.get('paragraph_count', 0)}, tableaux : {x.get('table_count', 0)}")
    elif x.get("kind") == "image":
        lines.append(f"Dimensions : {att.width}×{att.height}")
    if summary:
        lines.append("Extrait :\n" + summary[:800])
    return ToolResult(True, "\n".join(lines), data=public)


registry.add(
    id="file.inspect", name="Inspecter un fichier", category=CATEGORY,
    description="Renvoie la fiche d'une pièce jointe : type réel, taille, pages (PDF), "
                "colonnes (CSV/XLSX), dimensions (image). Ne remplace pas la lecture du contenu.",
    handler=_file_inspect, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "attachment_id": {"type": "string", "description": "Identifiant de la pièce jointe (att_…)"}},
        "required": ["attachment_id"]},
)


def _file_read(ctx: ToolContext) -> ToolResult:
    att, err = _require_attachment(ctx)
    if err:
        return err
    if att.kind == "image":
        return ToolResult(True, "C'est une image : utilise image.inspect (question) pour l'analyser.",
                          data=att.public())
    x = ctx.core.attachments.extract(att)
    if x.get("kind") == "text":
        content = x.get("text", "")
    elif x.get("kind") == "pdf":
        content = x.get("text", "")
    elif x.get("kind") == "csv":
        content = ctx.core.attachments.context_block(att)["text"]
    elif x.get("kind") == "docx":
        content = x.get("text", "")
    else:
        content = ctx.core.attachments.context_block(att)["text"]
    content = content or "(fichier vide)"
    return ToolResult(True, content, data={"attachment": att.public(),
                                           "truncated": bool(x.get("truncated"))})


registry.add(
    id="file.read", name="Lire un fichier joint", category=CATEGORY,
    description="Lit le contenu réel d'une pièce jointe texte/code/PDF/CSV/DOCX et le renvoie "
                "pour analyse. Pour une image, utilise image.inspect.",
    handler=_file_read, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "attachment_id": {"type": "string", "description": "Identifiant de la pièce jointe (att_…)"}},
        "required": ["attachment_id"]},
)


def _vision(ctx: ToolContext, prompt: str, system: str) -> ToolResult:
    att, err = _require_attachment(ctx)
    if err:
        return err
    if att.kind != "image":
        return ToolResult(False, "Ce fichier n'est pas une image : use file.read / document.parse.")
    try:
        import base64
        raw = base64.b64encode(att.path.read_bytes()).decode("ascii")
    except OSError as exc:
        return ToolResult(False, f"Lecture de l'image impossible : {exc}")
    response = ctx.core.llm.analyze_images([raw], prompt, system=system)
    if not response.ok:
        reason = (response.error or "").lower()
        if "vision" in reason or "image" in reason:
            return ToolResult(False, "Analyse d'image indisponible : aucun modèle vision "
                                     "connecté (Ollama avec un modèle multimodal, ou clé d'un "
                                     "fournisseur vision).")
        return ToolResult(False, f"Analyse d'image impossible : {response.error[:300]}")
    return ToolResult(True, (response.text or "").strip(), data={
        "attachment": att.public(), "vision_model": response.model or "",
        "provider": response.provider or ""})


_VISION_SYSTEM = (
    "Tu es un module d'analyse visuelle de JARVIS. Décris uniquement ce que tu observes "
    "réellement dans l'image fournie. Ne devine pas ce que tu ne vois pas. Réponds en français."
)


def _image_inspect(ctx: ToolContext) -> ToolResult:
    question = str(ctx.arguments.get("question") or "").strip()
    if not question:
        return ToolResult(False, "Indique une question ou un point précis à vérifier (question).")
    return _vision(ctx, f"Question de l'utilisateur sur cette image : {question}", _VISION_SYSTEM)


registry.add(
    id="image.inspect", name="Analyser une image (question)", category=CATEGORY,
    description="Envoie une image jointe à un vrai modèle vision avec la question de l'utilisateur "
                "et répond précisément (problème, texte affiché, comparaison…).",
    handler=_image_inspect, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "attachment_id": {"type": "string"},
        "question": {"type": "string", "description": "Question spécifique sur l'image"}},
        "required": ["attachment_id", "question"]},
)


def _image_describe(ctx: ToolContext) -> ToolResult:
    return _vision(ctx, "Décris cette image en détail (sujet, contenu, éléments visibles, éventuels textes).",
                   _VISION_SYSTEM)


registry.add(
    id="image.describe", name="Décrire une image", category=CATEGORY,
    description="Produit une description détaillée d'une image jointe via le modèle vision.",
    handler=_image_describe, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "attachment_id": {"type": "string"}}, "required": ["attachment_id"]},
)


def _document_parse(ctx: ToolContext) -> ToolResult:
    att, err = _require_attachment(ctx)
    if err:
        return err
    if att.kind not in {"pdf", "csv", "xlsx", "docx"}:
        return ToolResult(False, "document.parse opère sur PDF, CSV, XLSX ou DOCX.")
    try:
        x = ctx.core.attachments.extract(att)
    except AttachmentError as exc:
        return ToolResult(False, str(exc))
    block = ctx.core.attachments.context_block(att)["text"]
    return ToolResult(True, block, data={"kind": att.kind, "extract": x})


registry.add(
    id="document.parse", name="Parser un document", category=CATEGORY,
    description="Analyse déterministe d'un PDF (pages + texte), CSV (colonnes/lignes/types), "
                "XLSX (onglets réels) ou DOCX (paragraphes).",
    handler=_document_parse, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "attachment_id": {"type": "string"}}, "required": ["attachment_id"]},
)


def _file_ingest(ctx: ToolContext) -> ToolResult:
    """Importe un fichier local déjà présent (ex. téléchargement navigateur) dans la session."""
    path = str(ctx.arguments.get("path") or "").strip()
    filename = str(ctx.arguments.get("filename") or "").strip()
    if not path:
        return ToolResult(False, "Chemin du fichier manquant.")
    try:
        att = ctx.core.attachments.ingest_file(path, filename)
    except AttachmentError as exc:
        return ToolResult(False, str(exc))
    return ToolResult(True, f"Fichier « {att.filename} » importé.", data=att.public())


registry.add(
    id="file.ingest", name="Importer un fichier local", category=CATEGORY,
    description="Importe un fichier déjà présent sur la machine (ex. téléchargement via le "
                "navigateur) dans la session pour pouvoir l'analyser.",
    handler=_file_ingest, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "path": {"type": "string", "description": "Chemin absolu du fichier"},
        "filename": {"type": "string"}}, "required": ["path"]},
)