import unittest

from jarvis.distributed.coordinator_service import ClusterState


class ClusterStateTests(unittest.TestCase):

    def test_snapshot_returns_all_registered_workers(self):
        state = ClusterState()

        state.register({
            "worker_id": "rtx3080",
            "hostname": "DESKTOP-BPP383L",
            "os": "windows",
            "capabilities": ["llm", "gpu"],
            "model": "qwen3.5:9b",
        })

        state.register({
            "worker_id": "gtx1080",
            "hostname": "DESKTOP-7MBCM4V",
            "os": "windows",
            "capabilities": ["llm", "gpu"],
            "model": "qwen3.5:9b",
        })

        snapshot = state.snapshot()
        workers = snapshot["workers"]

        self.assertEqual(
            {"rtx3080", "gtx1080"},
            set(workers),
        )
        self.assertEqual(
            "online",
            workers["rtx3080"]["status"],
        )
        self.assertEqual(
            "online",
            workers["gtx1080"]["status"],
        )


if __name__ == "__main__":
    unittest.main()
