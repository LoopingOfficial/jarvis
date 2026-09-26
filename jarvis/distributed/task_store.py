from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional


class TaskStore:
    """
    Thread-safe in-memory task store for the VELKO coordinator.

    Lifecycle:
        pending/blocked -> ready -> running -> done
                                      |
                                      +-> ready (lease expiry / retry)
                                      |
                                      +-> failed (max attempts)
    """

    def __init__(
        self,
        lease_seconds: float = 30.0,
        max_attempts: int = 3,
        event_sink: Optional[Callable[..., Any]] = None,
        retry_backoff: Optional[List[float]] = None,
    ):
        self.lock = threading.RLock()
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.event_sink = event_sink
        self.retry_backoff = retry_backoff or [2.0, 5.0, 10.0]

    def _event(self, event: str, task: Optional[Dict[str, Any]] = None, **kwargs: Any) -> None:
        if self.event_sink is None:
            return
        self.event_sink(
            event,
            task_id=task.get("task_id") if task else None,
            attempt=task.get("attempts") if task else None,
            **kwargs,
        )

    def create(
        self,
        prompt: str,
        required_capabilities: Optional[List[str]] = None,
        dependencies: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:

        task_id = str(uuid.uuid4())

        task = {
            "task_id": task_id,
            "prompt": prompt,
            "required_capabilities": required_capabilities or [],
            "dependencies": dependencies or [],
            "metadata": metadata or {},
            "status": "pending",
            "assigned_worker": None,
            "attempts": 0,
            "lease_expires_at": None,
            "created_at": time.time(),
            "started_at": None,
            "completed_at": None,
            "progress": None,
            "result": None,
            "error": None,
            "last_error": None,
            "error_type": None,
            "retry_count": 0,
            "retry_after": None,
        }

        with self.lock:
            self.tasks[task_id] = task
            self._refresh_locked()
            self._event("TASK_CREATED", task)
            if task["status"] == "ready":
                self._event("TASK_READY", task)

            return dict(task)

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            self._expire_leases_locked()
            self._refresh_locked()

            task = self.tasks.get(task_id)
            return dict(task) if task else None

    def list(self) -> List[Dict[str, Any]]:
        with self.lock:
            self._expire_leases_locked()
            self._refresh_locked()

            return [
                dict(task)
                for task in self.tasks.values()
            ]

    def claim(
        self,
        worker_id: str,
        capabilities: List[str],
    ) -> Optional[Dict[str, Any]]:

        with self.lock:
            self._expire_leases_locked()
            self._refresh_locked()

            worker_capabilities = set(capabilities)

            candidates = sorted(
                (
                    task
                    for task in self.tasks.values()
                    if task["status"] == "ready"
                    and (task.get("retry_after") or 0) <= time.time()
                ),
                key=lambda task: task["created_at"],
            )

            for task in candidates:
                preferred_worker = task.get("metadata", {}).get("test_preferred_worker")
                if task.get("metadata", {}).get("test_mode") is True and preferred_worker and preferred_worker != worker_id:
                    continue
                required = set(
                    task.get("required_capabilities", [])
                )

                if not required.issubset(worker_capabilities):
                    continue

                task["status"] = "running"
                task["assigned_worker"] = worker_id
                task["attempts"] += 1
                task["started_at"] = time.time()
                task["lease_expires_at"] = (
                    time.time() + self.lease_seconds
                )
                task["error"] = None
                task["retry_after"] = None

                self._event("TASK_CLAIMED", task, worker_id=worker_id)
                self._event("TASK_STARTED", task, worker_id=worker_id)

                return dict(task)

            return None

    def renew(
        self,
        task_id: str,
        worker_id: str,
    ) -> bool:

        with self.lock:
            self._expire_leases_locked()
            task = self.tasks.get(task_id)

            if not task:
                return False

            if task["status"] != "running":
                return False

            if task["assigned_worker"] != worker_id:
                return False

            task["lease_expires_at"] = (
                time.time() + self.lease_seconds
            )

            self._event("LEASE_RENEWED", task, worker_id=worker_id)

            return True

    def progress(
        self,
        task_id: str,
        worker_id: str,
        progress: Any,
    ) -> bool:

        with self.lock:
            self._expire_leases_locked()
            task = self.tasks.get(task_id)

            if not self._owns_running_task(
                task,
                worker_id,
            ):
                return False

            task["progress"] = progress
            task["lease_expires_at"] = (
                time.time() + self.lease_seconds
            )
            self._event("TASK_PROGRESS", task, worker_id=worker_id, details={"progress": progress})

            return True

    def complete(
        self,
        task_id: str,
        worker_id: str,
        result: Any,
    ) -> bool:

        with self.lock:
            self._expire_leases_locked()
            task = self.tasks.get(task_id)

            if not self._owns_running_task(
                task,
                worker_id,
            ):
                return False

            task["status"] = "done"
            task["result"] = result
            task["completed_at"] = time.time()
            task["lease_expires_at"] = None
            task["progress"] = 100

            self._refresh_locked()
            self._event("TASK_COMPLETED", task, worker_id=worker_id)
            return True

    def fail(
        self,
        task_id: str,
        worker_id: str,
        error: str,
        error_type: str = "TERMINAL",
    ) -> bool:

        with self.lock:
            task = self.tasks.get(task_id)

            if not self._owns_running_task(
                task,
                worker_id,
            ):
                return False

            task["error"] = error
            task["last_error"] = error
            task["error_type"] = error_type
            task["assigned_worker"] = None
            task["lease_expires_at"] = None

            self._event("TASK_FAILED", task, worker_id=worker_id, details={"error": error, "error_type": error_type})
            if task["attempts"] >= self.max_attempts:
                task["status"] = "failed"
                task["completed_at"] = time.time()
            else:
                task["status"] = "ready"
                if error_type == "TRANSIENT":
                    task["retry_count"] += 1
                    delay_index = min(task["retry_count"] - 1, len(self.retry_backoff) - 1)
                    task["retry_after"] = time.time() + self.retry_backoff[delay_index]
                    self._event("TASK_RETRY", task, worker_id=worker_id, details={"backoff_seconds": self.retry_backoff[delay_index]})
                else:
                    task["retry_after"] = None

            self._refresh_locked()
            return True

    def _owns_running_task(
        self,
        task: Optional[Dict[str, Any]],
        worker_id: str,
    ) -> bool:

        return bool(
            task
            and task["status"] == "running"
            and task["assigned_worker"] == worker_id
        )

    def _expire_leases_locked(self) -> None:
        now = time.time()

        for task in self.tasks.values():
            if task["status"] != "running":
                continue

            expires = task.get("lease_expires_at")

            if expires is None or expires > now:
                continue

            previous_worker = task["assigned_worker"]

            task["assigned_worker"] = None
            task["lease_expires_at"] = None
            task["error"] = (
                "lease_expired"
                + (
                    f":{previous_worker}"
                    if previous_worker
                    else ""
                )
            )
            task["last_error"] = task["error"]
            task["error_type"] = "TRANSIENT"
            self._event("LEASE_EXPIRED", task, worker_id=previous_worker)

            if task["attempts"] >= self.max_attempts:
                task["status"] = "failed"
                task["completed_at"] = now
            else:
                task["status"] = "ready"
                task["retry_count"] += 1
                task["retry_after"] = now + self.retry_backoff[min(task["retry_count"] - 1, len(self.retry_backoff) - 1)]
                self._event("TASK_REQUEUED", task, worker_id=previous_worker)

    def _refresh_locked(self) -> None:
        for task in self.tasks.values():
            if task["status"] in {
                "running",
                "done",
                "failed",
            }:
                continue

            dependencies = task.get("dependencies", [])

            if not dependencies:
                task["status"] = "ready"
                if task.get("retry_after") is None:
                    self._event("TASK_READY", task)
                continue

            dependency_tasks = [
                self.tasks.get(dep)
                for dep in dependencies
            ]

            if any(dep is None for dep in dependency_tasks):
                task["status"] = "blocked"
                continue

            if any(
                dep["status"] == "failed"
                for dep in dependency_tasks
                if dep
            ):
                task["status"] = "blocked"
                task["error"] = "dependency_failed"
                continue

            if all(
                dep["status"] == "done"
                for dep in dependency_tasks
                if dep
            ):
                task["status"] = "ready"
                task["error"] = None
            else:
                task["status"] = "blocked"
