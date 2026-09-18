"""Intégration du moteur de missions multitâches avec le VRAI Core JARVIS.

N'utilise AUCUN stub : le vrai JarvisCore (base SQLite réelle, gestionnaire
de permissions réel, VRAI SecureToolRunner et VRAI registre d'outils), le vrai
événementiel, la vraie Mission Control, le vrai serveur HTTP.

Pré-requis : interpréteur du projet (.venv) car le coffre Windows (keyring)
est nécessaire à l'instanciation réelle de JarvisCore.
"""
from __future__ import annotations

import http.client
import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from jarvis.background_tasks import COMPLETED, FAILED, PAUSED, QUEUED, RUNNING, WAITING_USER
from jarvis.core import JarvisCore
from jarvis.mission_control import MissionControl
from jarvis.server import create_server

from .background_support import EventRecorder, cleanup_tree, new_temp_dir, shell_sleep, wait_until


class RealCoreBackgroundTests(unittest.TestCase):
    max_workers_ctx = None

    def setUp(self) -> None:
        self.tmp = new_temp_dir()
        self.addCleanup(cleanup_tree, self.tmp)
        self.core = self._fresh_core()
        self.addCleanup(self._stop_core, self.core)
        self.engine = self.core.background

    # -- helpers -----------------------------------------------------------
    def _fresh_core(self) -> JarvisCore:
        core = JarvisCore(db_path=str(self.tmp / "real_core.db"))
        core.start_background()
        return core

    def _stop_core(self, core: JarvisCore) -> None:
        try:
            core.shutdown()
        except Exception:
            pass
        try:
            core.db.close()
        except Exception:
            pass

    def _mission(self, title: str, **kwargs):
        return self.engine.create_task(title, **kwargs)


class RealCoreSingleMissionTests(RealCoreBackgroundTests):
    def test_core_to_result_full_pipeline(self) -> None:
        token = f"mission-{time.time_ns()}"
        out = self.tmp / "out.txt"
        task = self._mission("Pipeline réel", task_type="GENERIC_AGENT",
                             metadata={"plan": [{"shell": shell_sleep(300)},
                                                {"shell": f"echo {token} > {out}"}]})
        tid = task["task_id"]
        self.assertTrue(wait_until(lambda: self.engine.get(tid)["status"] == COMPLETED,
                                   timeout=30), self.engine.get(tid))
        row = self.engine.get(tid)
        self.assertIn("Plan exécuté", row["result"])
        self.assertTrue(out.exists(), "le fichier attendu n'a pas été produit")
        self.assertEqual(out.read_text(encoding="utf-8").strip(), token)
        self.assertLessEqual(self.engine.stats()["workers"]["active"], 0)

    def test_unknown_task_queried_returns_none(self) -> None:
        self.assertIsNone(self.engine.get("bt_inconnue_xyz"))


