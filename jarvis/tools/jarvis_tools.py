"""Outils internes JARVIS : mémoire, connaissances, tâches, agenda, automatisations, connecteurs."""
from __future__ import annotations

import time
from typing import Any

from ..permissions import DESTRUCTIVE, READ_ONLY, SAFE_WRITE
from .base import ToolContext, ToolResult, registry


# --- Mémoire ----------------------------------------------------------------
def _memory_save(ctx: ToolContext) -> ToolResult:
    content = str(ctx.arguments.get("content") or "").strip()
    if not content:
        return ToolResult(False, "Contenu à mémoriser manquant.")
    scope = str(ctx.arguments.get("scope") or "user")
    item = ctx.core.memory.add(
        content=content, scope=scope,
        importance=int(ctx.arguments.get("importance") or 3),
        tags=ctx.arguments.get("tags") or [],
        source=f"agent:{ctx.agent}",
        project=str(ctx.arguments.get("project") or ""),
        conversation_id=ctx.conversation_id,
    )
    return ToolResult(True, "Mémorisé.", risk=SAFE_WRITE, data={"id": item["id"]})


registry.add(
    id="memory.save", name="Mémoriser", category="Mémoire",
    description="Enregistre durablement une information sur Jérôme, ses projets, serveurs ou préférences. "
                "scope: user | project | task | conversation.",
    handler=_memory_save, risk=SAFE_WRITE, permissions=("write",),
    input_schema={"type": "object", "properties": {
        "content": {"type": "string"},
        "scope": {"type": "string", "enum": ["user", "project", "task", "conversation"]},
        "importance": {"type": "integer", "description": "1 (faible) à 5 (critique)"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "project": {"type": "string"}}, "required": ["content"]},
)


def _memory_search(ctx: ToolContext) -> ToolResult:
    query = str(ctx.arguments.get("query") or "").strip()
    items = ctx.core.memory.search(query, limit=int(ctx.arguments.get("limit") or 10),
                                   scope=str(ctx.arguments.get("scope") or ""))
    if not items:
        return ToolResult(True, "Aucun souvenir correspondant.", data={"memories": []})
    lines = [f"- [{m['scope']}] {m['content']}" for m in items]
    return ToolResult(True, "\n".join(lines), data={"memories": items})


registry.add(
    id="memory.search", name="Chercher en mémoire", category="Mémoire",
    description="Recherche dans la mémoire persistante de JARVIS.",
    handler=_memory_search, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "query": {"type": "string"}, "scope": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["query"]},
)


def _memory_forget(ctx: ToolContext) -> ToolResult:
    mid = str(ctx.arguments.get("id") or "").strip()
    if not mid:
        return ToolResult(False, "Identifiant du souvenir manquant.")
    ok = ctx.core.memory.delete(mid)
    return ToolResult(ok, "Souvenir supprimé." if ok else "Souvenir introuvable.", risk=DESTRUCTIVE)


registry.add(
    id="memory.forget", name="Oublier", category="Mémoire",
    description="Supprime définitivement un souvenir par son identifiant.",
    handler=_memory_forget, risk=DESTRUCTIVE, permissions=("destructive",),
    dangerous_hint="Ce souvenir sera définitivement supprimé.",
    input_schema={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
)


# --- Base de connaissances --------------------------------------------------
def _knowledge_add(ctx: ToolContext) -> ToolResult:
    title = str(ctx.arguments.get("title") or "").strip()
    content = str(ctx.arguments.get("content") or "").strip()
    if not title or not content:
        return ToolResult(False, "Titre et contenu requis.")
    item = ctx.core.memory.knowledge_add(
        title=title, content=content, kind=str(ctx.arguments.get("kind") or "note"),
        tags=ctx.arguments.get("tags") or [], source=f"agent:{ctx.agent}",
        project=str(ctx.arguments.get("project") or ""))
    return ToolResult(True, f"Fiche « {title} » ajoutée à la base de connaissances.", risk=SAFE_WRITE,
                      data={"id": item["id"]})


registry.add(
    id="knowledge.add", name="Ajouter une fiche", category="Mémoire",
    description="Ajoute une fiche durable (procédure, documentation, note) à la Knowledge Base.",
    handler=_knowledge_add, risk=SAFE_WRITE, permissions=("write",),
    input_schema={"type": "object", "properties": {
        "title": {"type": "string"}, "content": {"type": "string"},
        "kind": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}},
        "project": {"type": "string"}}, "required": ["title", "content"]},
)


