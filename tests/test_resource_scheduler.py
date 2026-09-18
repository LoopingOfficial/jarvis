"""Tests du ResourceScheduler et du LockRegistry du moteur multitâches."""
from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace

from jarvis.background_tasks import ResourceScheduler, LockRegistry

from .background_support import make_core, new_temp_dir, cleanup_tree, wait_until


class ResourceSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = new_temp_dir()
        self.addCleanup(cleanup_tree, self.tmp)
        self.res = ResourceScheduler(limits={"LLM_GPU": 1, "CPU": 2})

    def test_acquire_and_release(self) -> None:
        self.assertTrue(self.res.acquire("t1", ["LLM_GPU"]))
        snap = self.res.snapshot()["LLM_GPU"]
        self.assertEqual(snap["used"], 1)
        self.assertEqual(snap["free"], 0)
        self.res.release("t1", ["LLM_GPU"])
        self.assertEqual(self.res.snapshot()["LLM_GPU"]["free"], 1)

    def test_acquisition_is_atomic(self) -> None:
        self.assertTrue(self.res.acquire("t1", ["LLM_GPU", "CPU"]))
        # r2 exige LLM_GPU (plein) ET CPU : rien ne doit être pris.
        self.assertFalse(self.res.acquire("t2", ["LLM_GPU", "CPU"]))
        self.assertFalse(self.res.has("t2"))

    def test_capacity_bounds_concurrency(self) -> None:
        self.assertTrue(self.res.acquire("t1", ["LLM_GPU"]))
        self.assertFalse(self.res.acquire("t2", ["LLM_GPU"]))
        self.res.release("t1")
        self.assertTrue(self.res.acquire("t2", ["LLM_GPU"]))
        self.assertTrue(self.res.has("t2"))

    def test_blocking_acquire_waits_then_takes(self) -> None:
        events: list[str] = []
        fake_engine = SimpleNamespace(
            _enter_waiting_resource=lambda tid, r: events.append("enter"),
            _leave_waiting_resource=lambda tid: events.append("leave"),
            _checkpoint=lambda tid: None,
        )
        self.assertTrue(self.res.acquire("owner", ["LLM_GPU"]))
        done = threading.Event()
        holder: list[str] = []

        def reader() -> None:
            self.res.acquire_blocking("reader", ["LLM_GPU"], fake_engine)
            holder.append("reader")
            done.set()

        t = threading.Thread(target=reader)
        t.start()
        time.sleep(0.4)
        self.assertFalse(done.is_set())
        self.res.release("owner", ["LLM_GPU"])
        t.join(timeout=5)
        self.assertTrue(done.is_set())
        self.assertTrue(self.res.has("reader"))
        self.assertIn("enter", events)
        self.assertIn("leave", events)

    def test_normalize(self) -> None:
        self.assertEqual(ResourceScheduler.normalize(["LLM_GPU"]), [("LLM_GPU", 1)])
        self.assertEqual(ResourceScheduler.normalize([("CPU", 2)]), [("CPU", 2)])
        self.assertEqual(ResourceScheduler.normalize({"NETWORK": 2}), [("NETWORK", 2)])
        self.assertEqual(ResourceScheduler.normalize("LLM_GPU"), [("LLM_GPU", 1)])
        self.assertEqual(ResourceScheduler.normalize(None), [])

    def test_holders_and_release_all(self) -> None:
        self.res.acquire("t1", ["LLM_GPU", "CPU"])
        self.res.release_all("t1")
        self.assertFalse(self.res.has("t1"))

    def test_reacquire_by_same_holder_is_reentrant(self) -> None:
        # Une mission qui déclare LLM_GPU au départ puis l'acquiert de nouveau
        # autour de chaque inférence ne doit JAMAIS se bloquer sur elle-même :
        # sa part déjà détenue compte comme disponible (self-disponibilité).
        self.assertTrue(self.res.acquire("t1", ["LLM_GPU"]))
        self.assertTrue(self.res.acquire("t1", ["LLM_GPU"]))
        self.res.release_all("t1")
        self.assertFalse(self.res.has("t1"))
        # La ré-entrance ne rallonge pas la capacité : t2 reste bloqué.
        self.assertTrue(self.res.acquire("t1", ["LLM_GPU", "CPU"]))
        self.res.release("t1", ["CPU"])
        self.assertFalse(self.res.acquire("t2", ["LLM_GPU"]))

    def test_reentrant_shared_slot_is_refunded_per_holder(self) -> None:
        # t1 détient LLM_GPU (1/1) et CPU (1/2). Il ré-acquiert LLM_GPU :
        # sa part est remboursée → toujours 1 <= 1, mais il compte 2 slots.
        # t2 ne peut PAS prendre LLM_GPU, mais peut prendre CPU (2/2).
        self.assertTrue(self.res.acquire("t1", ["LLM_GPU", "CPU"]))
        self.assertTrue(self.res.acquire("t1", ["LLM_GPU"]))
        self.assertFalse(self.res.acquire("t2", ["LLM_GPU"]))
        self.assertTrue(self.res.acquire("t2", ["CPU"]))
        self.assertFalse(self.res.acquire("t3", ["CPU"]))
        self.res.release_all("t1")
        self.res.release_all("t2")
        self.assertTrue(self.res.acquire("t3", ["LLM_GPU"]))


class LockRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.locks = LockRegistry()

    def test_canonical_keys(self) -> None:
        p = self.locks.project("/tmp/demo")
        self.assertTrue(p.startswith("project:"))
        self.assertEqual(self.locks.key("database", "Main"), "database:main")
        self.assertEqual(self.locks.key("database", "  main  "), "database:main")

    def test_conflicting_mission_is_refused(self) -> None:
        self.assertTrue(self.locks.acquire("m1", [self.locks.project("/x")]))
        self.assertFalse(self.locks.acquire("m2", [self.locks.project("/x")]))
        self.assertEqual(self.locks.holding("m1"), [self.locks.project("/x")])

    def test_release_frees_the_lock(self) -> None:
        name = self.locks.file("/x/y.txt")
        self.assertTrue(self.locks.acquire("m1", [name]))
        self.locks.release("m1")
        self.assertTrue(self.locks.acquire("m2", [name]))

    def test_release_all_and_snapshot(self) -> None:
        self.locks.acquire("m1", [self.locks.git("/repo"), self.locks.project("/x")])
        self.assertEqual(len(self.locks.snapshot()), 2)
        self.locks.release_all("m1")
        self.assertEqual(self.locks.snapshot(), [])

    def test_lock_names_include_workspace(self) -> None:
        names = self.locks.lock_names_for(["file:/a/b.txt"], workspace="/proj")
        self.assertIn(self.locks.project("/proj"), names)
        self.assertIn(self.locks.file("/a/b.txt"), names)
        # dédupliqué
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()