class RealCoreMissionControlTests(RealCoreBackgroundTests):
    def test_events_reach_mission_control_and_mission_completes(self) -> None:
        mc = MissionControl(self.core.events, db=self.core.db)
        recorder = EventRecorder(self.core, "task.created", "task.started",
                                 "task.waiting_user", "task.resumed", "task.completed")
        out = self.tmp / "apres.txt"
        task = self._mission(
            "Mission MC",
            task_type="GENERIC_AGENT",
            metadata={"plan": [{"ask_user": "On continue ?"},
                               {"shell": f"echo oui > {out}"}]},
        )
        tid = task["task_id"]
        self.assertTrue(wait_until(lambda: self.engine.get(tid)["status"] == WAITING_USER,
                                   timeout=30), self.engine.get(tid))
        self.engine.deliver_user_input(tid, "oui")
        self.assertTrue(wait_until(lambda: self.engine.get(tid)["status"] == COMPLETED,
                                   timeout=30), self.engine.get(tid))

        emitted = {e["type"] for e in recorder.records_events}
        for expected in ("task.created", "task.started", "task.waiting_user",
                         "task.resumed", "task.completed"):
            self.assertIn(expected, emitted, f"événement {expected} jamais émis")

        missions = mc.history("ALL")["missions"]
        by_id = {m["id"]: m for m in missions}
        self.assertIn(tid, by_id, "la mission est absente de Mission Control")
        self.assertEqual(by_id[tid]["state"], COMPLETED)
        self.assertFalse(by_id[tid].get("active"))

    def test_failed_mission_is_visible_in_mission_control(self) -> None:
        mc = MissionControl(self.core.events, db=self.core.db)
        task = self._mission("Échec MC", task_type="GENERIC_AGENT",
                             metadata={"plan": [{"shell": "cd /n'existe/pas && echo x > /tmp/zz"}]})
        tid = task["task_id"]
        self.assertTrue(wait_until(lambda: self.engine.get(tid)["status"] == FAILED,
                                   timeout=30), self.engine.get(tid))
        by_id = {m["id"]: m for m in mc.history("ALL")["missions"]}
        self.assertIn(tid, by_id)
        self.assertEqual(by_id[tid]["state"], FAILED)


class RealCoreParallelTests(RealCoreBackgroundTests):
    def test_four_parallel_missions_isolated(self) -> None:
        tokens = [f"p{i}-{time.time_ns()}" for i in range(4)]
        tids: list[str] = []
        for i, token in enumerate(tokens):
            out = self.tmp / f"p{i}.txt"
            tids.append(self._mission(
                f"Parallèle {i}", task_type="GENERIC_AGENT",
                priority="HIGH" if i % 2 else "NORMAL",
                metadata={"plan": [{"shell": shell_sleep(1200)},
                                   {"shell": f"echo {token} > {out}"}]},
            )["task_id"])

        max_concurrent, pending = 0, set(tids)
        deadline = time.time() + 40.0
        while time.time() < deadline and pending:
            max_concurrent = max(max_concurrent,
                                 sum(1 for t in pending if self.engine.get(t)["status"] == RUNNING))
            pending -= {t for t in pending if self.engine.get(t)["status"] == COMPLETED}
            time.sleep(0.02)
        self.assertEqual(pending, set(), {t: self.engine.get(t) for t in pending})
        self.assertGreaterEqual(max_concurrent, 2)

        for i, token in enumerate(tokens):
            self.assertEqual((self.tmp / f"p{i}.txt").read_text(encoding="utf-8").strip(), token)

        for i, tid in enumerate(tids):
            blob = " ".join(l["message"] for l in self.engine.logs(tid, limit=100))
            self.assertEqual(blob.count(token := tokens[i]), 1 if token in blob else 0)
        for snap in self.engine.resources.snapshot().values():
            self.assertEqual(snap["used"], 0)
        self.assertEqual(self.engine.locks.snapshot(), [])

    def test_llm_gpu_serializes_running_tasks(self) -> None:
        recorder = EventRecorder(self.core, "task.waiting_resource")
        t1 = self._mission("GPU A", task_type="GENERIC_AGENT", resources=["LLM_GPU"],
                           metadata={"plan": [{"shell": shell_sleep(900)},
                                              {"shell": f"echo a > {self.tmp / 'a.txt'}"}]})
        t2 = self._mission("GPU B", task_type="GENERIC_AGENT", resources=["LLM_GPU"],
                           metadata={"plan": [{"shell": shell_sleep(900)},
                                              {"shell": f"echo b > {self.tmp / 'b.txt'}"}]})
        both, pending = True, {t1["task_id"], t2["task_id"]}
        deadline = time.time() + 40.0
        while time.time() < deadline and pending:
            statuses = {t: self.engine.get(t)["status"] for t in pending}
            running = {t for t, s in statuses.items() if s == RUNNING}
            self.assertLessEqual(len(running), 1, "deux missions LLM_GPU en RUNNING en même temps")
            pending -= {t for t in pending if statuses[t] == COMPLETED}
            time.sleep(0.02)
        self.assertEqual(pending, set())
        waiting = [e for e in recorder.records_events if e["type"] == "task.waiting_resource"]
        self.assertGreaterEqual(len(waiting), 1, "le tâche attendue n'a jamais signalé WAITING_RESOURCE")
        self.assertTrue((self.tmp / "a.txt").exists())
        self.assertTrue((self.tmp / "b.txt").exists())