def _knowledge_search(ctx: ToolContext) -> ToolResult:
    items = ctx.core.memory.knowledge_search(str(ctx.arguments.get("query") or ""),
                                             limit=int(ctx.arguments.get("limit") or 8))
    if not items:
        return ToolResult(True, "Aucune fiche correspondante.", data={"items": []})
    text = "\n\n".join(f"### {k['title']}\n{k['content'][:800]}" for k in items)
    return ToolResult(True, text, data={"items": items})


registry.add(
    id="knowledge.search", name="Chercher une fiche", category="Mémoire",
    description="Recherche dans la base de connaissances.",
    handler=_knowledge_search, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]},
)


# --- Tâches -----------------------------------------------------------------
def _task_list(ctx: ToolContext) -> ToolResult:
    status = str(ctx.arguments.get("status") or "")
    items = ctx.core.tasks.list(status=status, limit=int(ctx.arguments.get("limit") or 20))
    if not items:
        return ToolResult(True, "Aucune tâche.", data={"tasks": []})
    lines = [f"- [{t['status']}] {t['name']} ({int(t['progress'] * 100)}%)" for t in items]
    return ToolResult(True, "\n".join(lines), data={"tasks": items})


registry.add(
    id="task.list", name="Lister les tâches", category="Tâches",
    description="Liste les tâches JARVIS (queued, running, completed, failed…).",
    handler=_task_list, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "status": {"type": "string"}, "limit": {"type": "integer"}}, "required": []},
)


def _task_create(ctx: ToolContext) -> ToolResult:
    name = str(ctx.arguments.get("name") or "").strip()
    if not name:
        return ToolResult(False, "Nom de la tâche manquant.")
    task = ctx.core.tasks.create(name=name, kind="manual", agent=ctx.agent,
                                 meta={"note": str(ctx.arguments.get("note") or "")},
                                 conversation_id=ctx.conversation_id)
    return ToolResult(True, f"Tâche créée : {name}", risk=SAFE_WRITE, data={"id": task["id"]})


registry.add(
    id="task.create", name="Créer une tâche", category="Tâches",
    description="Crée une tâche de suivi visible dans le dashboard.",
    handler=_task_create, risk=SAFE_WRITE, permissions=("write",),
    input_schema={"type": "object", "properties": {
        "name": {"type": "string"}, "note": {"type": "string"}}, "required": ["name"]},
)


def _task_complete(ctx: ToolContext) -> ToolResult:
    tid = str(ctx.arguments.get("id") or "")
    ok = ctx.core.tasks.complete(tid, str(ctx.arguments.get("result") or "Terminé."))
    return ToolResult(ok, "Tâche marquée terminée." if ok else "Tâche introuvable.", risk=SAFE_WRITE)


registry.add(
    id="task.complete", name="Terminer une tâche", category="Tâches",
    description="Marque une tâche comme terminée.",
    handler=_task_complete, risk=SAFE_WRITE, permissions=("write",),
    input_schema={"type": "object", "properties": {
        "id": {"type": "string"}, "result": {"type": "string"}}, "required": ["id"]},
)


# --- Agenda -----------------------------------------------------------------
def _calendar_list(ctx: ToolContext) -> ToolResult:
    days = int(ctx.arguments.get("days") or 7)
    items = ctx.core.calendar.upcoming(days=days)
    if not items:
        return ToolResult(True, f"Aucun événement dans les {days} prochains jours.", data={"events": []})
    lines = [f"- {time.strftime('%d/%m %H:%M', time.localtime(e['start_at']))} — {e['title']}" for e in items]
    return ToolResult(True, "\n".join(lines), data={"events": items})


registry.add(
    id="calendar.list", name="Agenda", category="Productivité",
    description="Liste les prochains événements enregistrés dans JARVIS.",
    handler=_calendar_list, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {"days": {"type": "integer"}}, "required": []},
)


def _calendar_add(ctx: ToolContext) -> ToolResult:
    title = str(ctx.arguments.get("title") or "").strip()
    when = str(ctx.arguments.get("start") or "").strip()
    if not title or not when:
        return ToolResult(False, "Titre et date/heure requis (format ISO ou « demain 14h »).")
    ts = ctx.core.calendar.parse_when(when)
    if not ts:
        return ToolResult(False, f"Date non comprise: {when}")
    ev = ctx.core.calendar.add(title=title, start_at=ts,
                               description=str(ctx.arguments.get("description") or ""),
                               duration_min=int(ctx.arguments.get("duration_min") or 60))
    return ToolResult(True, f"Événement ajouté : {title} le "
                            f"{time.strftime('%d/%m à %H:%M', time.localtime(ts))}",
                      risk=SAFE_WRITE, data={"id": ev["id"]})


