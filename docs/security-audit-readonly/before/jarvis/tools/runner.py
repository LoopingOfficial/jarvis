"""Secure Tool Runner.

Chaîne d'exécution :

    LLM / Agent
       ↓  (nom d'outil + arguments + connector_id)
    Tool Router  → vérifie l'outil, résout le connecteur
       ↓
    Permission Manager → niveau de risque, confirmation si nécessaire
       ↓
    Secure Tool Runner → déchiffre les secrets JUSTE ICI
       ↓
    Service externe

Le LLM ne voit jamais un secret : il ne manipule qu'un `connector_id`.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from ..permissions import DESTRUCTIVE, READ_ONLY, RISK_LABELS, SENSITIVE
from ..security_analysis import READ_ONLY_DENIED_TOOLS
from .base import Tool, ToolContext, ToolResult, registry


class ToolDenied(Exception):
    pass


class ConfirmationRequired(Exception):
    def __init__(self, pending, message: str) -> None:
        super().__init__(message)
        self.pending = pending
        self.message = message


class SecureToolRunner:
    def __init__(self, core) -> None:
        self._core = core

    # -- résolution du connecteur -----------------------------------------
    def _resolve_connector(self, tool: Tool, arguments: dict[str, Any]) -> dict[str, Any] | None:
        if not tool.connector_type:
            return None
        core = self._core
        requested = str(arguments.get("connector_id") or arguments.get("connector") or "").strip()
        if requested:
            found = core.connectors.resolve_connector(tool.connector_type, {"connector_id": requested})
            if found:
                return found
            found = core.connectors.raw(requested)
            if found and found["type"] == tool.connector_type and found.get("enabled") and found.get("status") == "connected":
                return found
            if not tool.connector_optional:
                raise ToolDenied(
                    f"Aucun connecteur « {requested} » de type {tool.connector_type}. "
                    f"Ajoute-le dans Settings → Connectors."
                )
            return None
        available = core.connectors.active(tool.connector_type)
        if len(available) == 1:
            return core.connectors.raw(available[0]["id"])
        if not available:
            if tool.connector_optional:
                return None
            raise ToolDenied(
                f"Aucun connecteur {tool.connector_type} n'est configuré. "
                f"Ouvre Settings → Connectors pour en ajouter un."
            )
        names = ", ".join(f"{c['id']} ({c['name']})" for c in available[:6])
        raise ToolDenied(f"Plusieurs connecteurs {tool.connector_type} disponibles, précise connector_id : {names}")

    # -- exécution ---------------------------------------------------------
    def run(
        self,
        tool_id: str,
        arguments: dict[str, Any] | None = None,
        *,
        agent: str = "jarvis",
        task_id: str = "",
        conversation_id: str = "",
        confirmed: bool = False,
        confirmation_id: str = "",
        execution_policy: dict[str, Any] | None = None,
    ) -> ToolResult:
        core = self._core
        arguments = dict(arguments or {})
        # La politique est un garde-fou backend, pas une simple instruction au
        # modèle. Le contexte actif sert de filet de sécurité pour les appels
        # déterministes qui ne transportent pas explicitement la politique.
        policy = dict(execution_policy or {})
        if not policy:
            active = getattr(core, "active_task_context", {}) or {}
            policy = {"read_only": bool(active.get("read_only")),
                      "write_allowed": active.get("write_allowed", True)}
        read_only = bool(policy.get("read_only"))
        write_allowed = bool(policy.get("write_allowed", not read_only))
        if read_only and not write_allowed and (
                tool_id in READ_ONLY_DENIED_TOOLS or
                tool_id.startswith(("write", "replace", "delete", "deploy"))):
            reason = "WRITE_DENIED_READ_ONLY"
            core.audit.record(action=f"{tool_id} refusé (lecture seule)", tool=tool_id,
                              status="denied", agent=agent, task_id=task_id, detail=reason)
            core.events.emit("tool.denied", {"tool": tool_id, "reason": reason,
                                               "agent": agent, "task_id": task_id,
                                               "write_policy": "DENY"})
            return ToolResult(False, reason)
        tool = registry.get(tool_id)
        if tool is None:
            return ToolResult(False, f"Outil inconnu: {tool_id}")
        if not tool.enabled:
            return ToolResult(False, f"L'outil {tool.name} est désactivé dans les réglages.")
        if tool.agents and agent not in tool.agents:
            return ToolResult(False, f"L'agent {agent} n'a pas accès à l'outil {tool.name}.")

        started = time.time()
        try:
            connector = self._resolve_connector(tool, arguments)
        except ToolDenied as exc:
            core.events.emit("tool.denied", {"tool": tool_id, "reason": str(exc), "agent": agent, "task_id": task_id})
            return ToolResult(False, str(exc))

        risk = tool.resolve_risk(arguments)

        if read_only and not write_allowed and risk != READ_ONLY:
            reason = "WRITE_DENIED_READ_ONLY"
            core.audit.record(action=f"{tool_id} refusé (lecture seule)", tool=tool_id,
                              connector_id=(connector or {}).get("id", ""), status="denied",
                              agent=agent, task_id=task_id, detail=reason)
            core.events.emit("tool.denied", {"tool": tool_id, "reason": reason,
                                               "agent": agent, "task_id": task_id,
                                               "write_policy": "DENY"})
            return ToolResult(False, reason, risk=risk)

        # Permissions du connecteur
        if connector:
            allowed, reason = core.permissions.connector_allows(connector.get("permissions") or [], risk)
            if not allowed:
                core.audit.record(action=f"{tool.name} refusé ({RISK_LABELS.get(risk, risk)})", tool=tool_id,
                                  connector_id=connector["id"], status="denied", agent=agent, task_id=task_id,
                                  detail=reason)
                core.events.emit("tool.denied", {"tool": tool_id, "reason": reason, "agent": agent})
                return ToolResult(False, reason, risk=risk)

        # Confirmation utilisateur si nécessaire
        if not confirmed and core.permissions.requires_confirmation(risk, tool.confirmation_policy):
            resolved = core.permissions.get(confirmation_id) if confirmation_id else None
            if resolved and resolved.resolved and resolved.approved:
                confirmed = True
            else:
                action_desc = self._describe(tool, arguments, connector)
                reason = tool.dangerous_hint or (
                    "Cette action est irréversible." if risk == DESTRUCTIVE else "Cette action modifie un système."
                )
                pending = core.permissions.create_pending(
                    tool=tool_id, action=action_desc, risk=risk, reason=reason,
                    arguments=arguments, task_id=task_id,
                )
                core.events.emit("task.waiting_confirmation", {
                    "confirmation_id": pending.id, "tool": tool_id, "action": action_desc,
                    "risk": risk, "risk_label": RISK_LABELS.get(risk, risk), "reason": reason, "task_id": task_id,
                })
                core.audit.record(action=f"Confirmation demandée: {action_desc}", tool=tool_id, agent=agent,
                                  connector_id=(connector or {}).get("id", ""), status="pending", task_id=task_id)
                raise ConfirmationRequired(pending, f"{action_desc}\n{reason} Confirmer ?")

        ctx = ToolContext(core=core, connector=connector, task_id=task_id, agent=agent,
                          conversation_id=conversation_id, arguments=arguments,
                          execution_policy=policy)
        core.events.emit("tool.called", {
            "tool": tool_id, "name": tool.name, "agent": agent, "task_id": task_id,
            "connector_id": (connector or {}).get("id", ""), "risk": risk,
            # Le chemin permet à l'UI Coding d'afficher « Lecture de index.php… »
            # puis de REMPLACER cette ligne par le résultat, au lieu d'empiler
            # « Lire un fichier » + « Lire un fichier ✓ ».
            "path": str(arguments.get("path") or ""),
        })
        if task_id:
            core.tasks.add_tool(task_id, tool_id)

        try:
            result = tool.handler(ctx)
            if not isinstance(result, ToolResult):
                result = ToolResult(True, str(result))
        except ConfirmationRequired:
            raise
        except Exception as exc:
            result = ToolResult(False, f"{tool.name}: {exc}")

        result.risk = result.risk if result.risk != READ_ONLY else risk
        result.output = core.vault.scrub(result.output or "")
        duration = int((time.time() - started) * 1000)

        core.audit.record(
            action=self._describe(tool, arguments, connector), tool=tool_id, agent=agent,
            connector_id=(connector or {}).get("id", ""), status="ok" if result.ok else "error",
            duration_ms=duration, task_id=task_id, detail=result.output[:1500],
        )
        core.events.emit("tool.completed" if result.ok else "tool.failed", {
            # « tool_id » = identité canonique (obligatoire) ; « tool » et
            # « name » restent fournis pour compatibilité avec les consommateurs
            # historiques. L'enregistrement d'usage n'utilise QUE tool_id.
            "tool_id": tool_id, "tool": tool_id, "name": tool.name,
            "execution_id": uuid.uuid4().hex,
            "agent": agent, "task_id": task_id,
            "ok": result.ok, "duration_ms": duration, "preview": result.output[:200],
            "error": "" if result.ok else str(result.output or tool.name)[:300],
        })
        if connector and result.ok:
            core.db.execute("UPDATE connectors SET status='connected', last_connected_at=? WHERE id=?",
                            (time.time(), connector["id"]))
        # Un échec d'OUTIL (fichier absent, commande refusée…) n'est PAS une
        # panne de connexion : on ne marque PAS le connecteur 'error', sinon la
        # prochaine requête contourne tout le routage déterministe et le modèle
        # peut invoquer des chemins génériques (/var/www/html). Le statut de
        # connexion n'est modifié que par un vrai test de connectivité.
        return result

    @staticmethod
    def _describe(tool: Tool, arguments: dict[str, Any], connector: dict[str, Any] | None) -> str:
        bits = [tool.name]
        if connector:
            bits.append(f"→ {connector['name']}")
        for key in ("command", "path", "query", "url", "workflow", "message", "task", "name"):
            if arguments.get(key):
                bits.append(f": {str(arguments[key])[:160]}")
                break
        return " ".join(bits)[:400]
