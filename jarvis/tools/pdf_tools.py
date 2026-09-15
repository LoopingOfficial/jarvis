"""Génération de factures et de devis PDF.

Le destinataire passe par le CRM : si le nom est ambigu, on s'arrête et on
demande, exactement comme `crm.search_contact`. Générer un devis pour le
mauvais « Martin » puis le faire confirmer ne corrigerait rien — l'utilisateur
validerait un nom qui lui semble juste.
"""
from __future__ import annotations

from typing import Any

from ..crm import AMBIGUOUS, NOT_FOUND, clarification_question
from ..invoicing import INVOICE, QUOTE, build_document, render_pdf
from ..permissions import SAFE_WRITE
from .base import ToolContext, ToolResult, registry

_KIND_WORD = {INVOICE: "facture", QUOTE: "devis"}


def _issuer(ctx: ToolContext) -> dict[str, Any]:
    """Émetteur lu dans les réglages : on n'invente jamais une raison sociale."""
    try:
        general = ctx.core.settings.section("general") or {}
        billing = ctx.core.settings.section("billing") or {}
    except Exception:
        general, billing = {}, {}
    return {
        "name": billing.get("company_name") or general.get("user_name") or "",
        "address": billing.get("address") or "",
        "email": billing.get("email") or "",
        "vat_number": billing.get("vat_number") or "",
    }


def _existing_numbers(ctx: ToolContext, kind: str) -> list[str]:
    """Numéros déjà utilisés, lus sur le disque : la séquence ne se réinitialise
    pas au redémarrage et deux documents ne peuvent pas porter le même numéro."""
    from ..invoicing import EXPORT_DIR
    try:
        return [p.stem.split("_", 1)[1] for p in EXPORT_DIR.glob(f"{kind}_*.pdf")
                if "_" in p.stem]
    except Exception:
        return []


def _resolve_contact(ctx: ToolContext, raw: Any) -> tuple[dict[str, Any] | None, ToolResult | None]:
    """Résout le destinataire. Renvoie (contact, None) ou (None, réponse à rendre)."""
    if isinstance(raw, dict) and raw.get("name"):
        return dict(raw), None          # fiche fournie telle quelle
    query = str(raw or "").strip()
    if not query:
        return None, ToolResult(False, "Indique le client destinataire du document.")
    store = getattr(ctx.core, "crm", None)
    if store is None:
        return None, ToolResult(False, "CRM indisponible.")
    # Un identifiant direct court-circuite la recherche.
    direct = store.get(query)
    if direct:
        return direct, None
    result = store.resolve(query)
    if result["status"] == NOT_FOUND:
        return None, ToolResult(
            False, f"Aucun contact ne correspond à « {query} ». "
                   "Crée la fiche avec crm.save_contact, ou donne les coordonnées complètes.",
            data={"status": NOT_FOUND, "query": query})
    if result["status"] == AMBIGUOUS:
        question = clarification_question(result)
        try:
            ctx.core.events.emit("crm.clarification.needed", {
                "query": query, "options": result["options"], "question": question,
                "task_id": ctx.task_id})
        except Exception:
            pass
        return None, ToolResult(True, question, data={
            "status": AMBIGUOUS, "query": query, "options": result["options"],
            "needs_clarification": True})
    return result["contact"], None


def _generate(kind: str):
    def handler(ctx: ToolContext) -> ToolResult:
        args = ctx.arguments
        contact, early = _resolve_contact(ctx, args.get("contact") or args.get("client"))
        if early is not None:
            return early

        lines = args.get("lines") or args.get("items") or []
        if not isinstance(lines, list) or not lines:
            return ToolResult(False, "Aucune ligne de prestation : impossible de chiffrer.")

        doc = build_document(
            kind, contact, lines,
            number=str(args.get("number") or ""),
            issuer=_issuer(ctx),
            global_discount_pct=args.get("discount_pct") or 0,
            notes=str(args.get("notes") or ""),
            payment_terms_days=int(args.get("payment_terms_days") or 30),
            existing_numbers=_existing_numbers(ctx, kind),
        )
        out = render_pdf(doc)
        if not out.get("ok"):
            return ToolResult(False, str(out.get("error") or "Génération impossible."),
                              data={k: v for k, v in out.items() if k != "ok"})

        try:
            ctx.core.events.emit("document.generated", {**out, "task_id": ctx.task_id})
        except Exception:
            pass
        word = _KIND_WORD.get(kind, "document")
        who = out.get("contact_company") or out.get("contact_name") or ""
        message = (f"{word.capitalize()} {out['number']} — {out['total_ttc_label']} TTC"
                   + (f" pour {who}" if who else "") + ".")
        return ToolResult(True, message, data=out, risk=SAFE_WRITE)

    return handler


_COMMON_SCHEMA = {
    "contact": {"type": "string",
                "description": "Nom, société, e-mail ou identifiant du client destinataire."},
    "lines": {"type": "array", "description": "Lignes de prestation.",
              "items": {"type": "object", "properties": {
                  "description": {"type": "string"},
                  "quantity": {"type": "number"},
                  "unit_price": {"type": "number", "description": "Prix unitaire HT."},
                  "vat_rate": {"type": "number", "description": "Taux de TVA en % (défaut 20)."},
                  "discount_pct": {"type": "number", "description": "Remise sur la ligne en %."}},
                  "required": ["description", "unit_price"]}},
    "discount_pct": {"type": "number", "description": "Remise globale en % appliquée avant TVA."},
    "notes": {"type": "string", "description": "Mention libre affichée sur le document."},
    "number": {"type": "string", "description": "Numéro imposé (sinon séquence automatique)."},
}


registry.add(
    id="pdf.generate_invoice", name="Générer une facture", category="Documents",
    description=(
        "Génère une facture PDF pour un client du CRM. Calcule automatiquement le "
        "sous-total HT, les remises, la TVA par taux et le total TTC, et ajoute les "
        "mentions légales (échéance, pénalités de retard, indemnité de recouvrement). "
        "Si le client est ambigu, l'outil demande lequel avant de générer quoi que ce soit."
    ),
    handler=_generate(INVOICE), risk=SAFE_WRITE, agents=("jarvis", "email", "task"),
    confirmation_policy="always",
    dangerous_hint="Une facture va être émise avec un numéro de séquence.",
    input_schema={"type": "object", "properties": {
        **_COMMON_SCHEMA,
        "payment_terms_days": {"type": "integer", "description": "Délai de paiement en jours (défaut 30)."}},
        "required": ["contact", "lines"]},
)


registry.add(
    id="pdf.generate_quote", name="Générer un devis", category="Documents",
    description=(
        "Génère un devis PDF pour un client du CRM, avec calcul du HT, de la TVA "
        "par taux et du TTC, et la mention de validité et de bon pour accord. "
        "Si le client est ambigu, l'outil demande lequel avant de générer."
    ),
    handler=_generate(QUOTE), risk=SAFE_WRITE, agents=("jarvis", "email", "task"),
    confirmation_policy="always",
    dangerous_hint="Un devis va être émis avec un numéro de séquence.",
    input_schema={"type": "object", "properties": dict(_COMMON_SCHEMA),
                  "required": ["contact", "lines"]},
)
