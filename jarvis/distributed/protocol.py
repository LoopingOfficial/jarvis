from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import time
import uuid


class WorkerStatus(str, Enum):
    UNKNOWN = "unknown"
    ONLINE = "online"
    BUSY = "busy"
    OFFLINE = "offline"


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass
class WorkerState:
    worker_id: str
    name: str
    role: str
    os: str
    ollama_url: str
    model: str
    priority: int
    capabilities: set[str]

    status: WorkerStatus = WorkerStatus.UNKNOWN
    latency_ms: float | None = None
    running_tasks: int = 0
    last_heartbeat: float | None = None
    available_models: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class Task:
    prompt: str
    required_capabilities: set[str] = field(default_factory=set)
    dependencies: set[str] = field(default_factory=set)

    task_id: str = field(
        default_factory=lambda: str(uuid.uuid4())
    )

    status: TaskStatus = TaskStatus.PENDING
    assigned_worker: str | None = None
    attempts: int = 0
    result: Any = None
    error: str | None = None

    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None


@dataclass
class TaskResult:
    task_id: str
    worker_id: str
    success: bool
    content: str = ""
    duration: float = 0.0
    tokens_per_second: float = 0.0
    error: str | None = None
