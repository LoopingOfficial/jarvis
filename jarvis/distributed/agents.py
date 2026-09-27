"""V3 agent providers, discovery and explainable routing.

This module deliberately keeps provider execution behind one small contract.  CLI
providers are discovered from the local PATH only; Ollama workers are discovered
from the coordinator's authenticated-by-reachability status endpoint.  No remote
shell or user supplied command is accepted here.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set


CLI_COMMANDS = {
    "codex": ("codex", "OpenAI Codex CLI"),
    "claude": ("claude", "Claude Code"),
    "gemini": ("gemini", "Gemini CLI"),
    "opencode": ("opencode", "OpenCode"),
}

READY = "READY"
INSTALLED = "INSTALLED"
DEGRADED = "DEGRADED"
UNAUTHENTICATED = "UNAUTHENTICATED"
QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
TIMEOUT = "TIMEOUT"
OFFLINE = "OFFLINE"
ERROR = "ERROR"


@dataclass
class ExecutionRequest:
    task_id: str
    prompt: str
    workspace: Optional[str] = None
    required_capabilities: List[str] = field(default_factory=list)
    structured_output: bool = False
    expected_schema: Optional[Dict[str, Any]] = None
    timeout: float = 180.0


@dataclass
class ExecutionResult:
    ok: bool
    provider_id: str
    agent_instance_id: str
    content: str = ""
    structured: Any = None
    structured_output: Any = None
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
    status: str = INSTALLED
    health: str = "unknown"
    reason: str = "not checked"
    load: float = 0.0
    last_checked: Optional[float] = None
    last_success: Optional[float] = None
    consecutive_failures: int = 0
    latency_ms: Optional[float] = None
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

    def __init__(self, provider_id: str, executable: str, display_name: str, which: Callable[[str], Optional[str]] = shutil.which, runner: Callable[..., Any] = subprocess.run, probe_timeout: float = 15.0) -> None:
        self.provider_id = provider_id
        self.executable = executable
        self.display_name = display_name
        self._which = which
        self._runner = runner
        self.probe_timeout = probe_timeout

    def build_command(self, request: ExecutionRequest, executable: Optional[str] = None, schema_path: Optional[str] = None, output_path: Optional[str] = None) -> List[str]:
        """Build the complete fixed argv for both probes and real executions.

        Codex treats the prompt as the positional argument of ``exec``.  Keep it
        last so optional flags cannot be mistaken for prompt text by wrappers.
        """
        executable = executable or self.executable
        prompt = request.prompt
        if self.provider_id == "codex":
            args = [executable, "exec", "--ephemeral", "--sandbox", "read-only", "--skip-git-repo-check", "-c", "model_reasoning_effort=none"]
            if request.structured_output:
                if not schema_path or not output_path:
                    raise ValueError("structured_output_paths_required")
                args.extend(["--output-schema", schema_path, "--output-last-message", output_path])
            args.append(prompt)
            return args
        if self.provider_id == "claude":
            if request.structured_output:
                raise ValueError("structured_output_unsupported:claude")
            return [executable, "--permission-mode", "plan", "-p", prompt]
        if self.provider_id == "gemini":
            if request.structured_output:
                raise ValueError("structured_output_unsupported:gemini")
            return [executable, "-p", prompt]
        if self.provider_id == "opencode":
            if request.structured_output:
                raise ValueError("structured_output_unsupported:opencode")
            return [executable, "run", prompt]
        raise ValueError("no fixed adapter for provider")

    def _probe_args(self, executable: str) -> List[str]:
        return self.build_command(ExecutionRequest("healthcheck", "Reply exactly VELKO_READY"), executable=executable)

    @staticmethod
    def _diagnostic_text(value: Any, limit: int = 4000) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        return str(value)[:limit]

    @staticmethod
    def _classify_error(text: str, timed_out: bool = False) -> str:
        lower = text.lower()
        if timed_out:
            return TIMEOUT
        if any(marker in lower for marker in ("weekly limit", "usage limit", "rate limit", "quota exhausted", "quota limit")):
            return QUOTA_EXHAUSTED
        if any(marker in lower for marker in ("unauthorized", "unauthenticated", "invalid api key", "authentication required")):
            return UNAUTHENTICATED
        return ERROR

    def discover(self) -> List[AgentInstance]:
        path = self._which(self.executable)
        instance = AgentInstance(
            instance_id="{}@local".format(self.provider_id),
            provider_id=self.provider_id,
            display_name=self.display_name,
            host="m4-local",
            capabilities={"reasoning", "analysis", "coding", "structured_output"},
            metadata={"executable": path or self.executable},
        )
        if not path:
            instance.status = OFFLINE
            instance.reason = "executable not found on PATH"
            return [instance]
        instance.available = True
        instance.last_checked = time.time()
        try:
            completed = self._runner([path, "--version"], capture_output=True, text=True, timeout=5, check=False)
            version = (completed.stdout or completed.stderr or "").strip().splitlines()[0][:160]
            instance.metadata["version"] = version
            if completed.returncode != 0:
                instance.status = ERROR
                instance.health = "unhealthy"
                instance.reason = "version check failed"
                return [instance]
            started = time.perf_counter()
            probe = self._runner(self._probe_args(path), cwd=None, capture_output=True, text=True, timeout=self.probe_timeout, check=False)
            instance.latency_ms = (time.perf_counter() - started) * 1000
            probe_text = ((probe.stdout or "") + "\n" + (probe.stderr or "")).strip()
            instance.last_checked = time.time()
            if probe.returncode == 0 and "VELKO_READY" in probe.stdout:
                instance.functional = True
                instance.status = READY
                instance.health = "healthy"
                instance.reason = "real execution probe passed"
                instance.last_success = instance.last_checked
            else:
                instance.status = self._classify_error(probe_text)
                instance.health = "degraded"
                instance.reason = probe_text[-500:] or "real execution probe failed"
        except (OSError, subprocess.SubprocessError) as exc:
            instance.status = self._classify_error(str(exc), isinstance(exc, subprocess.TimeoutExpired))
            instance.health = "degraded"
            instance.reason = "healthcheck failed: {}".format(exc)
        return [instance]

    def execute(self, instance: AgentInstance, request: ExecutionRequest) -> ExecutionResult:
        executable = instance.metadata.get("executable")
        if not executable or not instance.functional:
            return ExecutionResult(False, self.provider_id, instance.instance_id, error="provider is not functional")
        started = time.perf_counter()
        args: List[str] = []
        schema_path = None
        output_path = None
        schema_content = None
        output_content = None
        completed = None
        content = ""
        stderr = ""
        timed_out = False
        try:
            structured = None
            with tempfile.TemporaryDirectory(prefix="velko-structured-") as directory:
                if request.structured_output:
                    if not isinstance(request.expected_schema, dict):
                        return ExecutionResult(False, self.provider_id, instance.instance_id, error="structured_output_schema_required")
                    schema_path = directory + "/schema.json"
                    output_path = directory + "/result.json"
                    with open(schema_path, "w", encoding="utf-8") as handle:
                        json.dump(request.expected_schema, handle)
                    schema_content = json.dumps(request.expected_schema, ensure_ascii=False)
                args = self.build_command(request, executable=executable, schema_path=schema_path, output_path=output_path)
                completed = self._runner(args, cwd=request.workspace, capture_output=True, text=True, timeout=request.timeout, check=False)
                content = self._diagnostic_text(getattr(completed, "stdout", "")).strip()
                stderr = self._diagnostic_text(getattr(completed, "stderr", "")).strip()
                error = None if completed.returncode == 0 else (stderr or content or "cli execution failed")[:1000]
                ok = completed.returncode == 0
                if ok and request.structured_output:
                    try:
                        with open(output_path, "r", encoding="utf-8") as handle:
                            output_content = handle.read()
                        with open(output_path, "r", encoding="utf-8") as handle:
                            structured = json.load(handle)
                    except (OSError, TypeError, ValueError) as exc:
                        ok = False
                        error = "structured_output_invalid:{}".format(exc)
                output_exists = bool(output_path and os.path.exists(output_path))
            instance.last_checked = time.time()
            instance.latency_ms = (time.perf_counter() - started) * 1000
            if ok:
                instance.status = READY
                instance.last_success = instance.last_checked
                instance.consecutive_failures = 0
            else:
                instance.status = self._classify_error((completed.stderr or content or ""))
                instance.functional = False
                instance.consecutive_failures += 1
            raw = {
                "argv": args,
                "prompt_chars": len(request.prompt),
                "returncode": completed.returncode,
                "exit_code": completed.returncode,
                "timed_out": timed_out,
                "duration": time.perf_counter() - started,
                "stdout": content,
                "stderr": stderr,
                "schema_path": schema_path,
                "schema_exists": bool(schema_path and os.path.exists(schema_path)),
                "schema_content": schema_content,
                "output_path": output_path,
                "output_exists": output_exists,
                "output_content": output_content,
                "structured_output": structured,
            }
            return ExecutionResult(ok, self.provider_id, instance.instance_id, content=content, structured=structured, structured_output=structured, error=error, duration=time.perf_counter() - started, raw=raw)
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stderr = self._diagnostic_text(getattr(exc, "stderr", ""))
            content = self._diagnostic_text(getattr(exc, "output", ""))
            instance.status = TIMEOUT
            instance.functional = False
            instance.consecutive_failures += 1
            return ExecutionResult(False, self.provider_id, instance.instance_id, content=content, error="provider_timeout", duration=time.perf_counter() - started, raw={"argv": args, "prompt_chars": len(request.prompt), "returncode": None, "exit_code": None, "timed_out": True, "duration": time.perf_counter() - started, "stdout": content, "stderr": stderr, "schema_path": schema_path, "schema_exists": False, "schema_content": schema_content, "output_path": output_path, "output_exists": False, "output_content": None, "structured_output": None})
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            instance.status = self._classify_error(str(exc), isinstance(exc, subprocess.TimeoutExpired))
            instance.functional = False
            instance.consecutive_failures += 1
            return ExecutionResult(False, self.provider_id, instance.instance_id, error=str(exc), duration=time.perf_counter() - started, raw={"argv": args, "prompt_chars": len(request.prompt), "returncode": getattr(completed, "returncode", None), "exit_code": getattr(completed, "returncode", None), "timed_out": timed_out, "duration": time.perf_counter() - started, "stdout": content, "stderr": stderr, "schema_path": schema_path, "schema_exists": False, "schema_content": schema_content, "output_path": output_path, "output_exists": False, "output_content": output_content, "structured_output": None})


class OllamaProvider(AgentProvider):
    provider_id = "ollama"

    def __init__(self, coordinator: Any, timeout: float = 5.0):
        self.coordinator = coordinator
        self.timeout = timeout

    def discover(self) -> List[AgentInstance]:
        try:
            status = self.coordinator.status()
        except Exception as exc:
            return [AgentInstance("ollama@cluster", self.provider_id, "Ollama distributed", "cluster", available=False, status=OFFLINE, health="unreachable", reason=str(exc))]
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
                status=READY if state in {"online", "busy"} else OFFLINE,
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
        candidates = [item for item in self.instances if item.status == READY and item.functional and required.issubset(item.capabilities) and (not forced_provider or item.provider_id == forced_provider)]
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
            diagnostics = ""
            if result.raw:
                diagnostics = "; diagnostics=" + json.dumps(result.raw, ensure_ascii=False, default=str)[:4000]
            failures.append("{}: {}{}".format(instance_id, result.error or "provider_execution_failed", diagnostics))
        raise RuntimeError("agent_execution_failed; fallback=" + " | ".join(failures))

    def format_report(self) -> str:
        lines = ["VELKO AGENTS"]
        for item in self.instances:
            lines.append("{} {:<11} {:<22} installed={} operational={} latency_ms={} reason={}".format(item.status, item.provider_id, item.instance_id, "yes" if item.available else "no", "yes" if item.functional else "no", "{:.1f}".format(item.latency_ms) if item.latency_ms is not None else "-", item.reason))
        return "\n".join(lines)
