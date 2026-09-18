"""Tests du moteur de missions : états, priorités, dépendances, ressources,
pause / reprise / annulation, attente utilisateur."""
from __future__ import annotations

import time
import unittest

from jarvis.background_tasks import (BLOCKED, CANCELLED, COMPLETED, FAILED,
                                     PAUSED, QUEUED, RUNNING, WAITING_RESOURCE,
                                     WAITING_USER, BackgroundTaskManager)

from .background_support import (EventRecorder, make_core, new_temp_dir,
                                 cleanup_tree, shell_sleep, wait_until)


def _engine(tmp_path, **kwargs):
    core = make_core(tmp_path, **kwargs)
    engine = BackgroundTaskManager(core)
    engine.start()
    return core, engine


class EngineBasicsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = new_temp_dir()
        self.addCleanup(cleanup_tree, self.tmp)

    def test_validation_errors(self) -> None:
        _, engine = _engine(self.tmp)
        with self.assertRaises(ValueError):
            engine.create_task("")                      # titre manquant
        with self.assertRaises(ValueError):
            engine.create_task("x", task_type="NOPE")   # type inconnu
        with self.assertRaises(ValueError):
            engine.create_task("x", priority="MAX")     # priorité inconnue
        with self.assertRaises(ValueError):
            engine.create_task("x", dependencies=["bt_inconnue"])  # dépendance manquante
        engine.shutdown()

    def test_plan_mission_completes_with_real_tools(self) -> None:
        tmp = self.tmp
        core, engine = _engine(tmp)
        out = tmp / "out_plan.txt"
        engine.create_task(
            "Mission plan",
            task_type="GENERIC_AGENT",
            metadata={"plan": [
                {"shell": f"echo bonjour > {out}"},
                {"tool": "fs.read", "arguments": {"path": str(out), "max_lines": 5}},
            ]},
        )
        task_id = engine.list(limit=1)[0]["task_id"]
        ok = wait_until(lambda: engine.get(task_id)["status"] == COMPLETED, timeout=20)
        self.assertTrue(ok, engine.get(task_id))
        self.assertEqual(out.read_text(encoding="utf-8").strip(), "bonjour")
        self.assertTrue(engine.logs(task_id))

    def test_failed_tool_step_fails_the_mission(self) -> None:
        core, engine = _engine(self.tmp)
        engine.create_task(
            "Lecture impossible",
            task_type="GENERIC_AGENT",
            metadata={"plan": [{"tool": "fs.read",
                                "arguments": {"path": str(self.tmp / "absent.txt")}}]},
        )
        task_id = engine.list(limit=1)[0]["task_id"]
        ok = wait_until(lambda: engine.get(task_id)["status"] == FAILED, timeout=20)
        self.assertTrue(ok, engine.get(task_id))
        self.assertIn("Fichier introuvable", engine.get(task_id)["error"])

    def test_missing_llm_fails_with_real_error(self) -> None:
        core, engine = _engine(self.tmp)
        # Pas de plan et LLM absent : la boucle agentique doit ÉCHOUER avec
        # l'erreur réelle du composant, jamais produire un succès simulé.
        engine.create_task("Mission LLM", task_type="GENERIC_AGENT", description="fais ton travail")
        task_id = engine.list(limit=1)[0]["task_id"]
        ok = wait_until(lambda: engine.get(task_id)["status"] in (COMPLETED, FAILED), timeout=20)
        self.assertTrue(ok)
        row = engine.get(task_id)
        self.assertEqual(row["status"], FAILED)
        self.assertIn("Aucun fournisseur", row["error"])

    def test_resources_and_locks_are_released_at_end(self) -> None:
        core, engine = _engine(self.tmp, max_background=2)
        engine.create_task(
            "Ressources", task_type="GENERIC_AGENT", resources=["LLM_GPU", "CPU"],
            metadata={"plan": [{"shell": shell_sleep(1000)},
                               {"shell": f"echo fin > {self.tmp / 'fin.txt'}"}]})
        task_id = engine.list(limit=1)[0]["task_id"]
        wait_until(lambda: engine.get(task_id)["status"] == COMPLETED, timeout=20)
        for snap in engine.resources.snapshot().values():
            self.assertEqual(snap["used"], 0)
        self.assertEqual(engine.locks.snapshot(), [])

    def test_terminal_mission_cannot_be_touched(self) -> None:
        core, engine = _engine(self.tmp)
        task = engine.create_task("Terminée", task_type="GENERIC_AGENT", auto_submit=False)
        engine.store.set(task["task_id"], status=COMPLETED, result="ok", completed_at=time.time())
        with self.assertRaises(ValueError):
            engine.pause(task["task_id"])
        with self.assertRaises(ValueError):
            engine.resume_task(task["task_id"])
        with self.assertRaises(ValueError):
            engine.cancel(task["task_id"])
        engine.shutdown()


class EngineSchedulingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = new_temp_dir()
        self.addCleanup(cleanup_tree, self.tmp)

    def test_priority_ordering_of_starts(self) -> None:
        core, engine = _engine(self.tmp, max_background=4)
        recorder = EventRecorder(core, "task.started")
        low = engine.create_task("Basse", task_type="GENERIC_AGENT", priority="LOW",
                                 metadata={"plan": [{"shell": shell_sleep(900)}]})
        urgent = engine.create_task("Urgente", task_type="GENERIC_AGENT", priority="URGENT",
                                    metadata={"plan": [{"shell": shell_sleep(900)}]})
        wait_until(lambda: engine.get(low["task_id"])["status"] == RUNNING, timeout=20)
        wait_until(lambda: engine.get(urgent["task_id"])["status"] == RUNNING, timeout=20)
        self.assertEqual(recorder.first("task.started"), urgent["task_id"])
        wait_until(lambda: engine.get(low["task_id"])["status"] == COMPLETED, timeout=20)
        wait_until(lambda: engine.get(urgent["task_id"])["status"] == COMPLETED, timeout=20)
        engine.shutdown()

    def test_dependencies_block_then_release(self) -> None:
        core, engine = _engine(self.tmp, max_background=2)
        recorder = EventRecorder(core, "task.blocked")
        b = engine.create_task("Parent", task_type="GENERIC_AGENT",
                               metadata={"plan": [{"shell": shell_sleep(1800)}]})
        a = engine.create_task("Enfant", task_type="GENERIC_AGENT",
                               dependencies=[b["task_id"]],
                               metadata={"plan": [{"shell": f"echo ok > {self.tmp / 'enfant.txt'}"}]})
        # « Enfant » est d'abord bloqué par « Parent » : l'événement task.blocked
        # (transitoire, ensuite re-programmé) est plus fiable que l'état sondé.
        ok = wait_until(lambda: any(r.get("id") == a["task_id"]
                                    for r in recorder.records["task.blocked"]), timeout=20)
        self.assertTrue(ok, engine.get(a["task_id"]))
        wait_until(lambda: engine.get(b["task_id"])["status"] == COMPLETED, timeout=20)
        ok = wait_until(lambda: engine.get(a["task_id"])["status"] == COMPLETED, timeout=20)
        self.assertTrue(ok, engine.get(a["task_id"]))
        self.assertTrue((self.tmp / "enfant.txt").exists())

    def test_waiting_resource_serializes_llm_gpu(self) -> None:
        core, engine = _engine(self.tmp, max_background=2)
        recorder = EventRecorder(core, "task.waiting_resource")
        m1 = engine.create_task("GPU 1", task_type="GENERIC_AGENT", resources=["LLM_GPU"],
                                metadata={"plan": [{"shell": shell_sleep(1200)},
                                                   {"shell": f"echo a > {self.tmp / 'a.txt'}"}]})
        m2 = engine.create_task("GPU 2", task_type="GENERIC_AGENT", resources=["LLM_GPU"],
                                metadata={"plan": [{"shell": shell_sleep(1200)},
                                                   {"shell": f"echo b > {self.tmp / 'b.txt'}"}]})
        ok = wait_until(lambda: engine.get(m2["task_id"])["status"] == WAITING_RESOURCE, timeout=20)
        self.assertTrue(ok, engine.get(m2["task_id"]))
        self.assertGreaterEqual(recorder.count("task.waiting_resource"), 1)
        for t in (m1, m2):
            wait_until(lambda: engine.get(t["task_id"])["status"] == COMPLETED, timeout=30)
        engine.shutdown()


class EngineControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = new_temp_dir()
        self.addCleanup(cleanup_tree, self.tmp)

    def _slow_mission(self, engine: BackgroundTaskManager, *,
                      name: str = "Lente", extra: list[dict] | None = None):
        plan = [{"shell": shell_sleep(1400)}, {"shell": f"echo fin > {self.tmp / 'fin.txt'}"}]
        if extra:
            plan = extra + plan
        return engine.create_task(name, task_type="GENERIC_AGENT", metadata={"plan": plan})

    def test_pause_then_resume(self) -> None:
        core, engine = _engine(self.tmp)
        task = self._slow_mission(engine)
        wait_until(lambda: engine.get(task["task_id"])["status"] == RUNNING, timeout=20)
        engine.pause(task["task_id"], reason="pause test")
        wait_until(lambda: engine.get(task["task_id"])["status"] == PAUSED, timeout=10)
        engine.resume_task(task["task_id"])
        ok = wait_until(lambda: engine.get(task["task_id"])["status"] == COMPLETED, timeout=20)
        self.assertTrue(ok, engine.get(task["task_id"]))
        engine.shutdown()

    def test_cancel_a_running_mission(self) -> None:
        core, engine = _engine(self.tmp)
        task = self._slow_mission(engine)
        wait_until(lambda: engine.get(task["task_id"])["status"] == RUNNING, timeout=20)
        engine.cancel(task["task_id"], reason="annulée")
        ok = wait_until(lambda: engine.get(task["task_id"])["status"] == CANCELLED, timeout=20)
        self.assertTrue(ok, engine.get(task["task_id"]))
        self.assertFalse((self.tmp / "fin.txt").exists())
        engine.shutdown()

    def test_waiting_user_waits_for_input(self) -> None:
        core, engine = _engine(self.tmp)
        recorder = EventRecorder(core, "task.waiting_user")
        task = engine.create_task(
            "Question", task_type="GENERIC_AGENT",
            metadata={"plan": [{"ask_user": "On continue ?"},
                               {"shell": f"echo oui > {self.tmp / 'apres.txt'}"}]})
        ok = wait_until(lambda: engine.get(task["task_id"])["status"] == WAITING_USER, timeout=20)
        self.assertTrue(ok, engine.get(task["task_id"]))
        self.assertGreaterEqual(recorder.count("task.waiting_user"), 1)
        # La mission n'avance pas tant que l'utilisateur n'a pas répondu.
        time.sleep(0.5)
        self.assertEqual(engine.get(task["task_id"])["status"], WAITING_USER)
        engine.deliver_user_input(task["task_id"], "oui")
        ok = wait_until(lambda: engine.get(task["task_id"])["status"] == COMPLETED, timeout=20)
        self.assertTrue(ok, engine.get(task["task_id"]))
        self.assertTrue((self.tmp / "apres.txt").exists())
        engine.shutdown()


if __name__ == "__main__":
    unittest.main()