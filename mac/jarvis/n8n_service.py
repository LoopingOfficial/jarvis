"""Compréhension des demandes n8n en langage naturel + exécution réelle.

« Liste mes workflows », « montre seulement les actifs », « quel workflow gère
les emails ? », « lance X », « désactive X »… Le contexte de conversation est
conservé 15 min pour les relances sans le mot « workflow ».
Lecture : autonome. Lancer / activer / désactiver : confirmation obligatoire.
"""
from __future__ import annotations

import re
import time
import uuid
from typing import Any

from .n8n_connector import N8nConnector, N8nError

_N8N_WORD = re.compile(r"\b(n8n|workflows?)\b", re.I)
_LIST = re.compile(r"\b(liste|lister|list|montre|affiche|quels?|quelles?|combien|donne|voir|"
                   r"poss[eè]de|ai[- ]je|j'ai|mes)\b", re.I)
_ACTIVE = re.compile(r"\b(actifs?|activ[ée]s?)\b", re.I)
_INACTIVE = re.compile(r"\b(inactifs?|d[ée]sactiv[ée]s?)\b", re.I)
_RUN = re.compile(r"^\s*(?:peux[- ]tu\s+)?(lance|lancer|ex[ée]cute|ex[ée]cuter|d[ée]clenche|run)\b\s*(?:le\s+)?"
                  r"(?:workflow\s+)?(.+)$", re.I)
_ACTIVATE = re.compile(r"^\s*(?:peux[- ]tu\s+)?(r?active|activer|d[ée]sactive|d[ée]sactiver)\b\s*(?:le\s+)?"
                       r"(?:workflow\s+)?(.+)$", re.I)
_LAST_RUN = re.compile(r"(quand|derni[eè]re?).{0,40}(tourn|ex[ée]cut|lanc)", re.I)
_SEARCH = re.compile(r"\b(g[eè]re|s'occupe|concerne|parle|pour les?|qui fait|lequel|lesquels|cherche|trouve)\b", re.I)
_CONTEXT_TTL = 900


