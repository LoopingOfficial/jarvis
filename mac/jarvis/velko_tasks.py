"""VelkoTaskManager — cycle de vie réel des missions de VELKO.

Contrat strict : `task.completed` n'est émis QUE quand le travail demandé est
réellement terminé. Ce gestionnaire ne produit ni timer ni progression
décorative : chaque transition d'état est déclenchée par un fait du moteur
(planification, exécution d'outil, attente de confirmation / d'utilisateur,
phase de test, nouvelle tentative après échec, blocage justifié).

    queued → planning → running ──┬─→ waiting_tool (outil verrouillé/attente)
                                  │
                                  ├─→ waiting_user (confirmation requise)
                                  │
                                  ├─→ testing     (vérification du travail)
                                  │
                                  ├─→ retrying    (reprise après échec d'outil)
                                  │
                                  ├─→ blocked     (ACTION BLOQUÉE + raison réelle)
                                  │
                                  └─→ completed   (travail réellement fini)
                                     / failed
"""
from __future__ import annotations

import threading
import time
from typing import Any

CLOSED_PHASES = {"completed", "failed", "cancelled"}
DESK_PHASES = {"queued", "planning", "running", "waiting_tool", "waiting_user",
               "waiting_confirmation", "testing", "retrying", "blocked"}