class RealCoreRestartRecoveryTests(RealCoreBackgroundTests):
    def test_restart_recovery(self) -> None:
        done = self._mission("Terminée", task_type="GENERIC_AGENT",
                             metadata={"plan": [{"shell": f"echo done > {self.tmp / 'done.txt'}"}]})["task_id"]
        self.assertTrue(wait_until(lambda: self.engine.get(done)["status"] == COMPLETED, timeout=30))

        queued = self._mission("Fi programmée", task_type="GENERIC_AGENT", auto_submit=False,
                               metadata={"plan": [{"shell": f"echo q > {self.tmp / 'q.txt'}"}]})["task_id"]
        self.assertEqual(self.engine.get(queued)["status"], QUEUED)

        # Simule une mission « au milieu de l'air » quand JARVIS est tombé :
        # l'état RUNNING est posé en base sans worker vivant (le worker est la
        # partie qui disparaît dans un crash).
        running = self._mission("En cours crash", task_type="GENERIC_AGENT", auto_submit=False,
                                metadata={"plan": [{"shell": shell_sleep(1200)}]})["task_id"]
        self.engine.store.set(running, status=RUNNING, started_at=time.time())

        # Redémarrage : le vieux Core meurt proprement (son scheduler n'a aucun
        # worker vivant), puis un NOUVEAU Core rouvre la même base.
        self._stop_core(self.core)
        self.core.db.close()
        self.core = JarvisCore(db_path=str(self.tmp / "real_core.db"))
        self.addCleanup(self._stop_core, self.core)
        self.engine = self.core.background
        self.core.start_background()

        self.assertEqual(self.engine.get(running)["status"], FAILED)
        self.assertIn("redémarrage", self.engine.get(running)["error"])
        self.assertTrue(wait_until(lambda: self.engine.get(queued)["status"] == COMPLETED, timeout=30))
        self.assertEqual(self.engine.get(done)["status"], COMPLETED)
        self.assertTrue((self.tmp / "q.txt").exists())

    def test_restart_waiting_user_resume_restarts_instead_of_stuck_running(self) -> None:
        # Mission en attente d'une réponse quand JARVIS tombe : son état
        # WAITING_USER survit en base, mais son worker a disparu. Reprendre
        # cette mission ne doit PAS créer un fantôme RUNNING sans exécutant :
        # elle doit être relancée (et re-demander si nécessaire).
        waiting_id = self._mission("Attente post-crash", task_type="GENERIC_AGENT",
                                   auto_submit=False,
                                   metadata={"plan": [
                                       {"ask_user": "On continue ?"},
                                       {"shell": f"echo ok > {self.tmp / 'ok.txt'}"},
                                   ]})["task_id"]
        self.assertEqual(self.engine.get(waiting_id)["status"], QUEUED)
        self.engine.store.set(waiting_id, status=WAITING_USER, note="En attente (crash simulé).")

        # Redémarrage : nouvel engine sur la même base, aucun worker vivant.
        self._stop_core(self.core)
        self.core.db.close()
        self.core = JarvisCore(db_path=str(self.tmp / "real_core.db"))
        self.addCleanup(self._stop_core, self.core)
        self.engine = self.core.background
        self.core.start_background()

        self.assertEqual(self.engine.get(waiting_id)["status"], WAITING_USER)
        # La reprise n'a pas le droit de rester piégée en RUNNING ; l'entrée
        # fournie à un worker mort est explicitement ignorée (relance propre).
        self.engine.resume_task(waiting_id, user_input="pré-crash")
        self.assertTrue(wait_until(lambda: self.engine.get(waiting_id)["status"] == WAITING_USER,
                                   timeout=30), "mission relancée, elle re-demande")
        # La réponse utile arrive APRÈS le redémarrage : mission terminée.
        self.engine.resume_task(waiting_id, user_input="ok")
        self.assertTrue(wait_until(lambda: self.engine.get(waiting_id)["status"] == COMPLETED, timeout=30))
        self.assertTrue((self.tmp / "ok.txt").exists())


