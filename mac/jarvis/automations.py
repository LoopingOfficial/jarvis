"""Automatisations et workflows.

Abstraction volontaire : une automatisation JARVIS est autonome (scheduler
interne). n8n est un *moteur d'exécution optionnel* utilisé par un pas de
workflow, jamais une dépendance du système.

Déclencheurs : cron | interval | daily | weekly | event | webhook | condition | manual
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from .db import Database, dumps, loads, new_id

TRIGGER_TYPES = ("cron", "interval", "daily", "weekly", "event", "webhook", "condition", "manual")
WEEKDAYS = {"lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3, "vendredi": 4, "samedi": 5, "dimanche": 6}


class AutomationManager:
    def __init__(self, db: Database, events, tasks, settings, core=None) -> None:
        self._db = db
        self._events = events
        self._tasks = tasks
        self._settings = settings
        self._core = core
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def bind_core(self, core) -> None:
        self._core = core

    # -- CRUD ---------------------------------------------------------------
    def create(self, *, name: str, instruction: str, trigger: dict[str, Any],
               description: str = "", steps: list[dict[str, Any]] | None = None,
               source: str = "user", enabled: bool = True) -> dict[str, Any]:
        wid = new_id("wf")
        now = time.time()
        steps = steps or [{"type": "agent", "instruction": instruction}]
        self._db.execute(
            "INSERT INTO workflows(id, name, description, trigger, steps, enabled, source, created_at, "
            "updated_at, next_run_at, state) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (wid, name[:200], description[:1000], dumps(trigger), dumps(steps),
             1 if enabled else 0, source, now, now, self.next_run(trigger), dumps({})),
        )
        wf = self.get(wid)
        self._events.emit("workflow.created", wf)
        self._events.feed(f"Automatisation créée : {name}", level="info", kind="workflow",
                          detail=self.describe_trigger(trigger), source="automation")
        return wf  # type: ignore[return-value]

    def get(self, workflow_id: str) -> dict[str, Any] | None:
        row = self._db.one("SELECT * FROM workflows WHERE id=?", (workflow_id,))
        return self._row(row) if row else None

    def list(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM workflows"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY name"
        return [self._row(r) for r in self._db.query(sql)]

    def update(self, workflow_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        current = self.get(workflow_id)
        if not current:
            return None
        trigger = payload.get("trigger", current["trigger"])
        if isinstance(trigger, str):
            trigger = self.parse_trigger(trigger) or current["trigger"]
        steps = payload.get("steps", current["steps"])
        self._db.execute(
            "UPDATE workflows SET name=?, description=?, trigger=?, steps=?, enabled=?, updated_at=?, "
            "next_run_at=? WHERE id=?",
            (str(payload.get("name", current["name"]))[:200],
             str(payload.get("description", current["description"]))[:1000],
             dumps(trigger), dumps(steps),
             1 if payload.get("enabled", current["enabled"]) else 0,
             time.time(), self.next_run(trigger), workflow_id),
        )
        wf = self.get(workflow_id)
        self._events.emit("workflow.updated", wf)
        return wf

    def delete(self, workflow_id: str) -> bool:
        ok = bool(self._db.execute("DELETE FROM workflows WHERE id=?", (workflow_id,)).rowcount)
        if ok:
            self._db.execute("DELETE FROM workflow_runs WHERE workflow_id=?", (workflow_id,))
            self._events.emit("workflow.updated", {"id": workflow_id, "deleted": True})
        return ok

    def set_enabled(self, workflow_id: str, enabled: bool) -> bool:
        wf = self.get(workflow_id)
        if not wf:
            return False
        self._db.execute("UPDATE workflows SET enabled=?, next_run_at=? WHERE id=?",
                         (1 if enabled else 0, self.next_run(wf["trigger"]) if enabled else None, workflow_id))
        self._events.emit("workflow.updated", self.get(workflow_id))
        return True

    # -- déclencheurs -------------------------------------------------------
    def parse_trigger(self, text: str) -> dict[str, Any] | None:
        """Traduit une phrase française en déclencheur structuré."""
        t = (text or "").casefold().strip()
        if not t:
            return None
        if t in {"manuel", "manual", "à la demande", "a la demande"}:
            return {"type": "manual"}

        hour, minute = 8, 0
        hm = re.search(r"(\d{1,2})\s*(?:h|:)\s*(\d{2})?", t)
        if hm:
            hour, minute = int(hm.group(1)), int(hm.group(2) or 0)
        elif "matin" in t:
            hour, minute = 8, 0
        elif "midi" in t:
            hour, minute = 12, 0
        elif "soir" in t:
            hour, minute = 19, 0
        elif "nuit" in t:
            hour, minute = 2, 0

        m = re.search(r"(?:toutes? les|chaque)\s+(\d+)\s*(minute|min|heure|h)", t)
        if m:
            n = int(m.group(1))
            seconds = n * 60 if m.group(2).startswith("min") else n * 3600
            return {"type": "interval", "seconds": max(60, seconds)}

        for name, idx in WEEKDAYS.items():
            if re.search(rf"(?:chaque|tous les)\s+{name}", t):
                return {"type": "weekly", "weekday": idx, "hour": hour, "minute": minute}

        if re.search(r"chaque (?:jour|matin|soir|midi|nuit)|tous les jours|quotidien", t):
            return {"type": "daily", "hour": hour, "minute": minute}

        if "webhook" in t:
            return {"type": "webhook", "token": new_id("hook")}

        cron = re.search(r"cron\s*[:=]?\s*([\d*/,\- ]{5,})", t)
        if cron:
            return {"type": "cron", "expression": cron.group(1).strip()}

        m = re.search(r"(?:quand|lorsque|si)\s+(.{4,120})", t)
        if m:
            return {"type": "condition", "check": m.group(1).strip(), "interval_s": 900}

        if hm or "matin" in t or "soir" in t:
            return {"type": "daily", "hour": hour, "minute": minute}
        return None

    def describe_trigger(self, trigger: dict[str, Any]) -> str:
        if not isinstance(trigger, dict):
            return "déclencheur inconnu"
        kind = trigger.get("type")
        if kind == "daily":
            return f"chaque jour à {int(trigger.get('hour', 8)):02d}h{int(trigger.get('minute', 0)):02d}"
        if kind == "weekly":
            names = [k for k, v in WEEKDAYS.items() if v == int(trigger.get("weekday", 0))]
            return (f"chaque {names[0] if names else 'semaine'} à "
                    f"{int(trigger.get('hour', 8)):02d}h{int(trigger.get('minute', 0)):02d}")
        if kind == "interval":
            secs = int(trigger.get("seconds", 3600))
            return f"toutes les {secs // 60} minutes" if secs < 3600 else f"toutes les {secs // 3600} heures"
        if kind == "cron":
            return f"cron {trigger.get('expression', '')}"
        if kind == "event":
            return f"sur événement {trigger.get('event', '')}"
        if kind == "webhook":
            return "sur appel webhook"
        if kind == "condition":
            return f"si {trigger.get('check', '')}"
        return "manuel"

    def next_run(self, trigger: dict[str, Any], after: float | None = None) -> float | None:
        if not isinstance(trigger, dict):
            return None
        now = datetime.fromtimestamp(after or time.time())
        kind = trigger.get("type")
        if kind == "interval":
            return (now + timedelta(seconds=int(trigger.get("seconds", 3600)))).timestamp()
        if kind == "daily":
            target = now.replace(hour=int(trigger.get("hour", 8)), minute=int(trigger.get("minute", 0)),
                                 second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            return target.timestamp()
        if kind == "weekly":
            weekday = int(trigger.get("weekday", 0))
            target = now.replace(hour=int(trigger.get("hour", 8)), minute=int(trigger.get("minute", 0)),
                                 second=0, microsecond=0)
            ahead = (weekday - target.weekday()) % 7
            if ahead == 0 and target <= now:
                ahead = 7
            return (target + timedelta(days=ahead)).timestamp()
        if kind == "cron":
            return self._next_cron(str(trigger.get("expression", "")), now)
        if kind == "condition":
            return (now + timedelta(seconds=int(trigger.get("interval_s", 900)))).timestamp()
        return None

    @staticmethod
    def _next_cron(expression: str, now: datetime) -> float | None:
        """Cron simplifié : minute heure jour mois jour_semaine (valeurs, listes, */n, *)."""
        parts = expression.split()
        if len(parts) != 5:
            return None

        def matches(field: str, value: int, low: int, high: int) -> bool:
            if field == "*":
                return True
            for token in field.split(","):
                if token.startswith("*/"):
                    try:
                        if value % int(token[2:]) == 0:
                            return True
                    except ValueError:
                        continue
                elif "-" in token:
                    try:
                        a, b = (int(x) for x in token.split("-", 1))
                        if a <= value <= b:
                            return True
                    except ValueError:
                        continue
                else:
                    try:
                        if int(token) == value:
                            return True
                    except ValueError:
                        continue
            return False

        candidate = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        for _ in range(60 * 24 * 40):  # jusqu'à 40 jours
            if (matches(parts[0], candidate.minute, 0, 59)
                    and matches(parts[1], candidate.hour, 0, 23)
                    and matches(parts[2], candidate.day, 1, 31)
                    and matches(parts[3], candidate.month, 1, 12)
                    and matches(parts[4], (candidate.weekday() + 1) % 7, 0, 6)):
                return candidate.timestamp()
            candidate += timedelta(minutes=1)
        return None

    # -- exécution ----------------------------------------------------------
    def run(self, workflow_id: str, *, reason: str = "manuel",
            payload: dict[str, Any] | None = None) -> dict[str, Any]:
        wf = self.get(workflow_id)
        if not wf:
            return {"ok": False, "output": "Automatisation introuvable."}
        core = self._core
        run_id = new_id("run")
        started = time.time()
        task = self._tasks.create(name=f"Automatisation : {wf['name']}", kind="workflow", agent="task",
                                  meta={"workflow_id": workflow_id, "reason": reason})
        self._db.execute("INSERT INTO workflow_runs(id, workflow_id, started_at, status, task_id) "
                         "VALUES(?,?,?,?,?)", (run_id, workflow_id, started, "running", task["id"]))
        self._events.emit("workflow.started", {"id": workflow_id, "name": wf["name"], "run_id": run_id,
                                               "task_id": task["id"], "reason": reason})
        self._tasks.set_status(task["id"], "running", progress=0.1)

        outputs: list[str] = []
        ok = True
        try:
            for index, step in enumerate(wf["steps"] or []):
                self._tasks.progress(task["id"], min(0.9, 0.1 + index * 0.3), f"Étape {index + 1}")
                step_type = str(step.get("type") or "agent")
                if step_type == "tool":
                    result = core.runner.run(str(step.get("tool")), step.get("arguments") or {},
                                             agent="task", task_id=task["id"])
                    outputs.append(result.output)
                    ok = ok and result.ok
                elif step_type == "n8n":
                    result = core.runner.run("n8n.workflow", {
                        "action": "run", "workflow": step.get("workflow", ""),
                        "connector_id": step.get("connector_id", ""), "data": payload or {}},
                        agent="task", task_id=task["id"])
                    outputs.append(result.output)
                    ok = ok and result.ok
                else:
                    result = core.orchestrator.run_agent(
                        str(step.get("agent") or "task"), str(step.get("instruction") or wf["name"]),
                        task_id=task["id"])
                    outputs.append(result.get("output", ""))
                    ok = ok and result.get("ok", False)
        except Exception as exc:
            ok = False
            outputs.append(str(exc))

        output = "\n".join(o for o in outputs if o)[:8000]
        finished = time.time()
        self._db.execute("UPDATE workflow_runs SET finished_at=?, status=?, output=? WHERE id=?",
                         (finished, "success" if ok else "failed", output, run_id))
        self._db.execute(
            "UPDATE workflows SET last_run_at=?, last_status=?, run_count=run_count+1, next_run_at=? WHERE id=?",
            (finished, "success" if ok else "failed", self.next_run(wf["trigger"], finished), workflow_id))
        if ok:
            self._tasks.complete(task["id"], output or "Automatisation terminée.")
        else:
            self._tasks.fail(task["id"], output or "Échec de l'automatisation.")
        self._events.emit("workflow.completed" if ok else "workflow.failed",
                          {"id": workflow_id, "name": wf["name"], "run_id": run_id, "ok": ok})
        self._events.feed(f"Automatisation {'terminée' if ok else 'en échec'} : {wf['name']}",
                          level="info" if ok else "error", kind="workflow",
                          detail=output[:300], source="automation", meta={"workflow_id": workflow_id})
        return {"ok": ok, "output": output, "task_id": task["id"], "run_id": run_id}

    def runs(self, workflow_id: str = "", limit: int = 30) -> list[dict[str, Any]]:
        sql = "SELECT * FROM workflow_runs"
        params: list[Any] = []
        if workflow_id:
            sql += " WHERE workflow_id=?"
            params.append(workflow_id)
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        return [{k: r[k] for k in r.keys()} for r in self._db.query(sql, params)]

    def trigger_event(self, event_name: str, payload: dict[str, Any] | None = None) -> None:
        for wf in self.list(enabled_only=True):
            trigger = wf["trigger"]
            if trigger.get("type") == "event" and trigger.get("event") == event_name:
                threading.Thread(target=self.run, args=(wf["id"],),
                                 kwargs={"reason": f"événement {event_name}", "payload": payload},
                                 daemon=True).start()

    def trigger_webhook(self, token: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        for wf in self.list(enabled_only=True):
            trigger = wf["trigger"]
            if trigger.get("type") == "webhook" and trigger.get("token") == token:
                return self.run(wf["id"], reason="webhook", payload=payload)
        return {"ok": False, "output": "Webhook inconnu."}

    # -- scheduler ----------------------------------------------------------
    def start_scheduler(self) -> threading.Thread:
        def loop() -> None:
            while not self._stop.is_set():
                try:
                    if self._settings.get("automation", "scheduler_enabled", True):
                        self._tick()
                except Exception:
                    pass
                self._stop.wait(float(self._settings.get("automation", "scheduler_tick_s", 15)))

        self._thread = threading.Thread(target=loop, daemon=True, name="jarvis-scheduler")
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop.set()

    def _tick(self) -> None:
        now = time.time()
        max_concurrent = int(self._settings.get("automation", "max_concurrent_tasks", 3))
        for wf in self.list(enabled_only=True):
            nxt = wf.get("next_run_at")
            if not nxt or nxt > now:
                continue
            if wf["trigger"].get("type") in {"manual", "event", "webhook"}:
                self._db.execute("UPDATE workflows SET next_run_at=NULL WHERE id=?", (wf["id"],))
                continue
            if self._tasks.active_count() >= max_concurrent:
                continue
            if wf["trigger"].get("type") == "condition" and not self._condition_met(wf):
                self._db.execute("UPDATE workflows SET next_run_at=? WHERE id=?",
                                 (self.next_run(wf["trigger"], now), wf["id"]))
                continue
            self._db.execute("UPDATE workflows SET next_run_at=? WHERE id=?",
                             (self.next_run(wf["trigger"], now), wf["id"]))
            threading.Thread(target=self.run, args=(wf["id"],), kwargs={"reason": "planifié"},
                             daemon=True).start()

    def _condition_met(self, wf: dict[str, Any]) -> bool:
        """Condition simple : URL joignable, seuil CPU/disque. Sinon l'agent décide."""
        check = str(wf["trigger"].get("check", "")).casefold()
        core = self._core
        if not core:
            return False
        m = re.search(r"(https?://[^\s]+|[\w.-]+\.[a-z]{2,})", check)
        if m and ("inaccessible" in check or "down" in check or "ne répond" in check or "hors ligne" in check):
            result = core.runner.run("web.check", {"url": m.group(1)}, agent="task")
            return not result.ok
        m = re.search(r"(cpu|disque|disk|m[ée]moire)\D+(\d{1,3})\s*%", check)
        if m:
            metrics = core.monitor.snapshot()
            key = {"cpu": "cpu", "disque": "disk", "disk": "disk"}.get(m.group(1), "memory")
            value = metrics[key]["percent"]
            return value is not None and value >= int(m.group(2))
        return True

    @staticmethod
    def _row(row) -> dict[str, Any]:
        d = {k: row[k] for k in row.keys()}
        d["trigger"] = loads(d.get("trigger"), {})
        d["steps"] = loads(d.get("steps"), [])
        d["state"] = loads(d.get("state"), {})
        d["enabled"] = bool(d["enabled"])
        return d
