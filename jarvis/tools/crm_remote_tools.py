"""Outils du CRM distant : recherche, envoi d'une fiche, journal d'échange.

Ces outils doublent ceux de `crm_tools.py`, qui restent la voie par défaut :
le carnet local est instantané et fonctionne hors-ligne. On ne va sur le
réseau que lorsque la question porte explicitement sur la base partagée, ou
qu'il s'agit d'y publier quelque chose. Les descriptions le disent au modèle,
faute de quoi il choisirait le CRM distant pour un simple « qui est Martin ? »
et ferait attendre l'utilisateur le temps d'un aller-retour HTTP.
"""
from __future__ import annotations

from ..crm import label_of
from ..crm_remote import CrmRemoteClient, CrmRemoteError, to_local
from ..permissions import READ_ONLY, SAFE_WRITE
from .base import ToolContext, ToolResult, registry


def _client(ctx: ToolContext) -> CrmRemoteClient:
    # Le client est réutilisé s'il a été posé sur le core (tests, injection),
    # sinon construit depuis l'environnement. Il ne garde pas de connexion :
    # rien à fermer, rien à réinitialiser quand la configuration change.
    client = getattr(ctx.core, "crm_remote", None)
    return client if client is not None else CrmRemoteClient()


def _unconfigured(client: CrmRemoteClient) -> ToolResult | None:
    if client.configured:
        return None
    # Message actionnable plutôt qu'une trace : c'est une configuration
    # absente, pas une panne, et l'utilisateur peut y remédier lui-même.
    return ToolResult(
        False,
        "CRM distant non configuré : renseigne CRM_API_URL et CRM_API_KEY, "
        "ou utilise le carnet local (crm.search_contact).",
        data={"configured": False},
    )


# -- recherche ------------------------------------------------------------

def _remote_search(ctx: ToolContext) -> ToolResult:
    query = str(ctx.arguments.get("query") or "").strip()
    if not query:
        return ToolResult(False, "Indique un nom, une société ou un e-mail à chercher.")
    client = _client(ctx)
    if (refus := _unconfigured(client)) is not None:
        return refus
    try:
        body = client.search(query, limit=int(ctx.arguments.get("limit") or 20))
    except CrmRemoteError as exc:
        return ToolResult(False, str(exc), data={"remote_error": True})

    items = body.get("items") or []
    if not items:
        return ToolResult(False, f"Aucun contact distant ne correspond à « {query} ».",
                          data={"query": query, "total": 0, "contacts": []})

    contacts = [to_local(item) for item in items]
    lines = "\n".join(
        f"- {label_of(c)}" + (f" · {c['email']}" if c.get("email") else "")
        for c in contacts
    )
    total = body.get("total", len(items))
    # Le total complet est rappelé : voir 20 lignes sans savoir qu'il y en a
    # 200 ferait conclure à tort que le client cherché n'existe pas.
    suffix = f" (sur {total} au total)" if total > len(items) else ""
    return ToolResult(
        True, f"{len(items)} contact(s) dans le CRM distant{suffix} :\n{lines}",
        data={"query": query, "total": total, "contacts": contacts},
    )


registry.add(
    id="crm.remote_search", name="Chercher dans le CRM distant", category="CRM",
    description=(
        "Cherche un contact dans le CRM partagé (base distante), par nom, société, "
        "e-mail ou téléphone. N'utilise cet outil que si la question porte sur la "
        "base partagée : pour une recherche ordinaire, crm.search_contact interroge "
        "le carnet local, répond instantanément et fonctionne hors-ligne."
    ),
    handler=_remote_search, risk=READ_ONLY, agents=("jarvis", "email", "task"),
    input_schema={"type": "object", "properties": {
        "query": {"type": "string", "description": "Nom, société, e-mail ou téléphone."},
        "limit": {"type": "integer"}}, "required": ["query"]},
)


# -- envoi d'une fiche ----------------------------------------------------

