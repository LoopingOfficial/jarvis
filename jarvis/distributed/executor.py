from __future__ import annotations

import json
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from .protocol import TaskResult, WorkerStatus
from .queue import TaskQueue
from .registry import WorkerRegistry
from .scheduler import Scheduler


class DistributedExecutor:
    def __init__(
        self,
        registry: WorkerRegistry,
        queue: TaskQueue,
        scheduler: Scheduler,
    ):
        self.registry = registry
        self.queue = queue
        self.scheduler = scheduler

        self._print_lock = threading.Lock()

    def _log(self, message: str) -> None:
        with self._print_lock:
            print(message, flush=True)

    def _execute_ollama(
        self,
        worker_id: str,
        task,
        timeout: float = 180.0,
    ) -> TaskResult:

        worker = self.registry.get(worker_id)

        payload = {
            "model": worker.model,
            "messages": [
                {
                    "role": "user",
                    "content": task.prompt,
                }
            ],
            "stream": False,
            "think": False,
            "keep_alive": "10m",
        }

        data = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(
            f"{worker.ollama_url}/api/chat",
            data=data,
            headers={
                "Content-Type": "application/json",
            },
            method="POST",
        )

        started = time.perf_counter()

        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout,
            ) as response:
                result = json.loads(response.read())

            duration = time.perf_counter() - started

            content = (
                result
                .get("message", {})
                .get("content", "")
            )

            eval_count = result.get("eval_count", 0)
            eval_duration = result.get("eval_duration", 0)

            tokens_per_second = 0.0

            if eval_count and eval_duration:
                tokens_per_second = (
                    eval_count /
                    (eval_duration / 1_000_000_000)
                )

            return TaskResult(
                task_id=task.task_id,
                worker_id=worker_id,
                success=True,
                content=content,
                duration=duration,
                tokens_per_second=tokens_per_second,
            )

        except Exception as exc:
            return TaskResult(
                task_id=task.task_id,
                worker_id=worker_id,
                success=False,
                duration=time.perf_counter() - started,
                error=str(exc),
            )

    def _worker_loop(self, worker_id: str) -> int:
        completed = 0

        while True:
            task = self.scheduler.claim_next(worker_id)

            if task is None:
                return completed

            self._log(
                f"[START] {worker_id:<10} "
                f"task={task.task_id[:8]} "
                f"{task.prompt[:55]}"
            )

            result = self._execute_ollama(
                worker_id,
                task,
            )

            if result.success:
                self.queue.complete(
                    task.task_id,
                    result,
                )

                completed += 1

                self._log(
                    f"[DONE ] {worker_id:<10} "
                    f"task={task.task_id[:8]} "
                    f"{result.duration:.2f}s "
                    f"{result.tokens_per_second:.1f} tok/s"
                )

            else:
                self.queue.requeue(task.task_id)

                self._log(
                    f"[FAIL ] {worker_id:<10} "
                    f"task={task.task_id[:8]} "
                    f"{result.error}"
                )

                # Le worker ayant échoué est retiré de cette
                # exécution. La tâche redevient disponible
                # pour un autre worker.
                worker = self.registry.get(worker_id)
                worker.status = WorkerStatus.OFFLINE

            self.scheduler.release_worker(worker_id)

            if not result.success:
                return completed

    def run(self) -> dict[str, int]:
        online = [
            worker.worker_id
            for worker in self.registry.online_workers()
        ]

        if not online:
            raise RuntimeError("No online workers")

        results: dict[str, int] = {}

        with ThreadPoolExecutor(
            max_workers=len(online)
        ) as pool:

            futures = {
                pool.submit(
                    self._worker_loop,
                    worker_id,
                ): worker_id
                for worker_id in online
            }

            for future in as_completed(futures):
                worker_id = futures[future]
                results[worker_id] = future.result()

        return results
