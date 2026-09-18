"""Tests de l'isolation des missions CODE : WorktreeManager (git worktree)."""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from jarvis.background_tasks import WorktreeManager

from .background_support import new_temp_dir, cleanup_tree, make_git_repo


class WorktreeManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = new_temp_dir()
        self.addCleanup(cleanup_tree, self.tmp)
        self.repo = make_git_repo(self.tmp / "repo")
        self.manager = WorktreeManager(object())

    def test_create_worktree_on_its_own_branch(self) -> None:
        info = self.manager.create("bt_code_x", str(self.repo))
        wt = Path(info["worktree"])
        self.assertTrue((wt / ".git").exists())
        self.assertEqual(info["branch"], "task/bt_code_x")
        # La branche existe dans le dépôt parent.
        proc = subprocess.run(["git", "-C", str(self.repo), "branch", "--list", "task/bt_code_x"],
                              capture_output=True, text=True)
        self.assertIn("task/bt_code_x", (proc.stdout or "").split())

    def test_changes_and_commit(self) -> None:
        info = self.manager.create("bt_code_x", str(self.repo))
        wt = Path(info["worktree"])
        (wt / "note.txt").write_text("modif", encoding="utf-8")
        changes = self.manager.changes("bt_code_x")
        self.assertTrue(any("note.txt" in line for line in changes))
        commit = self.manager.commit("bt_code_x", "Mission JARVIS")
        self.assertTrue(commit["commit"])
        self.assertEqual(self.manager.changes("bt_code_x"), [])

    def test_ready_state(self) -> None:
        self.manager.create("bt_code_x", str(self.repo))
        ready = self.manager.ready("bt_code_x")
        self.assertEqual(ready["state"], "READY_TO_MERGE")

    def test_cleanup_removes_worktree_but_keeps_branch(self) -> None:
        info = self.manager.create("bt_code_x", str(self.repo))
        wt = Path(info["worktree"])
        self.manager.cleanup("bt_code_x")
        self.assertFalse(wt.exists())
        proc = subprocess.run(["git", "-C", str(self.repo), "branch", "--list", "task/bt_code_x"],
                              capture_output=True, text=True)
        self.assertIn("task/bt_code_x", (proc.stdout or "").split())

    def test_rejects_non_git_repo(self) -> None:
        plain = self.tmp / "not_a_repo"
        plain.mkdir()
        with self.assertRaises(RuntimeError):
            self.manager.create("bt_code_x", str(plain))


if __name__ == "__main__":
    unittest.main()