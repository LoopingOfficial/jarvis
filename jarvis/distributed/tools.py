"""Typed, workspace-scoped tools exposed to Operator V1."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List


@dataclass
class ToolSpec:
    name: str
    description: str
    required_capabilities: List[str]
    input_schema: Dict[str, Any]
    risk_level: str
    execute: Callable[[Dict[str, Any]], Any]


class ToolRegistry:
    def __init__(self, workspace: str, allow_write: bool = False):
        self.root = Path(workspace).resolve()
        self.allow_write = allow_write
        self.tools: Dict[str, ToolSpec] = {}
        self._register_defaults()

    def _path(self, value: str = ".") -> Path:
        candidate = (self.root / value).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("path_outside_mission_workspace")
        return candidate

    def _register_defaults(self) -> None:
        self.register("list_files", "List workspace files", ["tools"], {"path": "string"}, "low", lambda data: self.list_files(data.get("path", ".")))
        self.register("read_file", "Read a text file", ["tools"], {"path": "string"}, "low", lambda data: self.read_file(data["path"]))
        self.register("search_code", "Search text in source files", ["tools"], {"pattern": "string"}, "low", lambda data: self.search_code(data["pattern"]))
        self.register("git_status", "Read git status", ["tools"], {}, "low", lambda data: self.git_status())
        self.register("git_diff", "Read git diff", ["tools"], {}, "low", lambda data: self.git_diff())
        self.register("run_tests", "Run the repository unittest suite", ["testing", "tools"], {"path": "string"}, "medium", lambda data: self.run_tests(data.get("path", "tests")))
        self.register("write_file", "Write a file inside the mission workspace", ["coding", "tools"], {"path": "string", "content": "string"}, "high", lambda data: self.write_file(data["path"], data["content"]))
        self.register("patch_file", "Apply an exact text replacement", ["coding", "tools"], {"path": "string", "old": "string", "new": "string"}, "high", lambda data: self.patch_file(data["path"], data["old"], data["new"]))

    def register(self, name: str, description: str, capabilities: List[str], schema: Dict[str, Any], risk: str, execute: Callable[[Dict[str, Any]], Any]) -> None:
        self.tools[name] = ToolSpec(name, description, capabilities, schema, risk, execute)

    def execute_tool(self, name: str, data: Dict[str, Any]) -> Any:
        if name not in self.tools:
            raise KeyError("unknown_tool:" + name)
        if self.tools[name].risk_level == "high" and not self.allow_write:
            raise PermissionError("write_tools_disabled")
        return self.tools[name].execute(data)

    def list_files(self, relative: str) -> List[str]:
        base = self._path(relative)
        result = []
        for path in sorted(base.rglob("*")):
            if ".git" in path.parts:
                continue
            if path.is_file():
                result.append(str(path.relative_to(self.root)))
        return result[:500]

    def read_file(self, relative: str) -> str:
        path = self._path(relative)
        return path.read_text(encoding="utf-8", errors="replace")[:200000]

    def search_code(self, pattern: str) -> List[Dict[str, Any]]:
        matcher = re.compile(pattern, re.IGNORECASE)
        results = []
        for relative in self.list_files("."):
            path = self._path(relative)
            if path.suffix.lower() not in {".py", ".js", ".ts", ".tsx", ".json", ".md", ".yaml", ".yml"}:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if matcher.search(line):
                    results.append({"path": relative, "line": number, "text": line[:400]})
                    if len(results) >= 200:
                        return results
        return results

    def _git(self, args: List[str]) -> str:
        completed = subprocess.run(["git"] + args, cwd=str(self.root), capture_output=True, text=True, timeout=30, check=False)
        return (completed.stdout + completed.stderr).strip()

    def git_status(self) -> str:
        return self._git(["status", "--short"])

    def git_diff(self) -> str:
        return self._git(["diff", "--"])

    def run_tests(self, path: str) -> Dict[str, Any]:
        test_path = self._path(path)
        if not test_path.exists():
            return {"command": [], "returncode": 2, "exit_code": 2, "stdout": "", "stderr": "test_path_not_found", "duration": 0.0}
        if test_path.is_file():
            command = [sys.executable, str(test_path)]
        else:
            command = [sys.executable, "-m", "unittest", "discover", "-s", str(test_path)]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(self.root) + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
        started = time.perf_counter()
        completed = subprocess.run(command, cwd=str(self.root), env=environment, capture_output=True, text=True, timeout=180, check=False)
        return {"command": command, "returncode": completed.returncode, "exit_code": completed.returncode, "stdout": completed.stdout[-20000:], "stderr": completed.stderr[-10000:], "duration": round(time.perf_counter() - started, 3)}

    def write_file(self, relative: str, content: str) -> str:
        path = self._path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path.relative_to(self.root))

    def patch_file(self, relative: str, old: str, new: str) -> str:
        path = self._path(relative)
        content = path.read_text(encoding="utf-8")
        if content.count(old) != 1:
            raise ValueError("patch_context_not_unique")
        path.write_text(content.replace(old, new), encoding="utf-8")
        return str(path.relative_to(self.root))
