from __future__ import annotations

import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .protocol import WorkerState, WorkerStatus


class WorkerRegistry:

    def __init__(self, config_path: str | Path | None = None):
        if config_path is None:
            config_path = Path(__file__).with_name("config.json")

        self.config_path = Path(config_path)

        with self.config_path.open(
            "r",
            encoding="utf-8"
        ) as f:
            config = json.load(f)

        self.cluster_name = config["cluster_name"]
        self.leader = config["leader"]

        self.workers: dict[str, WorkerState] = {}

        for worker_id, data in config["workers"].items():
            self.workers[worker_id] = WorkerState(
                worker_id=worker_id,
                name=data["name"],
                role=data["role"],
                os=data["os"],
                ollama_url=data["ollama_url"].rstrip("/"),
                model=data["model"],
                priority=int(data.get("priority", 50)),
                capabilities=set(data.get("capabilities", [])),
            )

    def get(self, worker_id: str) -> WorkerState:
        return self.workers[worker_id]

    def online_workers(self) -> list[WorkerState]:
        return [
            worker
            for worker in self.workers.values()
            if worker.status in {
                WorkerStatus.ONLINE,
                WorkerStatus.BUSY,
            }
        ]

    def _healthcheck(
        self,
        worker: WorkerState,
        timeout: float,
    ) -> WorkerState:

        started = time.perf_counter()

        try:
            request = urllib.request.Request(
                f"{worker.ollama_url}/api/tags",
                method="GET",
            )

            with urllib.request.urlopen(
                request,
                timeout=timeout,
            ) as response:
                payload = json.loads(response.read())

            latency = (
                time.perf_counter() - started
            ) * 1000

            models = [
                model.get("name", "")
                for model in payload.get("models", [])
            ]

            worker.available_models = models
            worker.latency_ms = latency
            worker.last_heartbeat = time.time()
            worker.error = None

            if worker.model in models:
                worker.status = WorkerStatus.ONLINE
            else:
                worker.status = WorkerStatus.OFFLINE
                worker.error = (
                    f"Model {worker.model!r} unavailable"
                )

        except Exception as exc:
            worker.status = WorkerStatus.OFFLINE
            worker.error = str(exc)
            worker.latency_ms = None
            worker.available_models = []

        return worker

    def healthcheck_all(
        self,
        timeout: float = 3.0,
    ) -> list[WorkerState]:

        with ThreadPoolExecutor(
            max_workers=len(self.workers)
        ) as executor:

            futures = {
                executor.submit(
                    self._healthcheck,
                    worker,
                    timeout,
                ): worker.worker_id
                for worker in self.workers.values()
            }

            for future in as_completed(futures):
                future.result()

        return list(self.workers.values())
