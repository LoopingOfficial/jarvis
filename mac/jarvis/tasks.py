"""Task Manager — toute action longue devient une tâche persistée et suivie."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from .db import Database, dumps, loads, new_id

STATUSES = ("queued", "planning", "running", "waiting_confirmation", "completed", "failed", "cancelled")
ACTIVE_STATUSES = ("queued", "planning", "running", "waiting_confirmation")


class TaskManager:
    def __init__(self, db: Database, events, settings) -> None:
        self._db = db
        self._events = events
        self._settings = settings
        self._lock = threading.RLock()
        self._threads: dict[str, threading.Thread] = {}
        self._cancelled: set[str] = set()
        self._recover()

    def _recover(self) -> None:
        """Après redémarrage, les tâches actives orphelines sont marquées échouées."""
        rows = self._db.query(
            "SELECT id, name FROM tasks WHERE status IN ('queued','planning','running','waiting_confirmation')")
        for r in rows:
            self._db.execute(
                "UPDATE tasks SET status='failed', error=?, completed_at=? WHERE id=?",
                ("Interrompue par le redémarrage de JARVIS.", time.time(), r["id"]),
            )

    # -- création / mise à jour ---------------------------------------------
    def create(
        self, *, name: str, kind: str = "chat", agent: str = "jarvis",
        conversation_id: str = "", meta: dict[str, Any] | None = None, plan: list[Any] | None = None,
    ) -> dict[str, Any]:
        tid = new_id("task")
        now = time.time()
        self._db.execute(
            "INSERT INTO tasks(id, name, kind, status, progress, agent, tools, conversation_id, plan, "
            "result, error, meta, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, name[:300], kind, "queued", 0.0, agent, dumps([]), conversation_id,
             dumps(plan or []), "", "", dumps(meta or {}), now),
        )
        task = self.get(tid)
        self._events.emit("task.created", task)
        return task  # type: ignore[return-value]

    def get(self, task_id: str) -> dict[str, Any] | None:
        row = self._db.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        return self._row(row) if row else None

    def list(self, status: str = "", limit: int = 50, conversation_id: str = "") -> list[dict[str, Any]]:
        sql = "SELECT * FROM tasks WHERE 1=1"
        params: list[Any] = []
        if status == "active":
            sql += f" AND status IN ({','.join('?' * len(ACTIVE_STATUSES))})"
            params.extend(ACTIVE_STATUSES)
        elif status:
            sql += " AND status=?"
            params.append(status)
        if conversation_id:
            sql += " AND conversation_id=?"
            params.append(conversation_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return [self._row(r) for r in self._db.query(sql, params)]

    def active_count(self) -> int:
        return int(self._db.scalar(
            f"SELECT COUNT(*) FROM tasks WHERE status IN ({','.join('?' * len(ACTIVE_STATUSES))})",
            ACTIVE_STATUSES) or 0)

    def set_status(self, task_id: str, status: str, *, progress: float | None = None,
                   result: str | None = None, error: str | None = None) -> dict[str, Any] | None:
        if status not in STATUSES:
            return self.get(task_id)
        sets = ["status=?"]
        params: list[Any] = [status]
        if progress is not None:
            sets.append("progress=?")
            params.append(max(0.0, min(1.0, float(progress))))
        if result is not None:
            sets.append("result=?")
            params.append(str(result)[:20000])
        if error is not None:
            sets.append("error=?")
            params.append(str(error)[:4000])
        if status in {"running", "planning"}:
            sets.append("started_at=COALESCE(started_at, ?)")
            params.append(time.time())
        if status in {"completed", "failed", "cancelled"}:
            sets.append("completed_at=?")
            params.append(time.time())
        params.append(task_id)
        self._db.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", params)
        task = self.get(task_id)
        if task:
            event = {
                "running": "task.started", "planning": "task.started", "completed": "task.completed",
                "failed": "task.failed", "cancelled": "task.cancelled",
                "waiting_confirmation": "task.waiting_confirmation", "queued": "task.created",
            }.get(status, "task.progress")
            self._events.emit(event, task)
        return task

    def progress(self, task_id: str, value: float, message: str = "") -> None:
        self._db.execute("UPDATE tasks SET progress=? WHERE id=?", (max(0.0, min(1.0, value)), task_id))
        if message:
            self.log(task_id, message)
        self._events.emit("task.progress", {"id": task_id, "progress": value, "message": message})

    def complete(self, task_id: str, result: str = "") -> bool:
        task = self.get(task_id)
        if not task:
            return False
        self.set_status(task_id, "completed", progress=1.0, result=result)
        return True

    def fail(self, task_id: str, error: str) -> bool:
        if not self.get(task_id):
            return False
        self.set_status(task_id, "failed", error=error)
        self._events.feed(f"Tâche en échec : {self.get(task_id)['name'][:70]}", level="error",  # type: ignore[index]
                          kind="task", detail=error[:300], source="tasks", meta={"task_id": task_id})
        return True

    def cancel(self, task_id: str) -> bool:
        task = self.get(task_id)
        if not task or task["status"] not in ACTIVE_STATUSES:
            return False
        with self._lock:
            self._cancelled.add(task_id)
        self.set_status(task_id, "cancelled", error="Annulée par l'utilisateur.")
        return True

    def is_cancelled(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._cancelled

    # -- plan d'exécution ---------------------------------------------------
    # Contrat avec la checklist du Command Center (spatial_shell.setTaskSteps) :
    # chaque étape est {key, label, state} avec state ∈ idle|run|done|err.
    # Règle tenue : on ne publie que des étapes que le backend a réellement
    # déclarées ou réellement exécutées — jamais une progression décorative.
    PLAN_STATES = ("idle", "run", "done", "err")

    @staticmethod
    def _norm_step(step: Any, index: int) -> dict[str, Any]:
        """Accepte une chaîne, ou un dict {key,label,state} déjà formé."""
        if isinstance(step, dict):
            key = str(step.get("key") or step.get("name") or f"s{index}")
            label = str(step.get("label") or step.get("name") or key)
            state = str(step.get("state") or "idle")
        else:
            key = label = str(step)
            state = "idle"
        if state not in TaskManager.PLAN_STATES:
            state = "idle"
        return {"key": key[:60], "label": label[:60], "state": state}

    def set_plan(self, task_id: str, plan: list[Any]) -> list[dict[str, Any]]:
        """Déclare le plan d'une tâche dont les étapes sont connues d'avance."""
        steps = [self._norm_step(s, i) for i, s in enumerate(plan or [])]
        self._db.execute("UPDATE tasks SET plan=? WHERE id=?", (dumps(steps), task_id))
        self._events.emit("task.progress", {"id": task_id, "plan": steps})
        return steps

    def plan(self, task_id: str) -> list[dict[str, Any]]:
        row = self._db.one("SELECT plan FROM tasks WHERE id=?", (task_id,))
        return loads(row["plan"], []) or [] if row else []

    def step(self, task_id: str, key: str, state: str = "run", label: str = "") -> None:
        """Fait avancer une étape ; l'ajoute si la tâche découvre son plan en route.

        Les tâches dont le plan n'est pas connu d'avance (boucle d'outils du
        chat : c'est le modèle qui choisit) construisent ainsi une checklist
        honnête, étape par étape, à mesure que les outils tournent vraiment.
        """
        if state not in self.PLAN_STATES:
            state = "run"
        key = str(key)[:60]
        steps = self.plan(task_id)
        for s in steps:
            if s.get("key") == key:
                s["state"] = state
                if label:
                    s["label"] = str(label)[:60]
                break
        else:
            steps.append({"key": key, "label": (str(label) or key)[:60], "state": state})
        self._db.execute("UPDATE tasks SET plan=? WHERE id=?", (dumps(steps), task_id))
        self._events.emit("task.progress", {"id": task_id, "plan": steps})

    def add_tool(self, task_id: str, tool_id: str) -> None:
        row = self._db.one("SELECT tools FROM tasks WHERE id=?", (task_id,))
        if not row:
            return
        tools = loads(row["tools"], []) or []
        if tool_id not in tools:
            tools.append(tool_id)
            self._db.execute("UPDATE tasks SET tools=? WHERE id=?", (dumps(tools), task_id))

    def set_agent(self, task_id: str, agent: str) -> None:
        self._db.execute("UPDATE tasks SET agent=? WHERE id=?", (agent, task_id))

    # -- logs ---------------------------------------------------------------
    def log(self, task_id: str, message: str, level: str = "info", data: Any = None) -> None:
        self._db.execute(
            "INSERT INTO task_logs(task_id, ts, level, message, data) VALUES(?,?,?,?,?)",
            (task_id, time.time(), level, str(message)[:2000], dumps(data) if data is not None else ""),
        )
        # `data` voyage aussi sur le SSE : c'est lui qui porte la phase réelle
        # des pipelines (ex. security_audit → {"phase","completed","total"}).
        # Sans ça, la progression existe côté backend et se perd en route.
        entry: dict[str, Any] = {"level": level, "message": str(message)[:500]}
        if data is not None:
            entry["data"] = data
        self._events.emit("task.progress", {"id": task_id, "log": entry})

    def logs(self, task_id: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._db.query(
            "SELECT ts, level, message, data FROM task_logs WHERE task_id=? ORDER BY id LIMIT ?", (task_id, limit))
        return [{"ts": r["ts"], "level": r["level"], "message": r["message"], "data": loads(r["data"], None)}
                for r in rows]

    def detail(self, task_id: str) -> dict[str, Any] | None:
        task = self.get(task_id)
        if not task:
            return None
        task["logs"] = self.logs(task_id)
        return task

    # -- exécution en arrière-plan ------------------------------------------
    def run_background(self, task_id: str, worker: Callable[[], None]) -> None:
        def runner() -> None:
            try:
                worker()
            except Exception as exc:
                self.fail(task_id, str(exc))
            finally:
                with self._lock:
                    self._threads.pop(task_id, None)
                    self._cancelled.discard(task_id)

        thread = threading.Thread(target=runner, daemon=True, name=f"task-{task_id}")
        with self._lock:
            self._threads[task_id] = thread
        thread.start()

    def stats(self) -> dict[str, Any]:
        rows = self._db.query("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")
        counts = {r["status"]: r["n"] for r in rows}
        return {
            "total": sum(counts.values()),
            "active": sum(counts.get(s, 0) for s in ACTIVE_STATUSES),
            "running": counts.get("running", 0) + counts.get("planning", 0),
            "queued": counts.get("queued", 0),
            "completed": counts.get("completed", 0),
            "failed": counts.get("failed", 0),
            "waiting_confirmation": counts.get("waiting_confirmation", 0),
            "by_status": counts,
        }

    def prune(self, retention_days: int = 30) -> int:
        cutoff = time.time() - retention_days * 86400
        ids = [r["id"] for r in self._db.query(
            "SELECT id FROM tasks WHERE completed_at IS NOT NULL AND completed_at < ?", (cutoff,))]
        for tid in ids:
            self._db.execute("DELETE FROM task_logs WHERE task_id=?", (tid,))
        self._db.execute("DELETE FROM tasks WHERE completed_at IS NOT NULL AND completed_at < ?", (cutoff,))
        return len(ids)

    @staticmethod
    def _row(row) -> dict[str, Any]:
        d = {k: row[k] for k in row.keys()}
        d["tools"] = loads(d.get("tools"), [])
        d["plan"] = loads(d.get("plan"), [])
        d["meta"] = loads(d.get("meta"), {})
        return d