registry.add(
    id="calendar.add", name="Ajouter un événement", category="Productivité",
    description="Ajoute un événement à l'agenda JARVIS.",
    handler=_calendar_add, risk=SAFE_WRITE, permissions=("write",),
    input_schema={"type": "object", "properties": {
        "title": {"type": "string"}, "start": {"type": "string"},
        "duration_min": {"type": "integer"}, "description": {"type": "string"}},
        "required": ["title", "start"]},
)


# --- Automatisations --------------------------------------------------------
def _automation_create(ctx: ToolContext) -> ToolResult:
    name = str(ctx.arguments.get("name") or "").strip()
    instruction = str(ctx.arguments.get("instruction") or "").strip()
    if not name or not instruction:
        return ToolResult(False, "Nom et instruction requis.")
    trigger = ctx.arguments.get("trigger") or {}
    if isinstance(trigger, str):
        trigger = ctx.core.automations.parse_trigger(trigger)
    if not trigger:
        return ToolResult(False, "Déclencheur non compris. Précise par exemple « chaque matin à 8h ».")
    wf = ctx.core.automations.create(name=name, instruction=instruction, trigger=trigger,
                                     description=str(ctx.arguments.get("description") or ""))
    return ToolResult(True, f"Automatisation « {name} » créée ({ctx.core.automations.describe_trigger(trigger)}).",
                      risk=SAFE_WRITE, data={"id": wf["id"]})


registry.add(
    id="automation.create", name="Créer une automatisation", category="Automatisation",
    description="Crée une automatisation récurrente. trigger accepte un objet "
                "{type: cron|interval|daily|event|webhook, ...} ou une phrase « chaque matin à 8h ».",
    handler=_automation_create, risk=SAFE_WRITE, permissions=("write",),
    input_schema={"type": "object", "properties": {
        "name": {"type": "string"},
        "instruction": {"type": "string", "description": "Ce que JARVIS doit faire à chaque exécution"},
        "trigger": {"type": ["object", "string"]}, "description": {"type": "string"}},
        "required": ["name", "instruction", "trigger"]},
)


def _automation_list(ctx: ToolContext) -> ToolResult:
    items = ctx.core.automations.list()
    if not items:
        return ToolResult(True, "Aucune automatisation.", data={"workflows": []})
    lines = []
    for w in items:
        nxt = time.strftime("%d/%m %H:%M", time.localtime(w["next_run_at"])) if w.get("next_run_at") else "—"
        lines.append(f"- {w['name']} [{'actif' if w['enabled'] else 'inactif'}] "
                     f"{ctx.core.automations.describe_trigger(w['trigger'])} → prochaine: {nxt}")
    return ToolResult(True, "\n".join(lines), data={"workflows": items})


registry.add(
    id="automation.list", name="Lister les automatisations", category="Automatisation",
    description="Liste les automatisations et workflows JARVIS.",
    handler=_automation_list, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {}, "required": []},
)


def _automation_toggle(ctx: ToolContext) -> ToolResult:
    wid = str(ctx.arguments.get("id") or "")
    enabled = bool(ctx.arguments.get("enabled", True))
    ok = ctx.core.automations.set_enabled(wid, enabled)
    return ToolResult(ok, ("Automatisation activée." if enabled else "Automatisation désactivée.")
                      if ok else "Automatisation introuvable.", risk=SAFE_WRITE)


registry.add(
    id="automation.toggle", name="Activer/désactiver une automatisation", category="Automatisation",
    description="Active ou désactive une automatisation.",
    handler=_automation_toggle, risk=SAFE_WRITE, permissions=("write",),
    input_schema={"type": "object", "properties": {
        "id": {"type": "string"}, "enabled": {"type": "boolean"}}, "required": ["id", "enabled"]},
)


# --- Connecteurs ------------------------------------------------------------
def _connector_list(ctx: ToolContext) -> ToolResult:
    items = ctx.core.connectors.list()
    if not items:
        return ToolResult(True, "Aucun connecteur enregistré. Ouvre Settings → Connectors.",
                          data={"connectors": []})
    lines = [f"- {c['id']} — {c['name']} ({c['type']}) [{c['status']}]"
             f"{'' if c['enabled'] else ' (désactivé)'}" for c in items]
    return ToolResult(True, "\n".join(lines), data={"connectors": items})


registry.add(
    id="connector.list", name="Lister les connecteurs", category="Connecteurs",
    description="Liste les connecteurs enregistrés avec leur type, identifiant et état. "
                "Utilise ce connector_id pour appeler les autres outils.",
    handler=_connector_list, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {"type": {"type": "string"}}, "required": []},
)


