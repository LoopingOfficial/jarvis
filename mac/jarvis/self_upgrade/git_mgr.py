"""Opérations Git directes pour les workspaces d'upgrade."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def _run(cmd: list[str], cwd: str | Path | None = None, timeout: float = 60.0) -> dict[str, Any]:
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "timeout", "returncode": -1}
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc), "returncode": -1}


class GitManager:
    def __init__(self, repo_root: str | Path) -> None:
        self._root = Path(repo_root)

    def _git(self, *args: str, timeout: float = 60.0) -> dict[str, Any]:
        return _run(["git"] + list(args), cwd=self._root, timeout=timeout)

    def is_repo(self) -> bool:
        r = self._git("rev-parse", "--is-inside-work-tree")
        return r["ok"] and "true" in r["stdout"].lower()

    def current_branch(self) -> str:
        r = self._git("rev-parse", "--abbrev-ref", "HEAD")
        return r["stdout"] if r["ok"] else ""

    def head_sha(self) -> str:
        r = self._git("rev-parse", "HEAD")
        return r["stdout"] if r["ok"] else ""

    def short_log(self, n: int = 10) -> str:
        r = self._git("log", f"--oneline", f"-{n}")
        return r["stdout"] if r["ok"] else ""

    def status(self) -> str:
        r = self._git("status", "--short")
        return r["stdout"] if r["ok"] else ""

    def diff(self, ref: str = "HEAD", staged: bool = False) -> str:
        args = ["diff"]
        if staged:
            args.append("--cached")
        args.append(ref)
        r = self._git(*args, timeout=30)
        return r["stdout"] if r["ok"] else ""

    def diff_files(self, ref: str = "HEAD") -> list[str]:
        r = self._git("diff", "--name-status", ref)
        if not r["ok"]:
            return []
        return [line.split("\t")[-1] for line in r["stdout"].splitlines() if line.strip()]

    def create_branch(self, branch: str) -> bool:
        r = self._git("checkout", "-b", branch)
        return r["ok"]

    def checkout(self, ref: str) -> bool:
        r = self._git("checkout", ref)
        return r["ok"]

    def commit(self, message: str) -> bool:
        self._git("add", "-A")
        r = self._git("commit", "-m", message)
        return r["ok"]

    def worktree_add(self, path: str | Path, branch: str, start_point: str = "HEAD") -> bool:
        r = self._git("worktree", "add", "-b", branch, str(path), start_point)
        return r["ok"]

    def worktree_remove(self, path: str | Path) -> bool:
        r = self._git("worktree", "remove", str(path), "--force")
        return r["ok"]

    def worktree_list(self) -> list[str]:
        r = self._git("worktree", "list")
        if not r["ok"]:
            return []
        return [line.split()[0] for line in r["stdout"].splitlines() if line.strip()]

    def tag(self, tag_name: str, ref: str = "HEAD", message: str = "") -> bool:
        args = ["tag"]
        if message:
            args.extend(["-m", message])
        args.extend([tag_name, ref])
        r = self._git(*args)
        return r["ok"]

    def delete_tag(self, tag_name: str) -> bool:
        r = self._git("tag", "-d", tag_name)
        return r["ok"]

    def reset_hard(self, ref: str) -> bool:
        r = self._git("reset", "--hard", ref)
        return r["ok"]