class N8nService:
    def __init__(self, core) -> None:
        self._core = core
        self.connector = N8nConnector(core)
        self._context: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, dict[str, Any]] = {}

    # -- détection ---------------------------------------------------------
    def detect(self, text: str, conversation_id: str = "") -> dict[str, Any] | None:
        t = (text or "").strip()
        ctx = self._context.get(conversation_id or "")
        in_ctx = bool(ctx and time.time() - ctx["at"] < _CONTEXT_TTL)
        mentions = bool(_N8N_WORD.search(t))
        if not mentions and not in_ctx:
            return None
        m = _ACTIVATE.match(t)
        if m and (mentions or in_ctx):
            return {"action": "deactivate" if m.group(1).lower().startswith("d") else "activate",
                    "workflow": self._clean_ref(m.group(2))}
        m = _RUN.match(t)
        if m and (mentions or in_ctx):
            return {"action": "run", "workflow": self._clean_ref(m.group(2))}
        if _LAST_RUN.search(t):
            ref = re.sub(r"(?i)^.*?\b(quand|derni[eè]re fois que?)\b\s*", "", t)
            ref = re.sub(r"(?i)\s*(a[- ]t[- ]il|a[- ]t[- ]elle)?\s*(tourn|ex[ée]cut|lanc).*$", "", ref)
            return {"action": "last_run", "workflow": self._clean_ref(ref)}
        if _SEARCH.search(t) and mentions:
            return {"action": "search", "query": t}
        flt = "active" if _ACTIVE.search(t) and not _INACTIVE.search(t) else (
            "inactive" if _INACTIVE.search(t) else "")
        if mentions and _LIST.search(t):
            return {"action": "list", "filter": flt}
        if in_ctx and flt and re.search(r"\b(seulement|uniquement|juste|que|ceux|celles|montre|affiche)\b", t, re.I):
            return {"action": "list", "filter": flt}
        if mentions and re.search(r"\b(n8n)\b", t, re.I) and re.search(r"\b(statut|[ée]tat|connect)", t, re.I):
            return {"action": "status"}
        return None

    @staticmethod
    def _clean_ref(ref: str) -> str:
        ref = re.sub(r"(?i)\b(n8n|workflow|le|la|s'il te pla[iî]t|stp|maintenant)\b", " ", ref or "")
        return re.sub(r"\s+", " ", ref).strip(" ?.!«»\"'")

    # -- exécution ---------------------------------------------------------
    def handle(self, text: str, conversation_id: str, request: dict[str, Any]) -> dict[str, Any]:
        core = self._core
        task = core.tasks.create(name=text[:200], kind="n8n", agent="jarvis", conversation_id=conversation_id,
                                 meta={"n8n": request})
        tid = task["id"]
        core.velko_tasks.running(tid, "Connexion à n8n")
        core.events.emit("tool.started", {"tool": "n8n.workflows." + request["action"], "task_id": tid})
        started = time.time()
        try:
            response, data, confirm = self._dispatch(request, tid)
        except N8nError as exc:
            core.events.emit("tool.failed", {"tool": "n8n." + request["action"], "error": str(exc)[:200]})
            recovery = core.recovery.build(exc.category, connector="n8n", detail=str(exc),
                                           http_status=exc.status, task_id=tid, text=text)
            core.velko_tasks.blocked(tid, recovery["cause"])
            self._remember(conversation_id)
            core.conversations.add_message(conversation_id, "assistant", recovery["message"],
                                           meta={"task_id": tid, "recovery": recovery["category"]})
            return {"ok": True, "response": recovery["message"], "task_id": tid, "status": "blocked",
                    "blocked": True, "recovery": recovery, "conversation_id": conversation_id,
                    "tools_used": [f"n8n.{request['action']}"]}
        core.events.emit("tool.completed", {"tool": "n8n." + request["action"], "ok": True,
                                            "preview": response[:200],
                                            "duration_ms": int((time.time() - started) * 1000)})
        self._remember(conversation_id)
        if confirm:
            core.velko_tasks.waiting_user(tid, f"Action bloquée : {confirm['action']}", data=confirm)
            return {"ok": True, "response": response, "task_id": tid, "needs_confirmation": confirm,
                    "conversation_id": conversation_id, "tools_used": ["n8n.workflows.get"], "n8n": data}
        core.velko_tasks.complete(tid, response)
        core.conversations.add_message(conversation_id, "assistant", response,
                                       meta={"task_id": tid, "source": "n8n", "grounded": True})
        return {"ok": True, "response": response, "task_id": tid, "status": "completed",
                "conversation_id": conversation_id, "tools_used": [f"n8n.workflows.{request['action']}"],
                "n8n": data}

    def _remember(self, conversation_id: str) -> None:
        self._context[conversation_id or ""] = {"at": time.time()}

    def _dispatch(self, req: dict[str, Any], tid: str):
        n8n, action = self.connector, req["action"]
        if action == "status":
            s = n8n.status()
            if s["state"] not in ("CONNECTED", "DEGRADED"):
                raise N8nError(s.get("detail", "n8n indisponible"), s.get("category", "EXTERNAL_SERVICE_ERROR"))
            return (f"n8n connecté ({s['instance']}) · {s['workflows']} workflows, {s['active']} actifs · "
                    f"latence {s['latency_ms']} ms.", s, None)
        if action == "list":
            rows = n8n.list_workflows()
            flt = req.get("filter") or ""
            shown = [w for w in rows if not flt or w["active"] == (flt == "active")]
            title = {"active": "actifs", "inactive": "inactifs"}.get(flt, "")
            head = f"## N8N — {len(shown)} WORKFLOWS{(' ' + title.upper()) if title else ''}"
            if not rows:
                return ("Votre instance n8n ne contient aucun workflow.", {"workflows": []}, None)
            lines = [f"- {'●' if w['active'] else '○'} **{w['name']}** — {'Actif' if w['active'] else 'Inactif'}"
                     for w in shown]
            summary = f"{sum(w['active'] for w in rows)} actifs / {len(rows)} au total."
            return (head + "\n\n" + "\n".join(lines) + "\n\n" + summary,
                    {"workflows": shown, "filter": flt, "total": len(rows)}, None)
        if action == "search":
            hits = n8n.search(req["query"])
            if not hits:
                return ("Aucun workflow ne correspond à cette recherche (noms, tags, nodes, description).",
                        {"workflows": []}, None)
            lines = [f"- {'●' if w['active'] else '○'} **{w['name']}** — {w.get('description') or ', '.join(w['nodes'][:5])}"
                     [:220] for w in hits[:8]]
            return (f"{len(hits)} workflow(s) pertinent(s) :\n\n" + "\n".join(lines), {"workflows": hits[:8]}, None)
        if action == "last_run":
            w = n8n.resolve(req.get("workflow") or "")
            runs = n8n.executions(w["workflow_id"], limit=1)
            if not runs:
                return (f"« {w['name']} » n'a aucune exécution enregistrée dans n8n.", {"workflow": w}, None)
            r = runs[0]
            return (f"Dernière exécution de « {w['name']} » : {r['started_at']} — statut {r['status']} "
                    f"(mode {r['mode']}).", {"workflow": w, "executions": runs}, None)
        if action in ("run", "activate", "deactivate"):
            w = n8n.get_workflow(req.get("workflow") or "")
            if action == "run" and not any(t.get("kind") == "webhook" for t in w["triggers"]):
                raise N8nError(f"« {w['name']} » n'a pas de déclencheur Webhook : l'API publique n8n ne permet "
                               "pas de le lancer directement. Ouvrez-le dans n8n ou ajoutez-lui un Webhook.",
                               "TOOL_UNAVAILABLE")
            return self._ask_confirmation(action, w, tid)
        raise N8nError(f"Action n8n inconnue : {action}", "TOOL_UNAVAILABLE")

    # -- confirmations -----------------------------------------------------
    def _ask_confirmation(self, action: str, w: dict[str, Any], tid: str):
        cid = "n8nc_" + uuid.uuid4().hex[:12]
        verb = {"run": "Lancer", "activate": "Activer", "deactivate": "Désactiver"}[action]
        writes = [n for n in w.get("nodes", []) if re.search(
            r"(postgres|mysql|sheets|gmail|email|discord|http|slack|write|insert|update|delete)", n, re.I)]
        impact = ("Ce workflow contient des nodes capables d'écrire : " + ", ".join(writes[:6])) if writes \
            else "Aucun node d'écriture détecté."
        confirm = {"id": cid, "action": f"{verb} le workflow « {w['name']} »", "tool": f"n8n.workflow.{action}",
                   "reason": impact, "risk": "sensitive" if (writes or action != "run") else "safe_write",
                   "workflow": {"id": w["workflow_id"], "name": w["name"], "active": w["active"]}}
        self._pending[cid] = {"action": action, "workflow_id": w["workflow_id"], "task_id": tid,
                              "created": time.time()}
        return (f"{verb.upper()} LE WORKFLOW\n\n{w['name']}\n\n{impact}\n\nConfirmez-vous ?", {"workflow": w}, confirm)

    def request_action(self, workflow_id: str, action: str) -> dict[str, Any]:
        """Depuis l'UI (bouton Lancer/Activer) : même chemin confirmé que la voix."""
        if action not in ("run", "activate", "deactivate"):
            raise ValueError("Action inconnue.")
        return self.handle(f"{action} workflow {workflow_id}", "",
                           {"action": action, "workflow": workflow_id})

    def is_pending(self, confirmation_id: str) -> bool:
        return confirmation_id in self._pending

    def confirm(self, confirmation_id: str, approved: bool) -> dict[str, Any]:
        core = self._core
        p = self._pending.pop(confirmation_id, None)
        if not p or time.time() - p["created"] > 600:
            return {"ok": False, "response": "Confirmation expirée ou déjà utilisée."}
        tid = p["task_id"]
        if not approved:
            core.tasks.cancel(tid)
            return {"ok": True, "response": "Action n8n annulée. Rien n'a été modifié.", "task_id": tid,
                    "status": "cancelled"}
        core.velko_tasks.running(tid, "Exécution n8n confirmée")
        try:
            if p["action"] == "run":
                out = self.connector.run(p["workflow_id"])
                text = f"Workflow « {out['workflow']['name']} » déclenché. Réponse n8n reçue."
            else:
                w = self.connector.set_active(p["workflow_id"], p["action"] == "activate")
                text = f"Workflow « {w['name']} » {'activé' if w['active'] else 'désactivé'}."
        except N8nError as exc:
            recovery = core.recovery.build(exc.category, connector="n8n", detail=str(exc), task_id=tid)
            core.velko_tasks.blocked(tid, recovery["cause"])
            return {"ok": True, "response": recovery["message"], "task_id": tid, "status": "blocked",
                    "blocked": True, "recovery": recovery}
        core.audit.record(action=text, tool="n8n", connector_id="n8n", status="ok")
        core.velko_tasks.complete(tid, text)
        return {"ok": True, "response": text, "task_id": tid, "status": "completed"}
