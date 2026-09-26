from __future__ import annotations

from .protocol import WorkerState, WorkerStatus, Task
from .queue import TaskQueue
from .registry import WorkerRegistry


class Scheduler:
    def __init__(
        self,
        registry: WorkerRegistry,
        queue: TaskQueue,
    ):
        self.registry = registry
        self.queue = queue

    @staticmethod
    def compatible(
        worker: WorkerState,
        task: Task,
    ) -> bool:
        return task.required_capabilities.issubset(
            worker.capabilities
        )

    @staticmethod
    def worker_score(worker: WorkerState) -> float:
        score = float(worker.priority)

        # Favorise les workers libres.
        score -= worker.running_tasks * 100

        # Petite pénalité réseau, sans laisser la latence
        # dominer la puissance/capacité du worker.
        if worker.latency_ms is not None:
            score -= worker.latency_ms / 100

        return score

    def claim_next(
        self,
        worker_id: str,
    ) -> Task | None:

        worker = self.registry.get(worker_id)

        if worker.status not in {
            WorkerStatus.ONLINE,
            WorkerStatus.BUSY,
        }:
            return None

        candidates = [
            task
            for task in self.queue.ready()
            if self.compatible(worker, task)
        ]

        if not candidates:
            return None

        task = candidates[0]

        claimed = self.queue.claim(
            task.task_id,
            worker.worker_id,
        )

        worker.running_tasks += 1
        worker.status = WorkerStatus.BUSY

        return claimed

    def best_worker_for(
        self,
        task: Task,
    ) -> WorkerState | None:

        candidates = [
            worker
            for worker in self.registry.online_workers()
            if self.compatible(worker, task)
        ]

        if not candidates:
            return None

        return max(
            candidates,
            key=self.worker_score,
        )

    def release_worker(
        self,
        worker_id: str,
    ) -> None:

        worker = self.registry.get(worker_id)

        worker.running_tasks = max(
            0,
            worker.running_tasks - 1,
        )

        if worker.running_tasks == 0:
            worker.status = WorkerStatus.ONLINE
