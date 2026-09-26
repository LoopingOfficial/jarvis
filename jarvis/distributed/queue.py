from __future__ import annotations

import threading
from .protocol import Task, TaskStatus


class TaskQueue:
    def __init__(self):
        self.tasks: dict[str, Task] = {}
        self._lock = threading.RLock()

    def add(self, task: Task) -> Task:
        with self._lock:
            if task.task_id in self.tasks:
                raise ValueError(f"Task already exists: {task.task_id}")

            self.tasks[task.task_id] = task
            self._refresh()
            return task

    def add_many(self, tasks: list[Task]) -> None:
        for task in tasks:
            self.add(task)

    def _refresh(self) -> None:
        for task in self.tasks.values():
            if task.status not in {
                TaskStatus.PENDING,
                TaskStatus.BLOCKED,
            }:
                continue

            if not task.dependencies:
                task.status = TaskStatus.READY
                continue

            missing = [
                dep for dep in task.dependencies
                if dep not in self.tasks
            ]

            if missing:
                task.status = TaskStatus.BLOCKED
                continue

            completed = all(
                self.tasks[dep].status == TaskStatus.DONE
                for dep in task.dependencies
            )

            task.status = (
                TaskStatus.READY
                if completed
                else TaskStatus.BLOCKED
            )

    def ready(self) -> list[Task]:
        with self._lock:
            self._refresh()
            return [
                task for task in self.tasks.values()
                if task.status == TaskStatus.READY
            ]

    def claim(self, task_id: str, worker_id: str) -> Task:
        with self._lock:
            task = self.tasks[task_id]

            if task.status != TaskStatus.READY:
                raise RuntimeError(
                    f"Task {task_id} is not READY"
                )

            task.status = TaskStatus.RUNNING
            task.assigned_worker = worker_id
            task.attempts += 1
            return task

    def complete(self, task_id: str, result) -> None:
        with self._lock:
            task = self.tasks[task_id]
            task.status = TaskStatus.DONE
            task.result = result
            self._refresh()

    def fail(self, task_id: str, error: str) -> None:
        with self._lock:
            task = self.tasks[task_id]
            task.status = TaskStatus.FAILED
            task.error = error

    def requeue(self, task_id: str) -> None:
        with self._lock:
            task = self.tasks[task_id]
            task.status = TaskStatus.READY
            task.assigned_worker = None
            task.error = None

    def all_done(self) -> bool:
        with self._lock:
            return bool(self.tasks) and all(
                task.status == TaskStatus.DONE
                for task in self.tasks.values()
            )