class VelkoTaskManager:
    """Supervisions des tâches menées au nom de VELKO.

    La persistance reste celle du TaskManager canonique (`core.tasks`) ; ce
    gestionnaire ajoute le vocabulaire de phase (`waiting_tool`, `testing`,
    `blocked`…), lève les événements `velko.task.*` que l'interface consomme,
    et tient un journal de mission limité en mémoire — uniquement nourri par
    des événements réels du moteur (outils, terminal, fichiers).
    """

    def __init__(self, core) -> None:
        self._core = core
        self._lock = threading.RLock()
        # journal de mission par tâche : entrées réelles seulement.
        self._journals: dict[str, list[dict[str, Any]]] = {}
        self._max_entries = 400
        core.events.on("tool.completed", self._on_tool_completed)
        core.events.on("tool.failed", self._on_tool_failed)
        core.events.on("terminal.command", self._on_terminal_command)
        core.events.on("terminal.completed", self._on_terminal_completed)
        core.events.on("file.created", self._on_file_event)
        core.events.on("file.changed", self._on_file_event)
        core.events.on("file.deleted", self._on_file_event)
        core.events.on("file.opened", self._on_file_opened)

    # -- transitions de phase -------------------------------------------------
    def phase(self, task_id: str, phase: str, label: str = "", data: Any = None) -> None:
        """Passe la tâche à une phase réelle et diffuse l'événement."""
        core = self._core
        core.tasks.set_status(task_id, phase)
        core.tasks.log(task_id, label or phase, level="phase" if label else "info", data=data)
        core.events.emit("velko.task.phase", {
            "task_id": task_id, "phase": phase, "label": label, "ts": time.time(),
            "desk": phase in DESK_PHASES,
        })

    def planning(self, task_id: str, label: str = "Planification de la mission") -> None:
        self.phase(task_id, "planning", label)

    def running(self, task_id: str, label: str = "Exécution en cours") -> None:
        self.phase(task_id, "running", label)

    def waiting_tool(self, task_id: str, label: str = "Outil verrouillé", data: Any = None) -> None:
        self.phase(task_id, "waiting_tool", label, data=data)

    def waiting_user(self, task_id: str, label: str = "Action bloquée : validation requise", data: Any = None) -> None:
        self.phase(task_id, "waiting_user", label, data=data)

    def testing(self, task_id: str, label: str = "Vérification du travail") -> None:
        self.phase(task_id, "testing", label)
        core = self._core
        core.events.emit("velko.task.desk", {"task_id": task_id, "reason": "testing", "ts": time.time()})

    def retrying(self, task_id: str, label: str = "Nouvelle tentative après échec", data: Any = None) -> None:
        self.phase(task_id, "retrying", label, data=data)

    def blocked(self, task_id: str, reason: str) -> None:
        """ACTION BLOQUÉE : VELKO s'arrête, informe, et N'IMPROVISE PAS."""
        core = self._core
        core.tasks.set_status(task_id, "blocked", error=reason)
        core.tasks.log(task_id, f"ACTION BLOQUÉE : {reason}", level="error")
        core.events.emit("velko.task.blocked", {"task_id": task_id, "reason": reason, "ts": time.time()})
        core.events.emit("velko.task.phase", {
            "task_id": task_id, "phase": "blocked", "label": reason, "ts": time.time(), "desk": True,
        })

    def complete(self, task_id: str, result: str, journal: list[dict[str, Any]] | None = None) -> bool:
        """Clôture réelle : appelé uniquement quand le travail est terminé."""
        task = self._core.tasks.get(task_id)
        if not task or task["status"] in {"blocked", "waiting_user", "waiting_confirmation", "failed", "cancelled"}:
            return False
        ok = self._core.tasks.complete(task_id, result)
        if ok:
            self._core.events.emit("velko.task.desk", {
                "task_id": task_id, "reason": "completed", "ts": time.time()})
        return ok

    def fail(self, task_id: str, error: str) -> bool:
        return self._core.tasks.fail(task_id, error)

    # -- journal de mission (faits réels uniquement) --------------------------
    def journal(self, task_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._journals.get(task_id, []))

    def _append(self, task_id: str, entry: dict[str, Any]) -> None:
        if not task_id:
            return
        with self._lock:
            entries = self._journals.setdefault(task_id, [])
            entries.append(entry)
            if len(entries) > self._max_entries:
                del entries[: len(entries) - self._max_entries]

    def _on_tool_completed(self, event: dict[str, Any]) -> None:
        d = event.get("data") or {}
        tid = d.get("task_id") or ""
        if not tid:
            return
        tool_id = d.get("tool_id") or d.get("tool") or ""
        entry = {"ts": time.time(), "kind": "tool", "tool": tool_id,
                 "ok": bool(d.get("ok")), "preview": str(d.get("preview") or "")[:300]}
        self._append(tid, entry)
        self._emit_activity(tid, "tool", entry)

    def _on_tool_failed(self, event: dict[str, Any]) -> None:
        d = event.get("data") or {}
        tid = d.get("task_id") or ""
        if not tid:
            return
        tool_id = d.get("tool_id") or d.get("tool") or ""
        entry = {"ts": time.time(), "kind": "tool", "tool": tool_id,
                 "ok": False, "error": str(d.get("error") or "")[:300]}
        self._append(tid, entry)
        self._emit_activity(tid, "tool_error", entry)

    def _on_terminal_command(self, event: dict[str, Any]) -> None:
        d = event.get("data") or {}
        tid = d.get("task_id") or ""
        if not tid:
            return
        entry = {"ts": time.time(), "kind": "terminal", "command": str(d.get("command") or "")[:300],
                 "cwd": str(d.get("cwd") or "")}
        self._append(tid, entry)
        self._emit_activity(tid, "terminal", entry)

    def _on_terminal_completed(self, event: dict[str, Any]) -> None:
        d = event.get("data") or {}
        tid = d.get("task_id") or ""
        if not tid:
            return
        entry = {"ts": time.time(), "kind": "exit", "command": str(d.get("command") or "")[:300],
                 "code": d.get("exit_code")}
        self._append(tid, entry)
        self._emit_activity(tid, "terminal_exit", entry)

    def _on_file_opened(self, event: dict[str, Any]) -> None:
        d = event.get("data") or {}
        tid = d.get("task_id") or ""
        if not tid:
            return
        entry = {"ts": time.time(), "kind": "file", "action": "opened", "path": str(d.get("path") or "")}
        self._append(tid, entry)
        self._emit_activity(tid, "file", entry)

    def _on_file_event(self, event: dict[str, Any]) -> None:
        d = event.get("data") or {}
        tid = d.get("task_id") or ""
        if not tid:
            return
        action = str(event.get("type") or "changed").replace("file.", "")
        entry = {"ts": time.time(), "kind": "file", "action": action, "path": str(d.get("path") or "")}
        self._append(tid, entry)
        self._emit_activity(tid, "file", entry)

    def _emit_activity(self, task_id: str, kind: str, entry: dict[str, Any]) -> None:
        self._core.events.emit("velko.activity", {"task_id": task_id, "kind": kind, **entry},
                               cache=False)