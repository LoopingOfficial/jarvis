"""Planificateur Discord : lecture des intervalles, cycle de vie, audit, robustesse.

Aucun test ne touche Discord : le planificateur ne connaît que le ToolRunner,
ce qui permet de vérifier son comportement avec un cœur factice.
"""
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from jarvis.db import Database
from jarvis.tools import discord_tools  # noqa: F401  (l'import enregistre les outils)
from jarvis.discord_scheduler import (STATUS_ACTIVE, STATUS_PAUSED, DiscordScheduler,
                                      ScheduleError, describe_interval, parse_interval)


class _Result:
    def __init__(self, ok=True, output="envoyé", data=None):
        self.ok, self.output, self.data = ok, output, data or {}


class _Runner:
    """ToolRunner factice : mémorise les appels, peut simuler une panne Discord."""

    def __init__(self):
        self.calls = []
        self.raise_error = None
        self.ok = True

    def run(self, tool_id, arguments=None, **kwargs):
        self.calls.append((tool_id, dict(arguments or {}), kwargs))
        if self.raise_error:
            raise self.raise_error
        return _Result(ok=self.ok, output="panne" if not self.ok else "envoyé")


class _Bus:
    def __init__(self):
        self.events = []

    def emit(self, kind, payload=None):
        self.events.append((kind, payload))

    def feed(self, *a, **k):
        pass


class _Audit:
    def __init__(self):
        self.records = []

    def record(self, **kwargs):
        self.records.append(kwargs)
        return len(self.records)


class _Settings:
    @staticmethod
    def get(section, key, default=None):
        return default


class _Vault:
    @staticmethod
    def scrub(text):
        return text


class _Core:
    """Assez de JARVIS pour le planificateur : db, audit, events, runner, cron."""

    def __init__(self, db):
        from jarvis.automations import AutomationManager
        self.db = db
        self.audit = _Audit()
        self.events = _Bus()
        self.settings = _Settings()
        self.vault = _Vault()
        self.runner = _Runner()
        self.automations = AutomationManager(db, self.events, None, self.settings)


