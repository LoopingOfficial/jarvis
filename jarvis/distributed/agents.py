"""V3 agent providers, discovery and explainable routing.

This module deliberately keeps provider execution behind one small contract.  CLI
providers are discovered from the local PATH only; Ollama workers are discovered
from the coordinator's authenticated-by-reachability status endpoint.  No remote
shell or user supplied command is accepted here.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set


CLI_COMMANDS = {
    "codex": ("codex", "OpenAI Codex CLI"),
    "claude": ("claude", "Claude Code"),
    "gemini": ("gemini", "Gemini CLI"),
    "opencode": ("opencode", "OpenCode"),
}


@dataclass
class ExecutionRequest:
    task_id: str
    prompt: str
    workspace: Optional[str] = None
    required_capabilities: List[str] = field(default_factory=list)
    structured_output: bool = False
    timeout: float = 180.0


@dataclass
class ExecutionResult:
    ok: bool
    provider_id: str
    agent_instance_id: str
    content: str = ""
    structured: Any = None
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    duration: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentInstance:
    instance_id: str
    provider_id: str
    display_name: str
    host: str
    model: str = ""
    capabilities: Set[str] = field(default_factory=set)
    available: bool = False
    functional: bool = False
    health: str = "unknown"
    reason: str = "not checked"
    load: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["capabilities"] = sorted(self.capabilities)
        return result


class AgentProvider:
    """Provider contract shared by local agents and distributed workers."""

    provider_id = "base"

    def discover(self) -> List[AgentInstance]:
        raise NotImplementedError

    def execute(self, instance: AgentInstance, request: ExecutionRequest) -> ExecutionResult:
        raise NotImplementedError


class CliProvider(AgentProvider):
    """Safe discovery adapter for a locally installed agent CLI.

    Execution is opt-in per provider and uses a fixed argv shape.  It never
    invokes a shell and never accepts an arbitrary executable from mission data.
    """

    def __init__(self, provider_id: str, executable: str, display_name: str, which: Callable[[str], Optional[str]] = shutil.which, runner: Callable[..., Any] = subprocess.run) -> None:
        self.provider_id = provider_id
        self.executable = executable
        self.display_name = display_name
        self._which = which
        self._runner = runner

    def discover(self) -> List[AgentInstance]:
        path = self._which(self.executable)
        instance = AgentInstance(
            instance_id="{}@local".format(self.provider_id),
            provider_id=self.provider_id,
            display_name=self.display_name,
            host="m4-local",
            capabilities={"reasoning", "analysis"},
            metadata={"executable": path or self.executable},
        )
        if not path:
            instance.reason = "executable not found on PATH"
            return [instance]
        try:
            completed = self._runner([path, "--version"], capture_output=True, text=True, timeout=5, check=False)
            version = (completed.stdout or completed.stderr or "").strip().splitlines()[0][:160]
            instance.available = completed.returncode == 0
            instance.functional = False
            instance.health = "healthy" if instance.available else "unhealthy"
            instance.reason = (version + "; detected, execution adapter unavailable") if instance.available else "version check failed"
            instance.metadata["version"] = version
        except (OSError, subprocess.SubprocessError) as exc:
            instance.reason = "healthcheck failed: {}".format(exc)
        return [instance]

    def execute(self, instance: AgentInstance, request: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(False, self.provider_id, instance.instance_id, error="{} execution adapter is not enabled".format(self.display_name))


class OllamaProvider(AgentProvider):
    provider_id = "ollama"

    def __init__(self, coordinator: Any, timeout: float = 5.0):
        self.coordinator = coordinator
        self.timeout = timeout

    def discover(self) -> List[AgentInstance]:
        try:
            status = self.coordinator.status()
        except Exception as exc:
            return [AgentInstance("ollama@cluster", self.provider_id, "Ollama distributed", "cluster", available=False, health="unreachable", reason=str(exc))]
        instances = []
        for worker_id, worker in status.get("workers", {}).items():
            state = str(worker.get("status", "offline"))
            capabilities = set(worker.get("capabilities") or [])
            # Structured output is a coordinator contract: the worker returns
            # text and the operator validates JSON before applying any patch.
            capabilities.add("structured_output")
            instance = AgentInstance(
                instance_id="ollama@{}".format(worker_id),
                provider_id=self.provider_id,
                display_name=worker.get("hostname") or worker_id,
                host=worker.get("hostname") or worker_id,
                model=worker.get("model", ""),
                capabilities=capabilities,
                available=state in {"online", "busy"},
                functional=state in {"online", "busy"},
                health="healthy" if state in {"online", "busy"} else "offline",
                reason="coordinator status: {}".format(state),
                load=float(worker.get("load") or 0),
                metadata={"worker_id": worker_id, "os": worker.get("os", "unknown"), "worker_version": worker.get("worker_version", "legacy")},
            )
            instances.append(instance)
        return instances

    def execute(self, instance: AgentInstance, request: ExecutionRequest) -> ExecutionResult:
        started = time.perf_counter()
        worker_id = instance.metadata.get("worker_id")
        try:
            remote = self.coordinator.create_task(request.prompt, "agent", request.required_capabilities, request.task_id, [], worker_id=worker_id)
            result = self.coordinator.wait(remote["task_id"], request.timeout)
            payload = result.get("result") or {}
            content = payload.get("content", "") if isinstance(payload, dict) else str(payload)
            actual_worker = result.get("assigned_worker") or (payload.get("worker_id") if isinstance(payload, dict) else worker_id)
            return ExecutionResult(True, self.provider_id, "ollama@{}".format(actual_worker or worker_id), content=content, raw=result, duration=time.perf_counter() - started)
        except Exception as exc:
            return ExecutionResult(False, self.provider_id, instance.instance_id, error=str(exc), duration=time.perf_counter() - started)


class AgentRegistry:
    def __init__(self, coordinator: Any, providers: Optional[Sequence[AgentProvider]] = None):
        self.coordinator = coordinator
        self.providers = list(providers or [OllamaProvider(coordinator)] + [CliProvider(pid, exe, name) for pid, (exe, name) in CLI_COMMANDS.items()])
        self.instances: List[AgentInstance] = []
        self.last_routing: List[Dict[str, Any]] = []

    def discover(self) -> List[AgentInstance]:
        self.instances = []
        for provider in self.providers:
            self.instances.extend(provider.discover())
        return self.instances

    def snapshot(self) -> Dict[str, Any]:
        return {"providers": sorted({item.provider_id for item in self.instances}), "agents": [item.to_dict() for item in self.instances]}

    def route(self, required_capabilities: Iterable[str], forced_provider: Optional[str] = None) -> Dict[str, Any]:
        required = set(required_capabilities)
        candidates = [item for item in self.instances if item.available and item.functional and required.issubset(item.capabilities) and (not forced_provider or item.provider_id == forced_provider)]
        candidates.sort(key=lambda item: (item.load, -len(item.capabilities), item.instance_id))
        reason = "required capabilities={} ; candidates={} ; selected={}".format(sorted(required), [item.instance_id for item in candidates], candidates[0].instance_id if candidates else None)
        decision = {"required_capabilities": sorted(required), "candidates": [item.instance_id for item in candidates], "selected": candidates[0].instance_id if candidates else None, "reason": reason}
        self.last_routing.append(decision)
        if not candidates:
            raise RuntimeError("no_available_agent:" + ",".join(sorted(required)))
        return decision

    def provider_for(self, instance_id: str) -> AgentProvider:
        instance = next(item for item in self.instances if item.instance_id == instance_id)
        return next(provider for provider in self.providers if provider.provider_id == instance.provider_id)

    def instance_for(self, instance_id: str) -> AgentInstance:
        return next(item for item in self.instances if item.instance_id == instance_id)

    def execute(self, request: ExecutionRequest, forced_provider: Optional[str] = None) -> ExecutionResult:
        if not self.instances:
            self.discover()
        decision = self.route(request.required_capabilities, forced_provider)
        failures = []
        for instance_id in [decision["selected"]] + [item for item in decision["candidates"] if item != decision["selected"]]:
            result = self.provider_for(instance_id).execute(self.instance_for(instance_id), request)
            if result.ok:
                return result
            failures.append("{}: {}".format(instance_id, result.error))
        raise RuntimeError("agent_execution_failed; fallback=" + " | ".join(failures))

    def format_report(self) -> str:
        lines = ["VELKO AGENTS"]
        for item in self.instances:
            state = "READY" if item.functional else ("DETECTED" if item.available else "UNAVAILABLE")
            lines.append("{} {:<11} {:<22} {}".format(state, item.provider_id, item.instance_id, item.reason))
        return "\n".join(lines)
