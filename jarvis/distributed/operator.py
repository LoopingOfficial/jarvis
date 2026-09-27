"""VELKO Distributed Operator V1 CLI."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from .mission import Mission
from .code_change import CodeChangeSet, PatchExecutor
from .planner import MissionPlanner
from .tools import ToolRegistry
from .verifier import MissionVerifier
from .workspace import GitWorkspaceManager
from .agents import AgentRegistry, ExecutionRequest


CODE_CHANGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["changes"],
    "properties": {
        "changes": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["operation", "path", "reason", "after"],
                "properties": {
                    "operation": {"enum": ["replace", "create"]},
                    "path": {"type": "string", "minLength": 1},
                    "reason": {"type": "string"},
                    "before": {"type": ["string", "null"]},
                    "after": {"type": "string"},
                },
            },
        },
    },
}


MISSION_HOME = Path.home() / ".velko" / "missions"


class CoordinatorClient:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def create_task(self, prompt: str, task_type: str, capabilities: List[str], mission_id: str, dependencies: List[str], worker_id: Optional[str] = None) -> Dict[str, Any]:
        metadata = {"mission_id": mission_id, "task_type": task_type}
        if worker_id:
            metadata["operator_agent_instance"] = worker_id
        return self.request("POST", "/tasks", {"prompt": prompt, "required_capabilities": capabilities, "dependencies": dependencies, "metadata": metadata})["task"]

    def status(self) -> Dict[str, Any]:
        return self.request("GET", "/status")

    def available_capabilities(self) -> List[str]:
        status = self.request("GET", "/status")
        result = set()
        for worker in status.get("workers", {}).values():
            if worker.get("status") in {"online", "busy"}:
                result.update(worker.get("capabilities", []))
        return sorted(result)

    def wait(self, task_id: str, timeout: float) -> Dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            tasks = self.request("GET", "/tasks").get("tasks", [])
            task = next((item for item in tasks if item["task_id"] == task_id), None)
            if task and task["status"] in {"done", "failed"}:
                return task
            time.sleep(0.5)
        raise TimeoutError("operator_task_timeout:" + task_id)


class MissionOperator:
    def __init__(self, coordinator: str = "http://127.0.0.1:8765", timeout: float = 180.0):
        self.coordinator = CoordinatorClient(coordinator)
        self.timeout = timeout
        self.planner = MissionPlanner()
        self.agents = AgentRegistry(self.coordinator)

    def save(self, mission: Mission) -> None:
        MISSION_HOME.mkdir(parents=True, exist_ok=True)
        (MISSION_HOME / (mission.mission_id + ".json")).write_text(json.dumps(mission.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    def load(self, mission_id: str) -> Mission:
        return Mission.from_dict(json.loads((MISSION_HOME / (mission_id + ".json")).read_text(encoding="utf-8")))

    def _remote(self, task: Any, prompt: str, mission_id: str, forced_provider: Optional[str] = None) -> Dict[str, Any]:
        request = ExecutionRequest(task.task_id, prompt, workspace=getattr(self, "_active_workspace", None), required_capabilities=list(task.required_capabilities), structured_output=task.task_type == "code_change", expected_schema=CODE_CHANGE_SCHEMA if task.task_type == "code_change" else None, timeout=self.timeout)
        if not self.agents.instances:
            self.agents.discover()
        decision = self.agents.route(request.required_capabilities, forced_provider)
        task.routing = decision
        result = self.agents.execute(request, forced_provider)
        worker_id = result.raw.get("assigned_worker") if isinstance(result.raw, dict) else None
        worker_id = worker_id or result.agent_instance_id
        return {"task": result.raw, "worker_id": worker_id, "content": result.content, "structured_output": result.structured_output, "routing": decision}

    @staticmethod
    def _repository_context(tools: ToolRegistry) -> Dict[str, Any]:
        all_files = tools.list_files(".")
        files = [item for item in all_files if not item.startswith((".claude/", ".claude-flow/", ".agents/", ".git/"))]
        excerpts = {}
        for relative in files:
            if len(excerpts) >= 12:
                break
            if Path(relative).suffix in {".py", ".js", ".ts", ".json"}:
                try:
                    excerpts[relative] = tools.read_file(relative)[:12000]
                except (OSError, UnicodeError):
                    continue
        return {"files": files[:200], "git_status": tools.git_status(), "excerpts": excerpts}

    def run(self, request: str, repository: str, dry_run: bool = False, allow_write: bool = False, test_path: str = "tests", max_fix_attempts: int = 2, keep_workspace: bool = True, show_diff: bool = False, forced_provider: Optional[str] = None) -> Mission:
        mission = Mission(request, workspace=None)
        mission.tasks = self.planner.plan(mission)
        mission.status = "ready"
        mission.event("PLAN_CREATED", task_count=len(mission.tasks))
        if dry_run:
            self.save(mission)
            self.print_plan(mission)
            return mission

        manager = GitWorkspaceManager(repository)
        mission.workspace = manager.create_workspace(mission.mission_id)
        manager.create_task_branch("velko/" + mission.mission_id)
        tools = ToolRegistry(mission.workspace, allow_write=allow_write)
        self._active_workspace = mission.workspace
        patcher = PatchExecutor(mission.workspace)
        context = {}
        root_cause = ""
        mission.status = "running"
        mission.started_at = time.time()
        mission.event("WORKSPACE_CREATED", path=mission.workspace)
        self.save(mission)
        self.print_progress(mission)

        try:
            for task in mission.tasks:
                if any(next((item for item in mission.tasks if item.task_id == dependency), None).status != "done" for dependency in task.depends_on):
                    raise RuntimeError("task_dependency_not_satisfied:" + task.task_id)
                task.status = "running"
                mission.event("TASK_STARTED", task_id=task.task_id)
                if task.task_type == "inspect":
                    context = self._repository_context(tools)
                    mission.artifact(task.task_id, "analysis", context, path=mission.workspace)
                    task.result = {"files": len(context["files"]), "git_status": context["git_status"]}
                elif task.task_type == "llm":
                    prompt = "User request:\n{}\nRepository evidence (JSON):\n{}\nPrevious root cause:\n{}\nReturn a concise, evidence-bound answer; do not invent files or test results.".format(request, json.dumps(context, ensure_ascii=False), root_cause)
                    remote = self._remote(task, prompt, mission.mission_id, forced_provider)
                    result = remote["task"]
                    task.worker_id = remote["worker_id"]
                    task.result = result.get("result") if isinstance(result, dict) and result.get("result") is not None else remote["content"]
                    artifact_type = "root_cause" if task.task_id == "analyze" else "final_report"
                    mission.artifact(task.task_id, artifact_type, task.result, worker_id=task.worker_id, remote_task_id=result.get("task_id") if isinstance(result, dict) else None)
                    if task.task_id == "analyze":
                        root_cause = (task.result or {}).get("content", "") if isinstance(task.result, dict) else str(task.result)
                elif task.task_type == "code_change":
                    prompt = "Return ONLY the JSON object matching the supplied schema, with no markdown or prose. Every path MUST be repository-relative (for example jarvis/distributed/file.py), never absolute and never containing .. . Use only replace/create operations. User request: {}. Root cause: {}. Repository evidence: {}. The change must be minimal and testable.".format(request, root_cause, json.dumps(context, ensure_ascii=False))
                    remote = self._remote(task, prompt, mission.mission_id, forced_provider)
                    result = remote["task"]
                    task.worker_id = remote["worker_id"]
                    raw = json.dumps(remote["structured_output"]) if remote.get("structured_output") is not None else ((result.get("result") or {}).get("content", "") if isinstance(result, dict) else remote["content"])
                    change_set = CodeChangeSet.from_json(raw)
                    modified = patcher.apply(change_set)
                    mission.artifact(task.task_id, "change_set", change_set.to_dict(), worker_id=task.worker_id, attempt=0)
                    mission.artifact(task.task_id, "modified_files", modified, worker_id=task.worker_id, attempt=0)
                    task.result = change_set.to_dict()
                elif task.task_type == "test":
                    result = tools.execute_tool("run_tests", {"path": test_path})
                    mission.artifact(task.task_id, "test_result", result)
                    task.result = result
                    fix_attempt = 0
                    while result.get("returncode") != 0 and any(item.task_type == "code_change" for item in mission.tasks):
                        if fix_attempt >= max_fix_attempts:
                            task.status = "failed"
                            raise RuntimeError("max_fix_attempts_exceeded")
                        fix_attempt += 1
                        patcher.rollback()
                        correction_prompt = "Return ONLY valid JSON CodeChangeSet. Correct the failed tests. User request: {}. Root cause: {}. Current test result: {}. Current diff: {}".format(request, root_cause, json.dumps(result, ensure_ascii=False), tools.git_diff())
                        remote = self._remote(task, correction_prompt, mission.mission_id, forced_provider)
                        correction_result = remote["task"]
                        raw = json.dumps(remote["structured_output"]) if remote.get("structured_output") is not None else ((correction_result.get("result") or {}).get("content", "") if isinstance(correction_result, dict) else remote["content"])
                        correction = CodeChangeSet.from_json(raw)
                        modified = patcher.apply(correction)
                        mission.artifact(task.task_id, "change_set", correction.to_dict(), worker_id=remote["worker_id"], attempt=fix_attempt)
                        mission.artifact(task.task_id, "modified_files", modified, worker_id=remote["worker_id"], attempt=fix_attempt)
                        result = tools.execute_tool("run_tests", {"path": test_path})
                        mission.artifact(task.task_id, "test_result", result, attempt=fix_attempt)
                        task.result = result
                    if result.get("returncode") != 0:
                        task.status = "failed"
                        raise RuntimeError("verification_tests_failed")
                elif task.task_type == "review":
                    diff = tools.execute_tool("git_diff", {})
                    review = {"decision": "PASS" if diff or not any(item.task_type == "code_change" for item in mission.tasks) else "NEEDS_FIX", "findings": [], "diff_length": len(diff)}
                    mission.artifact(task.task_id, "git_diff", diff)
                    mission.artifact(task.task_id, "review", review)
                    task.result = review
                else:
                    raise RuntimeError("unsupported_operator_task_type:" + task.task_type)
                task.progress = 100
                task.status = "done"
                mission.event("TASK_COMPLETED", task_id=task.task_id, worker_id=task.worker_id)
                self.save(mission)

            mission.status = "verifying"
            verification = MissionVerifier().verify(mission, tools)
            mission.artifact("verification", "report", verification)
            if verification["status"] != "PASS":
                raise RuntimeError("mission_verification_" + verification["status"].lower())
            mission.result = {"mission_id": mission.mission_id, "workspace": mission.workspace, "artifacts": len(mission.artifacts), "verification": verification}
            mission.status = "completed"
            mission.completed_at = time.time()
            mission.event("MISSION_COMPLETED", artifact_count=len(mission.artifacts))
        except (OSError, RuntimeError, TimeoutError, ValueError, urllib.error.URLError) as exc:
            mission.status = "blocked" if any(marker in str(exc) for marker in ("operator_task_timeout", "no_available_agent", "agent_execution_failed")) else "failed"
            mission.error = str(exc)
            mission.event("MISSION_FAILED", error=str(exc))
        self.save(mission)
        self.print_report(mission)
        if show_diff and mission.workspace:
            print("\nDIFF\n{}".format(tools.git_diff()))
        return mission

    @staticmethod
    def print_plan(mission: Mission) -> None:
        print("VELKO MISSION {} — DRY RUN".format(mission.mission_id))
        for task in mission.tasks:
            print("[ ] {} | type={} | capabilities={} | depends_on={}".format(task.title, task.task_type, ",".join(task.required_capabilities), ",".join(task.depends_on) or "-"))

    @staticmethod
    def print_progress(mission: Mission) -> None:
        print("VELKO MISSION {}".format(mission.mission_id))
        print("workspace: {}".format(mission.workspace))

    @staticmethod
    def print_report(mission: Mission) -> None:
        print("\nMISSION: {}".format(mission.status.upper()))
        print("tasks: {}/{} completed".format(sum(task.status == "done" for task in mission.tasks), len(mission.tasks)))
        print("artifacts: {}".format(len(mission.artifacts)))
        if mission.error:
            print("error: {}".format(mission.error))
        if mission.result:
            print(json.dumps(mission.result, indent=2, ensure_ascii=False))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="VELKO Distributed Operator")
    parser.add_argument("request", nargs="?", help="Mission utilisateur")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--coordinator", default="http://127.0.0.1:8765")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-write", action="store_true", help="Enable explicitly requested workspace write tools")
    parser.add_argument("--test-path", default="tests", help="Workspace-relative unittest directory")
    parser.add_argument("--max-fix-attempts", type=int, default=2)
    parser.add_argument("--keep-workspace", action="store_true", default=True)
    parser.add_argument("--show-diff", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--mission")
    parser.add_argument("--agents", action="store_true", help="Discover real local and distributed agent instances")
    parser.add_argument("--explain-routing", action="store_true", help="Include provider routing decisions in the mission report")
    parser.add_argument("--provider", choices=["codex", "claude", "gemini", "opencode", "ollama"], help="Force one discovered provider")
    args = parser.parse_args(argv)
    operator = MissionOperator(args.coordinator)
    if args.agents:
        operator.agents.discover()
        print(operator.agents.format_report())
        return 0 if any(item.functional for item in operator.agents.instances) else 1
    if args.status:
        if not args.mission:
            parser.error("--status requires --mission")
        operator.print_report(operator.load(args.mission))
        return 0
    if not args.request:
        parser.error("a mission request is required")
    mission = operator.run(args.request, args.repo, args.dry_run, args.allow_write, args.test_path, max(0, args.max_fix_attempts), args.keep_workspace, args.show_diff, args.provider)
    if args.explain_routing:
        print("\nROUTING\n" + json.dumps(operator.agents.last_routing, indent=2, ensure_ascii=False))
    return 0 if mission.status in {"completed", "ready"} else 1


if __name__ == "__main__":
    sys.exit(main())