def _connector_test(ctx: ToolContext) -> ToolResult:
    cid = str(ctx.arguments.get("id") or "").strip()
    conn = ctx.core.connectors.find(cid) if cid else None
    if not conn:
        return ToolResult(False, f"Connecteur « {cid} » introuvable.")
    ok, detail = ctx.core.connectors.test(conn["id"])
    return ToolResult(ok, f"{conn['name']}: {detail}")


registry.add(
    id="connector.test", name="Tester un connecteur", category="Connecteurs",
    description="Teste la connexion d'un connecteur enregistré.",
    handler=_connector_test, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
)


# --- Délégation à un agent --------------------------------------------------
def _delegate(ctx: ToolContext) -> ToolResult:
    agent_id = str(ctx.arguments.get("agent") or "").strip()
    instruction = str(ctx.arguments.get("instruction") or "").strip()
    if not agent_id or not instruction:
        return ToolResult(False, "Agent et instruction requis.")
    if agent_id not in ctx.core.agents.ids():
        return ToolResult(False, f"Agent inconnu: {agent_id}. Disponibles: {', '.join(ctx.core.agents.ids())}")
    result = ctx.core.orchestrator.run_agent(
        agent_id, instruction, task_id=ctx.task_id, conversation_id=ctx.conversation_id,
        parent_agent=ctx.agent,
    )
    return ToolResult(result.get("ok", True), result.get("output", ""), data=result)


registry.add(
    id="agent.delegate", name="Déléguer à un agent", category="Agents",
    description="Confie une sous-tâche à un agent spécialisé "
                "(coding, research, browser, system, memory, task, blender). "
                "blender est le SPÉCIALISTE 3D : toute tâche Blender, mesh, matériau, rig, animation, avatar ou export GLB doit lui être confiée.",
    handler=_delegate, risk=SAFE_WRITE, permissions=("execute",),
    agents=("jarvis",),
    input_schema={"type": "object", "properties": {
        "agent": {"type": "string", "enum": ["coding", "research", "browser", "system", "memory", "task", "blender"]},
        "instruction": {"type": "string"}}, "required": ["agent", "instruction"]},
)


def _brief(ctx: ToolContext) -> ToolResult:
    """Ce qui nécessite l'attention de Jérôme, à partir des données réelles."""
    core = ctx.core
    parts: list[str] = []
    tasks = core.tasks.list(limit=50)
    running = [t for t in tasks if t["status"] in {"running", "planning"}]
    waiting = [t for t in tasks if t["status"] == "waiting_confirmation"]
    failed = [t for t in tasks if t["status"] == "failed"]
    if waiting:
        parts.append(f"{len(waiting)} tâche(s) attendent ta confirmation : "
                     + ", ".join(t["name"][:50] for t in waiting[:3]))
    if failed:
        parts.append(f"{len(failed)} tâche(s) en échec : " + ", ".join(t["name"][:50] for t in failed[:3]))
    if running:
        parts.append(f"{len(running)} tâche(s) en cours.")
    events = core.calendar.upcoming(days=2)
    if events:
        nxt = events[0]
        parts.append(f"Prochain événement : {nxt['title']} le "
                     f"{time.strftime('%d/%m à %H:%M', time.localtime(nxt['start_at']))}.")
    broken = [c for c in core.connectors.list(include_disabled=False) if c["status"] == "error"]
    if broken:
        parts.append(f"{len(broken)} connecteur(s) en erreur : " + ", ".join(c["name"] for c in broken[:3]))
    metrics = core.monitor.snapshot()
    if metrics["cpu"]["percent"] and metrics["cpu"]["percent"] > 85:
        parts.append(f"CPU à {metrics['cpu']['percent']}%.")
    if metrics["disk"]["percent"] and metrics["disk"]["percent"] > 90:
        parts.append(f"Disque à {metrics['disk']['percent']}%.")
    unread = core.events.feed_items(limit=6, unread_only=True)
    important = [f for f in unread if f["level"] in {"warn", "error", "overdue"}]
    if important:
        parts.append("Alertes : " + " ; ".join(f["title"][:60] for f in important[:3]))
    if not parts:
        return ToolResult(True, "Rien de particulier. Tout est nominal.", data={"items": []})
    return ToolResult(True, " ".join(parts), data={"items": parts})


registry.add(
    id="jarvis.brief", name="Point de situation", category="Agents",
    description="Résume ce qui nécessite l'attention : tâches, confirmations, agenda, connecteurs, système, alertes.",
    handler=_brief, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {}, "required": []},
)
