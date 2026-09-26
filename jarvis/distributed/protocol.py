from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional, Set
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
    capabilities: Set[str]

    status: WorkerStatus = WorkerStatus.UNKNOWN
    latency_ms: Optional[float] = None
    running_tasks: int = 0
    last_heartbeat: Optional[float] = None
    available_models: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class Task:
    prompt: str
    required_capabilities: Set[str] = field(default_factory=set)
    dependencies: Set[str] = field(default_factory=set)

    task_id: str = field(
        default_factory=lambda: str(uuid.uuid4())
    )

    status: TaskStatus = TaskStatus.PENDING
    assigned_worker: Optional[str] = None
    attempts: int = 0
    result: Any = None
    error: Optional[str] = None

    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


@dataclass
class TaskResult:
    task_id: str
    worker_id: str
    success: bool
    content: str = ""
    duration: float = 0.0
    tokens_per_second: float = 0.0
    error: Optional[str] = None
