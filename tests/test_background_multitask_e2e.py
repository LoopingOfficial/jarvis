"""Test de bout en bout : 4 missions multitâches réelles en parallèle.

Vérifie la propriété centrale du moteur : plusieurs missions s'exécutent VRAIMENT
en même temps (pool de workers), dans des contextes isolés, avec les vrais outils
JARVIS (terminal.run, fs.read) et sans que les ressources/verrous fuient.
"""
from __future__ import annotations

import time
import unittest
import uuid

from jarvis.background_tasks import COMPLETED, RUNNING, BackgroundTaskManager

from .background_support import make_core, new_temp_dir, cleanup_tree, shell_sleep, wait_until


def _uid() -> str:
    return uuid.uuid4().hex[:6]


class BackgroundMultitaskE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = new_temp_dir()
        self.addCleanup(cleanup_tree, self.tmp)
        self.tokens = [f"mission-{i}-{_uid()}" for i in range(4)]

    def tearDown(self) -> None:
        try:
            self.engine.shutdown()
            self.core.db.close()
        except Exception:
            pass

    def _start_parallel_missions(self) -> list[str]:
        task_ids: list[str] = []
        for i, token in enumerate(self.tokens):
            out = self.tmp / f"out{i}.txt"
            task = self.engine.create_task(
                f"Mission parallèle {i}",
                task_type="GENERIC_AGENT",
                priority="HIGH" if i % 2 == 0 else "NORMAL",
                metadata={"plan": [
                    {"shell": shell_sleep(1300)},
                    {"shell": f"echo {token} > {out}"},
                    {"tool": "fs.read", "arguments": {"path": str(out), "max_lines": 5}},
                ]},
            )
            task_ids.append(task["task_id"])
        return task_ids

    def test_four_missions_run_in_parallel_isolated(self) -> None:
        self.core = make_core(self.tmp, max_background=4)
        self.engine = BackgroundTaskManager(self.core)
        self.engine.start()
        tids = self._start_parallel_missions()

        # 1-2) Elles atteignent toutes l'état terminal, en échantillonnant la
        #      concurrence réelle pendant la progression.
        max_concurrent, pending = 0, set(tids)
        deadline = time.time() + 40.0
        while time.time() < deadline and pending:
            count = sum(1 for t in pending if self.engine.get(t)["status"] == RUNNING)
            max_concurrent = max(max_concurrent, count)
            done = {t for t in pending if self.engine.get(t)["status"] == COMPLETED}
            pending -= done
            time.sleep(0.02)
        self.assertEqual(pending, set(), {t: self.engine.get(t) for t in pending})
        self.assertGreaterEqual(max_concurrent, 2)

        # 3) Chaque mission a écrit SON fichier, avec SON contenu.
        for i, token in enumerate(self.tokens):
            path = self.tmp / f"out{i}.txt"
            self.assertEqual(path.read_text(encoding="utf-8").strip(), token)

        # 4) Les contextes sont isolés : les logs d'une mission ne contiennent
        #    jamais la sortie d'une autre.
        for i, tid in enumerate(tids):
            blob = " ".join(l["message"] for l in self.engine.logs(tid, limit=100))
            self.assertIn(self.tokens[i], blob)
            for j in range(4):
                if j != i:
                    self.assertNotIn(self.tokens[j], blob)

        # 5) Le pool est revenu à zéro, ressources et verrous libérés.
        self.assertLessEqual(self.engine.stats()["workers"]["active"], 1)
        for snap in self.engine.resources.snapshot().values():
            self.assertEqual(snap["used"], 0)
        self.assertEqual(self.engine.locks.snapshot(), [])

    def test_e2e_snapshot_reports_running_missions(self) -> None:
        self.core = make_core(self.tmp, max_background=4)
        self.engine = BackgroundTaskManager(self.core)
        self.engine.start()
        tid = self.engine.create_task(
            "Observée", task_type="GENERIC_AGENT",
            metadata={"plan": [{"shell": shell_sleep(1200)},
                               {"shell": f"echo x > {self.tmp / 'obs.txt'}"}]})["task_id"]
        wait_until(lambda: self.engine.get(tid)["status"] == RUNNING, timeout=20)
        snap = self.engine.snapshot()
        self.assertEqual([t["task_id"] for t in snap["running"]], [tid])
        self.assertIn("resources", snap)
        self.assertIn("locks", snap)


if __name__ == "__main__":
    unittest.main()