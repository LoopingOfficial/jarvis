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

import re
import time
import uuid
import posixpath
from pathlib import Path
from hashlib import sha256
from typing import Any

from ..permissions import DESTRUCTIVE, READ_ONLY, RISK_LABELS, SENSITIVE
from ..security_analysis import READ_ONLY_ALLOWED_TOOLS
from ..execution_policy import current_policy, is_readonly, policy_scope
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
            found = core.connectors.raw(requested)
            if found and found["type"] == tool.connector_type and found.get("enabled"):
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
        task = core.tasks.get(task_id) if task_id else None
        saved = (task or {}).get("meta", {}).get("execution_policy", {})
        policies = [saved, current_policy(), execution_policy or {}]
        policy = next((dict(p) for p in policies if is_readonly(p)),
                      dict(saved or current_policy() or execution_policy or {}))
        # Mode MODEL_ONLY : la politique interdit TOUT appel d'outil, même en
        # lecture. Un benchmark, une citation ou une phrase « n'utilise aucun
        # outil » ne doivent jamais traverser le ToolRunner.
        if any(p.get("tools_allowed") is False for p in policies if p):
            reason = "TOOL_DENIED_BY_EXECUTION_POLICY"
            core.audit.record(action=f"{tool_id} refusé (modèle seul)", tool=tool_id,
                              status="denied", agent=agent, task_id=task_id, detail=reason)
            core.events.emit("tool.denied", {"tool": tool_id, "reason": reason,
                                             "agent": agent, "task_id": task_id,
                                             "tool_policy": "DENY"})
            return ToolResult(False, reason)
        read_only = is_readonly(policy)
        if tool_id in {"ssh.write_file", "fs.write"}:
            target = str(arguments.get("path") or "")
            cid = str(arguments.get("connector_id") or arguments.get("connector") or "")
            if tool_id == "ssh.write_file":
                if not target.startswith("/"):
                    candidates = core.connectors.routing_candidates("ssh")
                    selected = core.connectors.raw(cid) if cid else (
                        candidates[0] if len(candidates) == 1 else {})
                    cfg = (selected or {}).get("config") or {}
                    root = cfg.get("working_directory") or cfg.get("deployment_path") or cfg.get("remote_path")
                    if root:
                        target = posixpath.join(root, target)
                target = posixpath.normpath(target)
            elif target:
                target = str(Path(target).expanduser().resolve())
            for doc in core.documents.documents.values():
                if (doc.read_only and target == doc.absolute_path
                        and (not cid or cid == doc.connector_id)):
                    read_only = True
        # Fail closed: even a shell command misclassified as READ_ONLY cannot
        # escape the allowlist. This runs before connector/secrets/confirmation.
        if read_only and tool_id not in READ_ONLY_ALLOWED_TOOLS:
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

        if read_only and risk != READ_ONLY:
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
        if not confirmed and (core.permissions.requires_confirmation(risk, tool.confirmation_policy)
                              or policy.get("require_write_confirmation") and risk != READ_ONLY):
            resolved = core.permissions.get(confirmation_id) if confirmation_id else None
            if (resolved and resolved.resolved and resolved.approved
                    and resolved.tool == tool_id and resolved.arguments == arguments
                    and resolved.task_id == task_id):
                confirmed = True
            else:
                readable = self._readable_arguments(arguments)
                action_desc = self._describe(tool, readable, connector)
                speech_desc = self._describe_speech(tool, readable, connector)
                reason = tool.dangerous_hint or (
                    "Cette action est irréversible." if risk == DESTRUCTIVE else "Cette action modifie un système."
                )
                pending = core.permissions.create_pending(
                    tool=tool_id, action=action_desc, risk=risk, reason=reason,
                    arguments=arguments, task_id=task_id, speech=speech_desc,
                )
                core.events.emit("task.waiting_confirmation", {
                    "confirmation_id": pending.id, "tool": tool_id, "action": action_desc,
                    "risk": risk, "risk_label": RISK_LABELS.get(risk, risk), "reason": reason,
                    "speech": speech_desc, "task_id": task_id,
                })
                core.audit.record(action=f"Confirmation demandée: {action_desc}", tool=tool_id, agent=agent,
                                  connector_id=(connector or {}).get("id", ""), status="pending", task_id=task_id)
                raise ConfirmationRequired(pending, f"{action_desc}\n{reason} Confirmer ?")

        # Revalidate the exact version approved by the user at confirmation time.
        if tool_id in {"ssh.write_file", "fs.write"} and arguments.get("expected_sha256"):
            read_args = {"path": arguments.get("path")}
            if arguments.get("connector_id"):
                read_args["connector_id"] = arguments["connector_id"]
            checked = self.run("ssh.read_file" if tool_id.startswith("ssh.") else "fs.read",
                               read_args, agent=agent, task_id=task_id,
                               conversation_id=conversation_id, execution_policy=policy)
            content = (checked.data or {}).get("content") if checked.ok else None
            if (not isinstance(content, str) or
                    sha256(content.encode("utf-8")).hexdigest() != arguments["expected_sha256"]):
                return ToolResult(False, "SOURCE_CHANGED: écriture annulée, recharge le fichier.")

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
            with policy_scope(policy):
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


    # Un identifiant interne (« ct_sophie_renard ») n'a aucun sens dans une
    # question de confirmation, et encore moins lu à voix haute. On le remplace
    # par le nom réel quand on peut le résoudre ; sinon on le retire, plutôt
    # que de faire énoncer une chaîne technique.
    _RE_INTERNAL_ID = re.compile(r"^[a-z]{2,8}_[a-z0-9_]{3,}$")

    def _readable_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        out = dict(arguments)
        for key in ("contact", "client", "contact_id"):
            value = out.get(key)
            if not isinstance(value, str) or not self._RE_INTERNAL_ID.match(value):
                continue
            label = ""
            try:
                store = getattr(self._core, "crm", None)
                record = store.get(value) if store else None
                if record:
                    label = str(record.get("name") or "").strip()
                    company = str(record.get("company") or "").strip()
                    if company:
                        label = f"{label} ({company})" if label else company
            except Exception:
                label = ""
            if label:
                out[key] = label
            else:
                out.pop(key, None)
        return out

    @staticmethod
    def _describe(tool: Tool, arguments: dict[str, Any], connector: dict[str, Any] | None) -> str:
        bits = [tool.name]
        if connector:
            bits.append(f"→ {connector['name']}")
        for key in ("command", "path", "query", "url", "workflow", "message", "task", "name",
                    "contact", "to"):
            if arguments.get(key):
                bits.append(f": {str(arguments[key])[:160]}")
                break
        return " ".join(bits)[:400]

    # Cibles lisibles à l'oral, par ordre de préférence. On ne dicte jamais un
    # corps de message, une commande shell ou un chemin absolu : seul un
    # destinataire ou un nom court a du sens à voix haute.
    # « à Pierre » pour un destinataire ; « pour facture.pdf » pour un objet.
    _SPEECH_RECIPIENT_KEYS = ("to", "recipient", "contact")
    _SPEECH_OBJECT_KEYS = ("name", "workflow", "task")

    @staticmethod
    def _describe_speech(tool: Tool, arguments: dict[str, Any], connector: dict[str, Any] | None) -> str:
        """Question de confirmation courte et directe, destinée au TTS."""

        def pick(keys):
            for key in keys:
                value = arguments.get(key)
                if (value and isinstance(value, str) and len(value) <= 60
                        and "\n" not in value and not value.startswith(("http", "/", "\\"))):
                    return value.strip()
            return ""

        phrase = tool.name
        recipient = pick(SecureToolRunner._SPEECH_RECIPIENT_KEYS)
        obj = pick(SecureToolRunner._SPEECH_OBJECT_KEYS)
        if recipient:
            phrase += f" à {recipient}"
        elif obj:
            phrase += f" pour {obj}"
        elif connector:
            phrase += f" via {connector['name']}"
        return f"{phrase}. Tu confirmes ?"[:180]