class IntervalParsingTests(unittest.TestCase):
    def test_durations(self):
        self.assertEqual(parse_interval("30m"), {"type": "interval", "seconds": 1800})
        self.assertEqual(parse_interval("2h"), {"type": "interval", "seconds": 7200})
        self.assertEqual(parse_interval("90 minutes"), {"type": "interval", "seconds": 5400})
        self.assertEqual(parse_interval(45), {"type": "interval", "seconds": 2700})

    def test_fixed_hour_is_not_read_as_a_duration(self):
        self.assertEqual(parse_interval("09:00"), {"type": "daily", "hour": 9, "minute": 0})
        self.assertEqual(parse_interval("9h30"), {"type": "daily", "hour": 9, "minute": 30})

    def test_cron_expression(self):
        self.assertEqual(parse_interval("0 9 * * *"),
                         {"type": "cron", "expression": "0 9 * * *"})

    def test_floor_of_one_minute(self):
        # Une tâche « toutes les 5 secondes » inonderait Discord et serait
        # coupée par les limites de débit : on plafonne à la minute.
        self.assertEqual(parse_interval("5s")["seconds"], 60)

    def test_unreadable_interval_is_refused_with_examples(self):
        with self.assertRaises(ScheduleError) as ctx:
            parse_interval("de temps en temps")
        self.assertIn("30m", str(ctx.exception))

    def test_description_is_human_readable(self):
        self.assertEqual(describe_interval({"type": "interval", "seconds": 1800}),
                         "toutes les 30 minute(s)")
        self.assertEqual(describe_interval({"type": "daily", "hour": 9, "minute": 0}),
                         "chaque jour à 09h00")


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Database(Path(self._tmp.name) / "test.db")
        self.core = _Core(self.db)
        self.scheduler = DiscordScheduler(self.core)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def add(self, **kwargs):
        base = dict(name="Vérification d'état", interval="30m",
                    tool_to_call="discord.send_message", target_channel_id="123",
                    params={"content": "ping"})
        base.update(kwargs)
        return self.scheduler.add(**base)

    # -- CRUD ---------------------------------------------------------------
    def test_add_list_delete(self):
        task = self.add()
        self.assertEqual(task["status"], STATUS_ACTIVE)
        self.assertEqual(task["tool_to_call"], "discord.send_message")
        self.assertTrue(task["next_run_at"] > time.time())
        self.assertEqual([t["id"] for t in self.scheduler.list()], [task["id"]])
        self.assertTrue(self.scheduler.delete(task["id"]))
        self.assertEqual(self.scheduler.list(), [])

    def test_pause_and_resume(self):
        task = self.add()
        paused = self.scheduler.set_status(task["id"], STATUS_PAUSED)
        self.assertEqual(paused["status"], STATUS_PAUSED)
        self.assertIsNone(paused["next_run_at"])          # plus d'échéance en pause
        resumed = self.scheduler.set_status(task["id"], STATUS_ACTIVE)
        self.assertTrue(resumed["next_run_at"] > time.time())

    def test_flat_tool_name_is_accepted(self):
        task = self.add(tool_to_call="discord_send_message")
        self.assertEqual(task["tool_to_call"], "discord.send_message")

    def test_non_discord_tool_is_refused(self):
        with self.assertRaises(ScheduleError):
            self.add(tool_to_call="system.shell")

    def test_unknown_discord_tool_is_refused(self):
        with self.assertRaises(ScheduleError):
            self.add(tool_to_call="discord.inexistant")

    def test_destructive_tool_needs_explicit_agreement(self):
        with self.assertRaises(ScheduleError):
            self.add(tool_to_call="discord.purge", params={"limit": 50})
        task = self.add(tool_to_call="discord.purge", params={"limit": 50},
                        allow_destructive=True)
        self.assertTrue(task["allow_destructive"])

    def test_nameless_task_is_refused(self):
        with self.assertRaises(ScheduleError):
            self.add(name="  ")

    def test_named_api_aliases_manage_tasks(self):
        task = self.scheduler.add_scheduled_task(
            name="Alias", interval="30m", tool_to_call="discord.send_message",
            target_channel_id="123", params={"content": "ping"})
        self.assertEqual(self.scheduler.get_scheduled_tasks()[0]["id"], task["id"])
        self.assertTrue(self.scheduler.delete_scheduled_task(task["id"]))

    def test_update_cannot_enable_destructive_action_silently(self):
        task = self.add()
        with self.assertRaises(ScheduleError):
            self.scheduler.update(task["id"], {
                "tool_to_call": "discord.purge", "params": {"limit": 50}})

    # -- exécution ----------------------------------------------------------
    def test_run_injects_the_target_channel(self):
        task = self.add()
        result = self.scheduler.run_now(task["id"])
        self.assertTrue(result["ok"])
        tool_id, arguments, kwargs = self.core.runner.calls[0]
        self.assertEqual(tool_id, "discord.send_message")
        self.assertEqual(arguments, {"content": "ping", "channel_id": "123"})
        self.assertTrue(kwargs["confirmed"])            # personne ne confirme à 3 h du matin
        self.assertEqual(kwargs["agent"], "scheduler")

    def test_explicit_channel_in_params_wins(self):
        task = self.add(params={"content": "ping", "channel_id": "999"})
        self.scheduler.run_now(task["id"])
        self.assertEqual(self.core.runner.calls[0][1]["channel_id"], "999")

    def test_ticket_tools_use_their_own_channel_argument(self):
        task = self.add(tool_to_call="discord.handle_ticket",
                        params={"user_message": "aide"}, target_channel_id="42")
        self.scheduler.run_now(task["id"])
        self.assertEqual(self.core.runner.calls[0][1]["ticket_channel_id"], "42")

    def test_run_is_audited_and_recorded(self):
        task = self.add()
        self.scheduler.run_now(task["id"])
        runs = self.scheduler.runs(task["id"])
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "SUCCESS")
        self.assertEqual(runs[0]["tool_to_call"], "discord.send_message")
        triggered = [r for r in self.core.audit.records if "déclenchée" in r["action"]]
        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0]["task_id"], task["id"])
        self.assertEqual(triggered[0]["status"], "ok")

    def test_next_run_advances_after_execution(self):
        task = self.add()
        first = task["next_run_at"]
        self.scheduler.run_now(task["id"])
        self.assertGreater(self.scheduler.get(task["id"])["next_run_at"], first)

    # -- robustesse ---------------------------------------------------------
    def test_discord_outage_does_not_propagate(self):
        task = self.add()
        self.core.runner.raise_error = RuntimeError("Discord injoignable")
        result = self.scheduler.run_now(task["id"])        # ne lève pas
        self.assertFalse(result["ok"])
        self.assertIn("Discord injoignable", result["output"])
        self.assertEqual(self.scheduler.runs(task["id"])[0]["status"], "FAILURE")
        self.assertEqual(self.scheduler.get(task["id"])["failures"], 1)

    def test_task_is_paused_after_repeated_failures(self):
        task = self.add()
        self.core.runner.ok = False
        for _ in range(5):
            self.scheduler.run_now(task["id"])
        stored = self.scheduler.get(task["id"])
        self.assertEqual(stored["status"], STATUS_PAUSED)
        self.assertIsNone(stored["next_run_at"])

    def test_a_success_clears_the_failure_counter(self):
        task = self.add()
        self.core.runner.ok = False
        self.scheduler.run_now(task["id"])
        self.core.runner.ok = True
        self.scheduler.run_now(task["id"])
        self.assertEqual(self.scheduler.get(task["id"])["failures"], 0)

    def test_tick_only_runs_due_active_tasks(self):
        due = self.add(name="Due")
        self.db.execute("UPDATE discord_schedules SET next_run_at=? WHERE id=?",
                        (time.time() - 5, due["id"]))
        self.add(name="Plus tard")                        # échéance dans 30 min
        paused = self.add(name="En pause")
        self.scheduler.set_status(paused["id"], STATUS_PAUSED)
        self.scheduler._tick()
        for _ in range(50):                               # le tick exécute dans un thread
            if self.core.runner.calls:
                break
            time.sleep(0.02)
        time.sleep(0.05)
        self.assertEqual(len(self.core.runner.calls), 1)


if __name__ == "__main__":
    unittest.main()
