"""Mission, task and artifact domain objects for VELKO Operator."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


MISSION_STATES = {"planning", "ready", "running", "verifying", "completed", "failed", "blocked"}


@dataclass
class MissionTask:
    task_id: str
    title: str
    task_type: str
    prompt: str
    depends_on: List[str] = field(default_factory=list)
    required_capabilities: List[str] = field(default_factory=list)
    status: str = "pending"
    worker_id: Optional[str] = None
    progress: int = 0
    result: Any = None
    error: Optional[str] = None


@dataclass
class Artifact:
    artifact_id: str
    mission_id: str
    task_id: str
    worker_id: Optional[str]
    type: str
    path: Optional[str] = None
    content: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


@dataclass
class Mission:
    user_request: str
    workspace: Optional[str] = None
    mission_id: str = field(default_factory=lambda: "mission-" + uuid.uuid4().hex[:12])
    status: str = "planning"
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    tasks: List[MissionTask] = field(default_factory=list)
    artifacts: List[Artifact] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def event(self, name: str, **details: Any) -> None:
        self.events.append({"timestamp": time.time(), "event": name, "details": details})

    def artifact(self, task_id: str, artifact_type: str, content: Any = None, path: Optional[str] = None, worker_id: Optional[str] = None, **metadata: Any) -> Artifact:
        artifact = Artifact(
            artifact_id="artifact-" + uuid.uuid4().hex[:12],
            mission_id=self.mission_id,
            task_id=task_id,
            worker_id=worker_id,
            type=artifact_type,
            path=path,
            content=content,
            metadata=metadata,
        )
        self.artifacts.append(artifact)
        return artifact

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Mission":
        mission = cls(user_request=payload["user_request"], workspace=payload.get("workspace"), mission_id=payload["mission_id"])
        for key in ("status", "created_at", "started_at", "completed_at", "events", "result", "error"):
            if key in payload:
                setattr(mission, key, payload[key])
        mission.tasks = [MissionTask(**item) for item in payload.get("tasks", [])]
        mission.artifacts = [Artifact(**item) for item in payload.get("artifacts", [])]
        return mission