def _remote_push(ctx: ToolContext) -> ToolResult:
    args = ctx.arguments
    contact_id = str(args.get("contact_id") or "").strip()

    if contact_id:
        store = getattr(ctx.core, "crm", None)
        if store is None:
            return ToolResult(False, "CRM local indisponible.")
        contact = store.get(contact_id)
        if not contact:
            return ToolResult(False, f"Aucun contact local d'identifiant « {contact_id} ».")
    else:
        contact = {k: args.get(k) for k in
                   ("name", "company", "email", "phone", "address", "notes")
                   if args.get(k) is not None}
        if not str(contact.get("name") or "").strip():
            return ToolResult(False, "Donne un contact_id local, ou au moins un nom.")

    client = _client(ctx)
    if (refus := _unconfigured(client)) is not None:
        return refus
    try:
        result = client.push_contact(contact)
    except CrmRemoteError as exc:
        return ToolResult(False, str(exc), data={"remote_error": True})

    remote = result.get("contact") or {}
    verbe = "mis à jour" if result.get("action") == "updated" else "créé"
    try:
        ctx.core.events.emit("crm.remote.pushed", {
            "action": result.get("action"), "remote_id": remote.get("id"),
            "task_id": ctx.task_id})
    except Exception:
        pass
    return ToolResult(
        True,
        f"Contact {verbe} dans le CRM distant : "
        f"{remote.get('full_name', '')} (id distant {remote.get('id')}).",
        data={"action": result.get("action"), "remote_id": remote.get("id"),
              "contact": to_local(remote)},
        risk=SAFE_WRITE,
    )


registry.add(
    id="crm.remote_push_contact", name="Envoyer un contact au CRM distant", category="CRM",
    description=(
        "Publie une fiche contact dans le CRM partagé. Si un contact y porte déjà "
        "le même e-mail, sa fiche est mise à jour au lieu d'être dupliquée. "
        "Donne de préférence contact_id (fiche du carnet local) ; sinon les champs "
        "un par un. Le numéro de TVA reste local et n'est pas envoyé."
    ),
    handler=_remote_push, risk=SAFE_WRITE, agents=("jarvis", "email", "task"),
    # Écriture sortante, sur une base partagée avec d'autres personnes : on
    # demande confirmation, que SAFE_WRITE seul ne déclencherait pas.
    confirmation_policy="always",
    dangerous_hint="Une fiche va être écrite dans le CRM partagé distant.",
    input_schema={"type": "object", "properties": {
        "contact_id": {"type": "string", "description": "Identifiant du contact local à publier."},
        "name": {"type": "string"}, "company": {"type": "string"},
        "email": {"type": "string"}, "phone": {"type": "string"},
        "address": {"type": "string"}, "notes": {"type": "string"}}, "required": []},
)


# -- journal d'échange ----------------------------------------------------

def _remote_log(ctx: ToolContext) -> ToolResult:
    args = ctx.arguments
    try:
        remote_id = int(args.get("remote_id"))
    except (TypeError, ValueError):
        return ToolResult(
            False,
            "remote_id requis (identifiant du contact dans le CRM distant, "
            "renvoyé par crm.remote_search).",
        )
    content = str(args.get("content") or "").strip()
    subject = str(args.get("subject") or "").strip()
    if not content and not subject:
        return ToolResult(False, "Indique au moins un objet ou un contenu à journaliser.")

    kind = str(args.get("kind") or "note").strip().lower()
    valides = {"call", "email", "meeting", "note", "task", "other"}
    if kind not in valides:
        return ToolResult(False, f"kind doit être l'un de : {', '.join(sorted(valides))}.")

    client = _client(ctx)
    if (refus := _unconfigured(client)) is not None:
        return refus
    try:
        created = client.add_interaction(
            remote_id, kind=kind, subject=subject or None, content=content or None
        )
    except CrmRemoteError as exc:
        return ToolResult(False, str(exc), data={"remote_error": True})

    return ToolResult(
        True, f"Échange journalisé ({kind}) sur le contact distant {remote_id}.",
        data={"interaction": created}, risk=SAFE_WRITE,
    )


registry.add(
    id="crm.remote_log_interaction", name="Journaliser un échange (CRM distant)",
    category="CRM",
    description=(
        "Ajoute un appel, un e-mail, une réunion ou une note au fil d'un contact du "
        "CRM partagé. remote_id est l'identifiant distant renvoyé par crm.remote_search "
        "ou crm.remote_push_contact — ce n'est pas l'identifiant local « ct_… »."
    ),
    handler=_remote_log, risk=SAFE_WRITE, agents=("jarvis", "email", "task"),
    confirmation_policy="always",
    dangerous_hint="Une ligne va être ajoutée au fil d'un contact du CRM partagé.",
    input_schema={"type": "object", "properties": {
        "remote_id": {"type": "integer", "description": "Identifiant du contact distant."},
        "kind": {"type": "string", "enum": ["call", "email", "meeting", "note", "task", "other"]},
        "subject": {"type": "string"}, "content": {"type": "string"}},
        "required": ["remote_id"]},
)
