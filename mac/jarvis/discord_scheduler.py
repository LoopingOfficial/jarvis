"""Planificateur d'actions Discord — exécute des outils Discord à intervalle régulier.

Pourquoi un module dédié plutôt qu'une automatisation générique
--------------------------------------------------------------
`AutomationManager` orchestre des *workflows* : il crée une tâche, la fait
suivre par l'orchestrateur, et consomme un slot de concurrence. Pour publier un
message toutes les 30 minutes, c'est disproportionné : on veut un appel d'outil,
un verdict, une ligne d'audit. Ce module reste donc mince et n'emprunte à
`AutomationManager` que le calcul de la prochaine échéance (`next_run`), qui
sait déjà lire cron / interval / daily : dupliquer ce calcul aurait fait deux
implémentations de cron à maintenir.

Frontière asynchrone
--------------------
Rien ici ne touche l'API Discord directement. Chaque déclenchement passe par le
`SecureToolRunner`, donc par les outils `discord.*`, qui soumettent eux-mêmes
leur coroutine à la boucle du bot. Le planificateur reste un thread synchrone.

Sécurité
--------
  * Seuls les outils `discord.*` déjà enregistrés sont planifiables : une tâche
    planifiée ne peut pas devenir un vecteur d'exécution arbitraire.
  * Les outils destructeurs (purge…) demandent normalement une confirmation
    interactive, impossible à 3 h du matin. Ils ne sont donc planifiables que si
    la tâche a été créée avec `allow_destructive=True` — un accord donné une
    fois, explicitement, à la création, et consigné dans l'audit.
  * Chaque exécution est journalisée (audit_log + discord_schedule_runs) avec
    horodatage, identifiant de tâche, statut et réponse du bot.
  * Une panne de l'API Discord ne peut pas arrêter la boucle : toute exception
    est capturée, comptée, et au-delà de `MAX_FAILURES` échecs consécutifs la
    tâche passe en PAUSED au lieu de marteler une API en vrac.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime
from typing import Any

from .db import Database, dumps, loads, new_id
from .permissions import DESTRUCTIVE
from .tools import registry

STATUS_ACTIVE = "ACTIVE"
STATUS_PAUSED = "PAUSED"
STATUSES = (STATUS_ACTIVE, STATUS_PAUSED)

# Noms « plats » acceptés côté LLM (il écrit spontanément `discord_send_message`)
# ramenés sur l'identité canonique de l'outil.
TOOL_ALIASES = {
    "discord_send_message": "discord.send_message",
    "discord_send_embed": "discord.send_embed",
    "discord_send_alert": "discord.send_alert",
    "discord_purge": "discord.purge",
    "discord_announce": "discord.publish_announcement",
    "discord_publish_announcement": "discord.publish_announcement",
    "discord_summarize_channel": "discord.summarize_channel",
    "discord_lockdown": "discord.lockdown",
    "site_stats_discord": "site.stats_discord",
}

# Outils non préfixés « discord. » dont l'effet EST malgré tout une publication
# dans un salon. La règle du planificateur reste « rien qui ne publie pas sur
# Discord » : on l'ouvre nommément, jamais à une famille entière d'outils.
EXTRA_SCHEDULABLE = ("site.stats_discord",)

# Nom du paramètre « salon » attendu par chaque outil : tous ne l'appellent pas
# `channel_id`, et une tâche planifiée ne doit pas obliger l'appelant à le savoir.
CHANNEL_ARGUMENT = {
    "discord.handle_ticket": "ticket_channel_id",
    "discord.escalate_ticket": "ticket_channel_id",
    "site.stats_discord": "channel",
}

DEFAULT_TICK_S = 15.0
MAX_FAILURES = 5          # échecs consécutifs avant mise en pause automatique


class ScheduleError(ValueError):
    """Tâche refusée : message destiné à l'agent ou à l'UI."""


# ---------------------------------------------------------------------------
# Lecture d'un intervalle
# ---------------------------------------------------------------------------
def parse_interval(interval: Any) -> dict[str, Any]:
    """Normalise un intervalle en déclencheur structuré.

    Accepte, du plus explicite au plus courant :
      * un dict déjà structuré : {"type": "interval", "seconds": 1800}
      * une expression cron à 5 champs : "0 9 * * *"
      * une durée : "30m", "2h", "90 minutes", "1 jour"
      * une heure fixe : "09:00", "9h"
      * un nombre nu : minutes.
    """
    if isinstance(interval, dict):
        kind = str(interval.get("type") or "").strip()
        if kind == "interval":
            return {"type": "interval", "seconds": max(60, int(interval.get("seconds") or 3600))}
        if kind == "cron" and interval.get("expression"):
            return {"type": "cron", "expression": str(interval["expression"]).strip()}
        if kind == "daily":
            return {"type": "daily", "hour": int(interval.get("hour", 9) or 0),
                    "minute": int(interval.get("minute", 0) or 0)}
        raise ScheduleError("Déclencheur inconnu : attendu interval, cron ou daily.")

    if isinstance(interval, (int, float)) and not isinstance(interval, bool):
        return {"type": "interval", "seconds": max(60, int(interval) * 60)}

    text = str(interval or "").strip()
    if not text:
        raise ScheduleError("Aucun intervalle fourni.")

    # Cron à 5 champs — testé en premier : « 0 9 * * * » contient des chiffres que
    # les motifs de durée ci-dessous captureraient à tort.
    fields = text.split()
    if len(fields) == 5 and all(re.fullmatch(r"[\d*/,\-]+", f) for f in fields):
        return {"type": "cron", "expression": text}

    low = text.casefold()
    has_clock = bool(re.search(r"\d{1,2}\s*(?:h\s*\d{2}|:\s*\d{2})", low))
    m = re.search(r"(\d+)\s*(secondes?|s|minutes?|min|m|heures?|h|jours?|j|d)\b", low)
    if m and not has_clock:
        n, unit = int(m.group(1)), m.group(2)
        factor = (1 if unit.startswith("s") else
                  3600 if unit.startswith("h") else
                  86400 if unit[0] in "jd" else 60)
        return {"type": "interval", "seconds": max(60, n * factor)}

    m = re.search(r"(\d{1,2})\s*(?:h|:)\s*(\d{2})?", low)
    if m:
        return {"type": "daily", "hour": int(m.group(1)) % 24, "minute": int(m.group(2) or 0) % 60}

    if low.isdigit():
        return {"type": "interval", "seconds": max(60, int(low) * 60)}
    raise ScheduleError(f"Intervalle incompréhensible : « {text} ». "
                        f"Exemples : « 30m », « 2h », « 09:00 », « 0 9 * * * ».")


def describe_interval(trigger: dict[str, Any]) -> str:
    kind = (trigger or {}).get("type")
    if kind == "interval":
        secs = int(trigger.get("seconds", 3600))
        if secs % 86400 == 0:
            return f"tous les {secs // 86400} jour(s)"
        if secs % 3600 == 0:
            return f"toutes les {secs // 3600} heure(s)"
        return f"toutes les {max(1, secs // 60)} minute(s)"
    if kind == "daily":
        return f"chaque jour à {int(trigger.get('hour', 9)):02d}h{int(trigger.get('minute', 0)):02d}"
    if kind == "cron":
        return f"cron « {trigger.get('expression', '')} »"
    return "déclencheur inconnu"


# ---------------------------------------------------------------------------
# Planificateur
# ---------------------------------------------------------------------------
class DiscordScheduler:
    """Tâches Discord récurrentes : CRUD, boucle de fond, audit."""

    def __init__(self, core) -> None:
        self._core = core
        self._db: Database = core.db
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._running: set[str] = set()      # tâches en vol : pas de recouvrement
        self._lock = threading.RLock()

    # -- validation ---------------------------------------------------------
    def resolve_tool(self, tool_to_call: str) -> str:
        """Ramène un nom d'outil sur son identité canonique, ou refuse."""
        name = str(tool_to_call or "").strip()
        tool_id = TOOL_ALIASES.get(name, name)
        if not tool_id.startswith("discord.") and tool_id not in EXTRA_SCHEDULABLE:
            raise ScheduleError(
                f"Seuls les outils Discord sont planifiables ici ; « {name} » n'en est pas un.")
        if registry.get(tool_id) is None:
            known = ", ".join(sorted([t.id for t in registry.all() if t.id.startswith("discord.")]
                                     + list(EXTRA_SCHEDULABLE)))
            raise ScheduleError(f"Outil Discord inconnu : « {name} ». Disponibles : {known}.")
        return tool_id

    # -- CRUD ---------------------------------------------------------------
    def add(self, *, name: str, interval: Any, tool_to_call: str,
            target_channel_id: str = "", params: dict[str, Any] | None = None,
            status: str = STATUS_ACTIVE, allow_destructive: bool = False,
            source: str = "user") -> dict[str, Any]:
        """Enregistre une tâche planifiée et calcule sa première échéance."""
        tool_id = self.resolve_tool(tool_to_call)
        tool = registry.get(tool_id)
        if tool.resolve_risk(dict(params or {})) == DESTRUCTIVE and not allow_destructive:
            raise ScheduleError(
                f"« {tool.name} » est une action irréversible : elle ne peut pas être planifiée "
                f"sans accord explicite (allow_destructive). Cet accord remplace la confirmation "
                f"qui serait normalement demandée à chaque exécution.")
        trigger = parse_interval(interval)
        status = str(status or STATUS_ACTIVE).upper()
        if status not in STATUSES:
            raise ScheduleError(f"Statut inconnu : {status}. Attendu ACTIVE ou PAUSED.")
        if not str(name or "").strip():
            raise ScheduleError("Une tâche planifiée doit avoir un nom : il sert à la relire plus tard.")

        sid = new_id("dsch")
        now = time.time()
        self._db.execute(
            "INSERT INTO discord_schedules(id, name, trigger, target_channel_id, tool_to_call, params, "
            "status, allow_destructive, source, created_at, updated_at, next_run_at, failures) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0)",
            (sid, str(name)[:200], dumps(trigger), str(target_channel_id or ""), tool_id,
             dumps(dict(params or {})), status, 1 if allow_destructive else 0, source, now, now,
             self._next_run(trigger, now) if status == STATUS_ACTIVE else None),
        )
        task = self.get(sid)
        self._core.audit.record(
            action=f"Tâche Discord planifiée : {name} ({describe_interval(trigger)})",
            tool=tool_id, agent="scheduler", status="ok", task_id=sid,
            detail={"target_channel_id": target_channel_id, "params": params or {},
                    "status": status, "allow_destructive": allow_destructive})
        self._core.events.emit("discord.schedule.created", task)
        self._core.events.feed(f"Tâche Discord planifiée : {name}", level="info", kind="workflow",
                               detail=describe_interval(trigger), source="discord-scheduler")
        return task

    # API métier explicite pour les appels du LLM et les intégrations externes.
    # Les méthodes courtes ci-dessus restent utiles en interne et dans l'API HTTP.
    def add_scheduled_task(self, **kwargs) -> dict[str, Any]:
        return self.add(**kwargs)

    def get(self, task_id: str) -> dict[str, Any] | None:
        row = self._db.one("SELECT * FROM discord_schedules WHERE id=?", (task_id,))
        return self._row(row) if row else None

    def list(self, *, status: str = "") -> list[dict[str, Any]]:
        if status:
            rows = self._db.query("SELECT * FROM discord_schedules WHERE status=? ORDER BY created_at",
                                  (str(status).upper(),))
        else:
            rows = self._db.query("SELECT * FROM discord_schedules ORDER BY created_at")
        return [self._row(r) for r in rows]

    def get_scheduled_tasks(self, *, status: str = "") -> list[dict[str, Any]]:
        return self.list(status=status)

    def update(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        current = self.get(task_id)
        if not current:
            return None
        trigger = (parse_interval(payload["interval"]) if payload.get("interval") is not None
                   else current["trigger"])
        tool_id = (self.resolve_tool(payload["tool_to_call"]) if payload.get("tool_to_call")
                   else current["tool_to_call"])
        status = str(payload.get("status", current["status"])).upper()
        if status not in STATUSES:
            raise ScheduleError(f"Statut inconnu : {status}.")
        params = payload.get("params", current["params"])
        if not isinstance(params, dict):
            raise ScheduleError("`params` doit être un objet JSON.")
        name = str(payload.get("name", current["name"])).strip()
        if not name:
            raise ScheduleError("Une tâche planifiée doit avoir un nom.")
        allow_destructive = bool(payload.get("allow_destructive", current["allow_destructive"]))
        tool = registry.get(tool_id)
        if tool.resolve_risk(dict(params)) == DESTRUCTIVE and not allow_destructive:
            raise ScheduleError(
                f"« {tool.name} » est une action irréversible : allow_destructive est obligatoire.")
        now = time.time()
        self._db.execute(
            "UPDATE discord_schedules SET name=?, trigger=?, target_channel_id=?, tool_to_call=?, "
            "params=?, status=?, allow_destructive=?, updated_at=?, next_run_at=?, failures=0 WHERE id=?",
            (name[:200], dumps(trigger),
              str(payload.get("target_channel_id", current["target_channel_id"]) or ""), tool_id,
              dumps(dict(params or {})), status,
              1 if allow_destructive else 0,
             now, self._next_run(trigger, now) if status == STATUS_ACTIVE else None, task_id),
        )
        task = self.get(task_id)
        self._core.events.emit("discord.schedule.updated", task)
        return task

    def set_status(self, task_id: str, status: str) -> dict[str, Any] | None:
        """Suspend ou réactive une tâche sans perdre son historique."""
        status = str(status or "").upper()
        if status not in STATUSES:
            raise ScheduleError(f"Statut inconnu : {status}. Attendu ACTIVE ou PAUSED.")
        task = self.get(task_id)
        if not task:
            return None
        now = time.time()
        self._db.execute(
            "UPDATE discord_schedules SET status=?, updated_at=?, next_run_at=?, failures=0 WHERE id=?",
            (status, now, self._next_run(task["trigger"], now) if status == STATUS_ACTIVE else None,
             task_id))
        updated = self.get(task_id)
        self._core.audit.record(action=f"Tâche Discord {status.lower()} : {task['name']}",
                                tool=task["tool_to_call"], agent="scheduler", task_id=task_id)
        self._core.events.emit("discord.schedule.updated", updated)
        return updated

    def delete(self, task_id: str) -> bool:
        task = self.get(task_id)
        if not task:
            return False
        self._db.execute("DELETE FROM discord_schedules WHERE id=?", (task_id,))
        self._db.execute("DELETE FROM discord_schedule_runs WHERE schedule_id=?", (task_id,))
        self._core.audit.record(action=f"Tâche Discord supprimée : {task['name']}",
                                tool=task["tool_to_call"], agent="scheduler", task_id=task_id)
        self._core.events.emit("discord.schedule.updated", {"id": task_id, "deleted": True})
        return True

    def delete_scheduled_task(self, task_id: str) -> bool:
        return self.delete(task_id)

    def runs(self, task_id: str = "", limit: int = 30) -> list[dict[str, Any]]:
        """Historique des déclenchements (le plus récent d'abord)."""
        sql = "SELECT * FROM discord_schedule_runs"
        params: list[Any] = []
        if task_id:
            sql += " WHERE schedule_id=?"
            params.append(task_id)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(int(limit))
        return [{k: r[k] for k in r.keys()} for r in self._db.query(sql, params)]

    # -- exécution ----------------------------------------------------------
    def run_now(self, task_id: str, *, reason: str = "manuel") -> dict[str, Any]:
        """Exécute une tâche immédiatement (test d'une planification, rattrapage)."""
        task = self.get(task_id)
        if not task:
            return {"ok": False, "output": "Tâche planifiée introuvable."}
        with self._lock:
            if task_id in self._running:
                return {"ok": False, "output": "Exécution déjà en cours pour cette tâche."}
            self._running.add(task_id)
        try:
            return self._execute(task, reason=reason)
        finally:
            with self._lock:
                self._running.discard(task_id)

    def _execute(self, task: dict[str, Any], *, reason: str) -> dict[str, Any]:
        """Un déclenchement : appel de l'outil, journalisation, suite du cycle.

        Aucune exception ne remonte : la boucle doit survivre à une panne de
        l'API Discord comme à un outil qui lève.
        """
        started = time.time()
        tool_id = task["tool_to_call"]
        arguments = self._arguments(task)
        ok, output, data = False, "", None
        try:
            result = self._core.runner.run(
                tool_id, arguments, agent="scheduler", task_id=task["id"],
                # La confirmation a été donnée une fois, à la création de la tâche
                # (cf. allow_destructive) : personne ne répondra à 3 h du matin.
                confirmed=True)
            ok, output, data = bool(result.ok), str(result.output or ""), result.data
        except Exception as exc:                      # panne Discord, outil qui lève…
            output = f"{type(exc).__name__}: {exc}"
        duration = int((time.time() - started) * 1000)
        try:
            output = self._core.vault.scrub(output)
        except Exception:
            pass
        output = output[:4000]

        self._db.execute(
            "INSERT INTO discord_schedule_runs(id, schedule_id, ts, status, duration_ms, reason, "
            "tool_to_call, output, data) VALUES(?,?,?,?,?,?,?,?,?)",
            (new_id("dsrun"), task["id"], started, "SUCCESS" if ok else "FAILURE", duration, reason,
             tool_id, output, dumps(data if isinstance(data, (dict, list)) else {})))
        self._core.audit.record(
            action=f"Tâche Discord « {task['name']} » déclenchée ({reason})",
            tool=tool_id, agent="scheduler", status="ok" if ok else "error",
            duration_ms=duration, task_id=task["id"],
            detail={"result": "SUCCESS" if ok else "FAILURE", "channel": task["target_channel_id"],
                    "arguments": arguments, "response": output[:1500]})
        self._core.events.emit("discord.schedule.run",
                               {"id": task["id"], "name": task["name"], "ok": ok,
                                "reason": reason, "preview": output[:200]})

        failures = 0 if ok else int(task.get("failures") or 0) + 1
        finished = time.time()
        if failures >= MAX_FAILURES:
            # Marteler une API en échec n'apporte rien et noie l'audit : on
            # suspend et on le dit, plutôt que d'échouer silencieusement en boucle.
            self._db.execute(
                "UPDATE discord_schedules SET status=?, next_run_at=NULL, failures=?, last_run_at=?, "
                "last_status=?, run_count=run_count+1, last_output=? WHERE id=?",
                (STATUS_PAUSED, failures, finished, "FAILURE", output[:1000], task["id"]))
            self._core.events.feed(
                f"Tâche Discord suspendue après {failures} échecs : {task['name']}", level="error",
                kind="workflow", detail=output[:300], source="discord-scheduler")
        else:
            # Un déclenchement manuel peut arriver avant l'échéance initiale.
            # On conserve alors l'ancrage de la planification pour ne pas
            # repousser indéfiniment la prochaine occurrence.
            schedule_after = max(finished, float(task.get("next_run_at") or 0))
            self._db.execute(
                "UPDATE discord_schedules SET failures=?, last_run_at=?, last_status=?, "
                "run_count=run_count+1, last_output=?, next_run_at=? WHERE id=?",
                (failures, finished, "SUCCESS" if ok else "FAILURE", output[:1000],
                 self._next_run(task["trigger"], schedule_after)
                 if task["status"] == STATUS_ACTIVE else None,
                 task["id"]))
        return {"ok": ok, "output": output, "data": data, "schedule_id": task["id"]}

    def _arguments(self, task: dict[str, Any]) -> dict[str, Any]:
        """Paramètres de l'outil : `params` enrichi du salon cible.

        Le salon nommé dans `params` gagne sur `target_channel_id` : si l'appelant
        a précisé les deux, c'est le plus spécifique qui exprime son intention.
        """
        arguments = dict(task.get("params") or {})
        key = CHANNEL_ARGUMENT.get(task["tool_to_call"], "channel_id")
        if task.get("target_channel_id") and not arguments.get(key):
            arguments[key] = str(task["target_channel_id"])
        return arguments

    # -- boucle de fond -----------------------------------------------------
    def start(self) -> threading.Thread:
        """Démarre la boucle en tâche de fond (thread démon, arrêt propre)."""
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop.clear()

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    if self._core.settings.get("discord", "scheduler_enabled", True):
                        self._tick()
                except Exception as exc:          # la boucle ne meurt jamais
                    self._core.events.emit("discord.schedule.error", {"error": str(exc)[:300]})
                self._stop.wait(float(self._core.settings.get(
                    "discord", "scheduler_tick_s", DEFAULT_TICK_S)))

        self._thread = threading.Thread(target=loop, daemon=True, name="jarvis-discord-scheduler")
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop.set()

    def _tick(self) -> None:
        now = time.time()
        for task in self.list(status=STATUS_ACTIVE):
            due = task.get("next_run_at")
            if not due:
                # Échéance absente (tâche réactivée hors ligne) : on la recalcule
                # au lieu de laisser la tâche dormir indéfiniment.
                self._db.execute("UPDATE discord_schedules SET next_run_at=? WHERE id=?",
                                 (self._next_run(task["trigger"], now), task["id"]))
                continue
            if due > now:
                continue
            with self._lock:
                if task["id"] in self._running:
                    continue                      # exécution précédente encore en vol
                self._running.add(task["id"])

            def worker(t=task) -> None:
                try:
                    self._execute(t, reason="planifié")
                finally:
                    with self._lock:
                        self._running.discard(t["id"])

            threading.Thread(target=worker, daemon=True,
                             name=f"discord-sched-{task['id']}").start()

    def _next_run(self, trigger: dict[str, Any], after: float | None = None) -> float | None:
        """Prochaine échéance. Délègue à AutomationManager, seul détenteur du cron."""
        try:
            nxt = self._core.automations.next_run(trigger, after)
        except Exception:
            nxt = None
        if nxt:
            return nxt
        # Repli : une expression cron illisible ne doit pas figer la tâche pour
        # toujours — on retente dans une heure, l'audit gardant trace des échecs.
        return (after or time.time()) + 3600

    # -- sérialisation ------------------------------------------------------
    @staticmethod
    def _row(row) -> dict[str, Any]:
        d = {k: row[k] for k in row.keys()}
        d["trigger"] = loads(d.get("trigger"), {})
        d["params"] = loads(d.get("params"), {})
        d["allow_destructive"] = bool(d.get("allow_destructive"))
        d["interval"] = describe_interval(d["trigger"])
        nxt = d.get("next_run_at")
        d["next_run_iso"] = datetime.fromtimestamp(nxt).isoformat(timespec="seconds") if nxt else ""
        return d
