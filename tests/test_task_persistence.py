"""Tests de la persistance SQLite (TaskStore) et de la reprise après crash."""
from __future__ import annotations

import unittest

from jarvis.background_tasks import (COMPLETED, FAILED, QUEUED, RUNNING,
                                     BackgroundTaskManager, TaskStore)

from .background_support import make_core, new_temp_dir, cleanup_tree


class TaskStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = new_temp_dir()
        self.addCleanup(cleanup_tree, self.tmp)
        self.core = make_core(self.tmp)
        self.store = TaskStore(self.core.db)

    def _row(self, task_id: str = "bt_x1", status: str = QUEUED) -> dict:
        return {
            "task_id": task_id, "mission_id": task_id, "title": "Mission test",
            "description": "desc", "task_type": "GENERIC_AGENT", "priority": "NORMAL",
            "status": status, "agent": "jarvis", "workspace": "",
            "resources": ["CPU"], "dependencies": [], "context": {"a": 1},
            "metadata": {"plan": [{"shell": "echo ok"}]}, "step": "", "note": "",
            "result": "", "error": "", "progress": 0.0,
            "created_at": 0.0, "started_at": None, "updated_at": 0.0, "completed_at": None,
        }

    def test_create_get_set_list_count(self) -> None:
        self.store.create(self._row())
        row = self.store.get("bt_x1")
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], QUEUED)
        self.assertEqual(row["resources"], ["CPU"])          # JSON décodé
        self.assertEqual(row["context"], {"a": 1})
        self.assertEqual(row["metadata"]["plan"][0]["shell"], "echo ok")
        self.store.set("bt_x1", status=RUNNING, progress=0.5)
        self.assertEqual(self.store.status("bt_x1"), RUNNING)
        self.assertAlmostEqual(self.store.get("bt_x1")["progress"], 0.5)
        listings = self.store.list(status=RUNNING)
        self.assertEqual([t["task_id"] for t in listings], ["bt_x1"])
        self.assertEqual(self.store.counts()[RUNNING], 1)
        self.assertEqual(self.store.counts()["total"], 1)

    def test_logs_and_artifacts(self) -> None:
        self.store.create(self._row())
        self.store.add_log("bt_x1", "première ligne")
        self.store.add_log("bt_x1", "seconde ligne", level="error", data={"k": 2})
        logs = self.store.logs("bt_x1")
        self.assertEqual([l["message"] for l in logs], ["première ligne", "seconde ligne"])
        self.assertEqual(logs[1]["data"], {"k": 2})
        art = self.store.add_artifact("bt_x1", "/tmp/out.txt", "file", metadata={"step": 1})
        self.assertEqual(art["name"], "out.txt")
        artifacts = self.store.artifacts("bt_x1")
        self.assertEqual(artifacts[0]["metadata"], {"step": 1})

    def test_unknown_task(self) -> None:
        self.assertIsNone(self.store.get("bt_zzz"))
        self.assertIsNone(self.store.status("bt_zzz") and None)
        self.assertEqual(self.store.logs("bt_zzz"), [])
        self.assertEqual(self.store.artifacts("bt_zzz"), [])


class CrashRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = new_temp_dir()
        self.addCleanup(cleanup_tree, self.tmp)

    def _make(self) -> tuple[BackgroundTaskManager, dict]:
        core = make_core(self.tmp)
        engine = BackgroundTaskManager(core)
        return engine, core

    def test_running_task_is_marked_interrupted(self) -> None:
        engine, core = self._make()
        engine.create_task("En cours", task_type="GENERIC_AGENT", auto_submit=False)
        task_id = engine.list(limit=1)[0]["task_id"]
        engine.store.set(task_id, status=RUNNING)
        engine._refresh_after_restart()
        row = engine.get(task_id)
        self.assertEqual(row["status"], FAILED)
        self.assertIn("redémarrage", row["error"])
        self.assertTrue(engine.logs(task_id))

    def test_completed_and_queued_are_preserved(self) -> None:
        engine, _ = self._make()
        engine.create_task("Finie", task_type="GENERIC_AGENT", auto_submit=False)
        done_id = engine.list(limit=2)[0]["task_id"]
        engine.store.set(done_id, status=COMPLETED, result="ok", completed_at=0.0)
        engine.create_task("En file", task_type="GENERIC_AGENT", auto_submit=False)
        queued_id = [t for t in engine.list(limit=2) if t["task_id"] != done_id][0]["task_id"]
        engine._refresh_after_restart()
        self.assertEqual(engine.get(done_id)["status"], COMPLETED)
        self.assertEqual(engine.get(queued_id)["status"], QUEUED)


if __name__ == "__main__":
    unittest.main()