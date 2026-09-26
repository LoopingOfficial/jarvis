import threading
import time
import unittest

from jarvis.distributed.event_log import EventLog
from jarvis.distributed.task_store import TaskStore


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.events = EventLog()
        self.store = TaskStore(lease_seconds=0.03, max_attempts=3, event_sink=self.events.append, retry_backoff=[0.0])

    def test_claim_is_atomic_and_duplicate_completion_is_rejected(self):
        task = self.store.create("work", required_capabilities=["coding"])
        claimed = []

        def claim():
            claimed.append(self.store.claim("w", ["coding"]))

        threads = [threading.Thread(target=claim) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(1, len([item for item in claimed if item]))
        self.assertTrue(self.store.complete(task["task_id"], "w", {"ok": True}))
        self.assertFalse(self.store.complete(task["task_id"], "w", {"ok": True}))

    def test_expired_lease_requeues_and_invalid_owner_cannot_complete(self):
        task = self.store.create("work")
        self.assertIsNotNone(self.store.claim("w1", []))
        time.sleep(0.05)
        self.assertFalse(self.store.complete(task["task_id"], "w1", "late"))
        current = self.store.get(task["task_id"])
        self.assertEqual("ready", current["status"])
        self.assertEqual("TRANSIENT", current["error_type"])
        self.assertTrue(any(event["event"] == "LEASE_EXPIRED" for event in self.events.recent()))

    def test_dag_waits_for_all_dependencies(self):
        first = self.store.create("A")
        second = self.store.create("B")
        final = self.store.create("C", dependencies=[first["task_id"], second["task_id"]])
        self.assertIsNotNone(self.store.claim("w", []))
        self.assertTrue(self.store.complete(first["task_id"], "w", "a"))
        self.assertEqual("blocked", self.store.get(final["task_id"])["status"])
        self.assertIsNotNone(self.store.claim("w", []))
        self.assertTrue(self.store.complete(second["task_id"], "w", "b"))
        self.assertEqual("ready", self.store.get(final["task_id"])["status"])


if __name__ == "__main__":
    unittest.main()
