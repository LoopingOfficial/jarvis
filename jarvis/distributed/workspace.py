"""Isolated git workspace management for missions."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Optional


class GitWorkspaceManager:
    def __init__(self, repository: str):
        self.repository = Path(repository).expanduser().resolve()
        self.workspace: Optional[Path] = None

    def repository_root(self) -> Path:
        result = subprocess.run(["git", "-C", str(self.repository), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=30, check=False)
        if result.returncode != 0:
            raise ValueError("repository_is_not_a_git_checkout")
        return Path(result.stdout.strip()).resolve()

    def create_workspace(self, mission_id: str) -> str:
        root = self.repository_root()
        path = Path(tempfile.mkdtemp(prefix="velko-" + mission_id + "-"))
        result = subprocess.run(["git", "-C", str(root), "worktree", "add", "--detach", str(path), "HEAD"], capture_output=True, text=True, timeout=60, check=False)
        if result.returncode != 0:
            path.rmdir()
            raise RuntimeError("worktree_creation_failed:" + result.stderr.strip())
        self.workspace = path
        return str(path)

    def status(self) -> str:
        return self._git(["status", "--short"])

    def diff(self) -> str:
        return self._git(["diff", "--"])

    def create_task_branch(self, branch: str) -> str:
        if not self.workspace:
            raise RuntimeError("workspace_not_created")
        result = subprocess.run(["git", "switch", "-c", branch], cwd=str(self.workspace), capture_output=True, text=True, timeout=30, check=False)
        if result.returncode != 0:
            raise RuntimeError("task_branch_creation_failed:" + result.stderr.strip())
        return branch

    def collect_changes(self) -> Dict[str, str]:
        return {"status": self.status(), "diff": self.diff()}

    def _git(self, args):
        if not self.workspace:
            raise RuntimeError("workspace_not_created")
        result = subprocess.run(["git"] + args, cwd=str(self.workspace), capture_output=True, text=True, timeout=30, check=False)
        return (result.stdout + result.stderr).strip()

    def cleanup(self) -> None:
        # Workspaces are intentionally retained by default for evidence.
        return None
