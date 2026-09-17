"""Outils CRM : recherche de contacts avec levée d'ambiguïté, et écriture.

La recherche ne devine jamais. Quand « Martin » désigne deux clients, l'outil
s'arrête et rend les options : c'est l'utilisateur qui tranche. Une facture
adressée au mauvais client est une erreur qu'une confirmation ne rattrape pas,
puisque l'utilisateur validerait un nom qui lui paraît correct.
"""
from __future__ import annotations

from ..crm import AMBIGUOUS, FOUND, NOT_FOUND, clarification_question, label_of
from ..permissions import READ_ONLY, SAFE_WRITE
from .base import ToolContext, ToolResult, registry


def _store(ctx: ToolContext):
    store = getattr(ctx.core, "crm", None)
    if store is None:
        raise RuntimeError("CRM indisponible : JarvisCore.crm n'est pas initialisé.")
    return store


def _crm_search(ctx: ToolContext) -> ToolResult:
    query = str(ctx.arguments.get("query") or ctx.arguments.get("name") or "").strip()
    if not query:
        return ToolResult(False, "Indique un nom, une société ou un e-mail à chercher.")
    try:
        result = _store(ctx).resolve(query)
    except RuntimeError as exc:
        return ToolResult(False, str(exc))

    if result["status"] == NOT_FOUND:
        # On ne fabrique pas de fiche : l'absence est une information.
        return ToolResult(False, f"Aucun contact ne correspond à « {query} ».",
                          data={"status": NOT_FOUND, "query": query})

    if result["status"] == AMBIGUOUS:
        question = clarification_question(result)
        # L'interface peut proposer les options en boutons plutôt qu'en texte.
        try:
            ctx.core.events.emit("crm.clarification.needed", {
                "query": query, "options": result["options"], "question": question,
                "task_id": ctx.task_id})
        except Exception:
            pass
        # `ok=True` : la recherche a réussi, elle a simplement trouvé plusieurs
        # fiches. Marquer un échec ici salirait le journal d'audit et ferait
        # croire à une panne.
        return ToolResult(True, question, data={
            "status": AMBIGUOUS, "query": query, "options": result["options"],
            "needs_clarification": True,
        })

    contact = result["contact"]
    return ToolResult(True, f"Contact trouvé : {label_of(contact)}.",
                      data={"status": FOUND, "contact": contact})


registry.add(
    id="crm.search_contact", name="Chercher un contact", category="CRM",
    description=(
        "Cherche un client dans le CRM local par nom, société ou e-mail. "
        "Si plusieurs contacts correspondent, l'outil NE choisit pas : il renvoie "
        "les options et la question à poser à l'utilisateur (needs_clarification). "
        "Attends sa réponse avant de continuer — n'en choisis jamais un toi-même."
    ),
    handler=_crm_search, risk=READ_ONLY, agents=("jarvis", "email", "task"),
    input_schema={"type": "object", "properties": {
        "query": {"type": "string", "description": "Nom, société ou e-mail recherché."}},
        "required": ["query"]},
)


def _crm_get(ctx: ToolContext) -> ToolResult:
    contact_id = str(ctx.arguments.get("contact_id") or "").strip()
    if not contact_id:
        return ToolResult(False, "contact_id requis.")
    contact = _store(ctx).get(contact_id)
    if not contact:
        return ToolResult(False, f"Aucun contact d'identifiant « {contact_id} ».")
    return ToolResult(True, f"Contact : {label_of(contact)}.", data={"contact": contact})


registry.add(
    id="crm.get_contact", name="Lire une fiche contact", category="CRM",
    description="Renvoie la fiche complète d'un contact à partir de son identifiant.",
    handler=_crm_get, risk=READ_ONLY, agents=("jarvis", "email", "task"),
    input_schema={"type": "object", "properties": {
        "contact_id": {"type": "string"}}, "required": ["contact_id"]},
)


def _crm_list(ctx: ToolContext) -> ToolResult:
    contacts = _store(ctx).all(limit=int(ctx.arguments.get("limit") or 50))
    if not contacts:
        return ToolResult(True, "Le carnet de contacts est vide.", data={"contacts": []})
    lines = "\n".join(f"- {label_of(c)}" + (f" · {c['email']}" if c.get("email") else "")
                      for c in contacts)
    return ToolResult(True, f"{len(contacts)} contact(s) :\n{lines}", data={"contacts": contacts})


registry.add(
    id="crm.list_contacts", name="Lister les contacts", category="CRM",
    description="Liste les contacts du CRM local.",
    handler=_crm_list, risk=READ_ONLY, agents=("jarvis", "email", "task"),
    input_schema={"type": "object", "properties": {"limit": {"type": "integer"}}, "required": []},
)


def _crm_save(ctx: ToolContext) -> ToolResult:
    args = ctx.arguments
    name = str(args.get("name") or "").strip()
    if not name and not str(args.get("contact_id") or "").strip():
        return ToolResult(False, "Le nom du contact est requis.")
    store = _store(ctx)
    payload = {k: args.get(k) for k in
               ("id", "name", "company", "email", "phone", "address", "vat_number", "notes")
               if args.get(k) is not None}
    if args.get("contact_id"):
        payload["id"] = args["contact_id"]
    contact = store.upsert(payload)
    try:
        ctx.core.events.emit("crm.contact.saved", {"contact": contact, "task_id": ctx.task_id})
    except Exception:
        pass
    return ToolResult(True, f"Contact enregistré : {label_of(contact)}.",
                      data={"contact": contact}, risk=SAFE_WRITE)


registry.add(
    id="crm.save_contact", name="Enregistrer un contact", category="CRM",
    description="Crée ou met à jour une fiche contact dans le CRM local.",
    handler=_crm_save, risk=SAFE_WRITE, agents=("jarvis", "email", "task"),
    # SAFE_WRITE seul ne déclenche PAS de confirmation (permissions.py ne la
    # demande que pour SENSITIVE et DESTRUCTIVE) : on l'impose explicitement.
    confirmation_policy="always",
    dangerous_hint="Une fiche du carnet de contacts va être créée ou modifiée.",
    input_schema={"type": "object", "properties": {
        "contact_id": {"type": "string", "description": "Identifiant à mettre à jour (sinon création)."},
        "name": {"type": "string"}, "company": {"type": "string"},
        "email": {"type": "string"}, "phone": {"type": "string"},
        "address": {"type": "string"}, "vat_number": {"type": "string"},
        "notes": {"type": "string"}}, "required": []},
)
