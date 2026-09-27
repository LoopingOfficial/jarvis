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
from .planner import MissionPlanner
from .tools import ToolRegistry
from .verifier import MissionVerifier
from .workspace import GitWorkspaceManager


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

    def create_task(self, prompt: str, task_type: str, capabilities: List[str], mission_id: str, dependencies: List[str]) -> Dict[str, Any]:
        return self.request("POST", "/tasks", {"prompt": prompt, "required_capabilities": capabilities, "dependencies": dependencies, "metadata": {"mission_id": mission_id, "task_type": task_type}})["task"]

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

    def save(self, mission: Mission) -> None:
        MISSION_HOME.mkdir(parents=True, exist_ok=True)
        (MISSION_HOME / (mission.mission_id + ".json")).write_text(json.dumps(mission.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    def load(self, mission_id: str) -> Mission:
        return Mission.from_dict(json.loads((MISSION_HOME / (mission_id + ".json")).read_text(encoding="utf-8")))

    def run(self, request: str, repository: str, dry_run: bool = False, allow_write: bool = False, test_path: str = "tests") -> Mission:
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
        mission.status = "running"
        mission.started_at = time.time()
        mission.event("WORKSPACE_CREATED", path=mission.workspace)
        self.save(mission)
        self.print_progress(mission)

        try:
            for task in mission.tasks:
                task.status = "running"
                mission.event("TASK_STARTED", task_id=task.task_id)
                if task.task_type == "inspect":
                    listing = tools.execute_tool("list_files", {"path": "."})
                    status = tools.execute_tool("git_status", {})
                    mission.artifact(task.task_id, "analysis", {"files": listing, "git_status": status}, path=mission.workspace)
                    task.result = {"files": len(listing), "git_status": status}
                elif task.task_type == "test":
                    result = tools.execute_tool("run_tests", {"path": test_path})
                    mission.artifact(task.task_id, "test_result", result)
                    task.result = result
                    if result.get("returncode") != 0:
                        task.status = "failed"
                        raise RuntimeError("verification_tests_failed")
                elif task.task_type == "review":
                    diff = tools.execute_tool("git_diff", {})
                    mission.artifact(task.task_id, "git_diff", diff)
                    task.result = {"diff_length": len(diff), "status": "reviewed"}
                else:
                    capabilities = list(task.required_capabilities)
                    available = set(self.coordinator.available_capabilities())
                    if capabilities and not set(capabilities).issubset(available):
                        alternatives = ["reasoning", "analysis", "coding", "review", "synthesis"]
                        fallback = next((item for item in alternatives if item in available), None)
                        if fallback:
                            capabilities = [fallback]
                    remote = self.coordinator.create_task(task.prompt, task.task_type, capabilities, mission.mission_id, [])
                    task.worker_id = remote.get("assigned_worker")
                    result = self.coordinator.wait(remote["task_id"], self.timeout)
                    task.worker_id = result.get("assigned_worker") or (result.get("result") or {}).get("worker_id")
                    task.result = result.get("result")
                    mission.artifact(task.task_id, "analysis", task.result, worker_id=task.worker_id, remote_task_id=remote["task_id"])
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
            mission.status = "blocked" if "operator_task_timeout" in str(exc) else "failed"
            mission.error = str(exc)
            mission.event("MISSION_FAILED", error=str(exc))
        self.save(mission)
        self.print_report(mission)
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
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--mission")
    args = parser.parse_args(argv)
    operator = MissionOperator(args.coordinator)
    if args.status:
        if not args.mission:
            parser.error("--status requires --mission")
        operator.print_report(operator.load(args.mission))
        return 0
    if not args.request:
        parser.error("a mission request is required")
    mission = operator.run(args.request, args.repo, args.dry_run, args.allow_write, args.test_path)
    return 0 if mission.status in {"completed", "ready"} else 1


if __name__ == "__main__":
    sys.exit(main())
