from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, List, Optional


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
    ):
        self.lock = threading.RLock()
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts

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
        }

        with self.lock:
            self.tasks[task_id] = task
            self._refresh_locked()

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
                ),
                key=lambda task: task["created_at"],
            )

            for task in candidates:
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

                return dict(task)

            return None

    def renew(
        self,
        task_id: str,
        worker_id: str,
    ) -> bool:

        with self.lock:
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

            return True

    def progress(
        self,
        task_id: str,
        worker_id: str,
        progress: Any,
    ) -> bool:

        with self.lock:
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

            return True

    def complete(
        self,
        task_id: str,
        worker_id: str,
        result: Any,
    ) -> bool:

        with self.lock:
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
            return True

    def fail(
        self,
        task_id: str,
        worker_id: str,
        error: str,
    ) -> bool:

        with self.lock:
            task = self.tasks.get(task_id)

            if not self._owns_running_task(
                task,
                worker_id,
            ):
                return False

            task["error"] = error
            task["assigned_worker"] = None
            task["lease_expires_at"] = None

            if task["attempts"] >= self.max_attempts:
                task["status"] = "failed"
                task["completed_at"] = time.time()
            else:
                task["status"] = "ready"

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

            if task["attempts"] >= self.max_attempts:
                task["status"] = "failed"
                task["completed_at"] = now
            else:
                task["status"] = "ready"

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