class RealCoreApiTests(RealCoreBackgroundTests):
    def _start_server(self):
        self.server = create_server(self.core, port=0)
        port = self.server.socket.getsockname()[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{port}"
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass

    def _request(self, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_api_background_flow(self) -> None:
        self._start_server()

        # create
        status, payload = self._request("POST", "/api/background/tasks",
                                        {"title": "Mission API", "task_type": "GENERIC_AGENT",
                                         "metadata": {"plan": [{"shell": "echo coucou > nul"}]}})
        self.assertEqual(status, 200)
        task = payload["task"]
        tid = task["task_id"]
        self.assertTrue(payload["ok"])

        # list
        status, payload = self._request("GET", "/api/background/tasks")
        self.assertEqual(status, 200)
        self.assertIn(tid, {t["task_id"] for t in payload["tasks"]})
        self.assertIn("stats", payload)

        # get
        status, payload = self._request("GET", f"/api/background/tasks/{tid}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["task"]["task_id"], tid)

        # pause / resume / cancel sur une mission en attente (auto_submit=False)
        waiting = self._mission("Quiescente", task_type="GENERIC_AGENT", auto_submit=False,
                                metadata={"plan": [{"shell": "echo x > nul"}]})["task_id"]
        status, payload = self._request("POST", f"/api/background/tasks/{waiting}/pause", {"reason": "test"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertIn(self.engine.get(waiting)["status"], (QUEUED, PAUSED))
        status, payload = self._request("POST", f"/api/background/tasks/{waiting}/resume", {})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        status, payload = self._request("POST", f"/api/background/tasks/{waiting}/cancel", {"reason": "nope"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

        # logs + artifacts
        status, payload = self._request("GET", f"/api/background/tasks/{tid}/logs")
        self.assertEqual(status, 200)
        self.assertIn("logs", payload)
        status, payload = self._request("GET", f"/api/background/tasks/{tid}/artifacts")
        self.assertEqual(status, 200)
        self.assertIn("artifacts", payload)

        # waiting_user -> input
        ask = self._mission("Question", task_type="GENERIC_AGENT",
                            metadata={"plan": [{"ask_user": "Ok ?"}]})["task_id"]
        self.assertTrue(wait_until(lambda: self.engine.get(ask)["status"] == WAITING_USER, timeout=30))
        status, payload = self._request("POST", f"/api/background/tasks/{ask}/input", {"user_input": "go"})
        self.assertEqual(status, 200)
        self.assertTrue(wait_until(
            lambda: self.engine.get(ask)["status"] in (COMPLETED, FAILED), timeout=30))
        self.assertEqual(self.engine.get(ask)["status"], COMPLETED)

        # resources + locks
        status, payload = self._request("GET", "/api/background/resources")
        self.assertEqual(status, 200)
        self.assertIn("resources", payload)
        status, payload = self._request("GET", "/api/background/locks")
        self.assertEqual(status, 200)
        self.assertIn("locks", payload)

    def test_api_unknown_task(self) -> None:
        self._start_server()
        status, payload = self._request("GET", "/api/background/tasks/bt_inconnue")
        self.assertEqual(status, 404)
        self.assertFalse(payload["ok"])


if __name__ == "__main__":
    unittest.main()