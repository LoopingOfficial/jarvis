"""Tests unitaires de l'auto-amélioration (SELF_UPGRADE_V1) — chemins protégés,
workspace Git, planification, persistance."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jarvis.config import ROOT
from jarvis.self_upgrade.git_mgr import GitManager
from jarvis.self_upgrade.history import UpgradeHistory
from jarvis.self_upgrade.paths import (ALLOWED_ZONES, PROTECTED_PATTERNS,
                                       is_protected)
from jarvis.self_upgrade.planner import UpgradePlanner
from jarvis.self_upgrade.service import (detect_python, make_upgrade_id)
from jarvis.self_upgrade.workspace import UpgradeWorkspaceManager


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@local"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)


class TestPaths(unittest.TestCase):
    def test_protected_core(self):
        protected = {
            "supervisor/jarvis_supervisor.py",
            ".env",
            ".env.local",
            "state/app_state.json",
            "backups/manifest.json",
            "releases/manifest.json",
            "data/jarvis.db",
            "jarvis/self_upgrade/service.py",
            "jarvis/self_upgrade",
            ".git/config",
        }
        for p in protected:
            self.assertTrue(is_protected(ROOT, p), f"{p} devrait être protégé")

    def test_allowed_zones(self):
        allowed = {
            "jarvis/build.py",
            "jarvis/server.py",
            "ui/js/self_upgrades.js",
            "tests/test_self_upgrade.py",
            "requirements.txt",
            "README.md",
            ".gitignore",
        }
        for p in allowed:
            self.assertFalse(is_protected(ROOT, p), f"{p} ne devrait pas être protégé")


class TestWorkspaceManager(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=ROOT.parent, prefix="su-test-"))
        _init_repo(self.tmp)
        (self.tmp / "a.txt").write_text("v1", encoding="utf-8")
        GitManager(self.tmp).commit("init")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_returns_workspace_and_branch(self):
        mgr = UpgradeWorkspaceManager(self.tmp)
        res = mgr.create("20260101-000000-test")
        self.assertTrue(res.get("ok"), res)
        ws = Path(res["workspace"])
        self.assertTrue(ws.exists())
        git = GitManager(ws)
        self.assertEqual(git.current_branch(), "upgrade/20260101-000000-test")
        head = git.head_sha()
        self.assertTrue(head, "un commit de base doit exister")

    def test_not_a_repo_errors(self):
        other = Path(tempfile.mkdtemp(dir=ROOT.parent, prefix="su-norepo-"))
        try:
            mgr = UpgradeWorkspaceManager(other)
            res = mgr.create("id")
            self.assertFalse(res.get("ok"), res)
        finally:
            shutil.rmtree(other, ignore_errors=True)


class TestGitManager(unittest.TestCase):
    def test_diff_and_commit(self):
        repo = Path(tempfile.mkdtemp(dir=ROOT.parent, prefix="su-git-"))
        try:
            _init_repo(repo)
            git = GitManager(repo)
            (repo / "a.txt").write_text("v1", encoding="utf-8")
            git.commit("init")
            head_before = git.head_sha()
            (repo / "a.txt").write_text("v2", encoding="utf-8")
            (repo / "b.txt").write_text("new", encoding="utf-8")
            self.assertIn("a.txt", git.diff_files("HEAD"))
            git.commit("second")
            self.assertEqual(git.diff_files("HEAD^"), ["a.txt", "b.txt"])
            self.assertNotEqual(git.head_sha(), head_before)
        finally:
            shutil.rmtree(repo, ignore_errors=True)


class TestHistory(unittest.TestCase):
    def setUp(self):
        self.db = mock.MagicMock()

    def test_create_and_lock(self):
        hist = UpgradeHistory(self.db)
        hist.create("id-x", "prompt", "auto", version_before="vB")
        self.db.execute.assert_called()

    def test_update_serialises_dicts(self):
        hist = UpgradeHistory(self.db)
        hist.update("id-x", plan={"objective": "o"}, status="plan_ready")
        sql, params = self.db.execute.call_args[0]
        self.assertIn("plan=?", sql)
        self.assertTrue(any(isinstance(p, str) for p in params if p))


class TestPlanner(unittest.TestCase):
    @mock.patch("jarvis.self_upgrade.planner.OllamaClient.chat")
    def test_plan_format(self, chat):
        chat.return_value = {"text": json.dumps({
            "objective": "Ajouter un endpoint consigne",
            "target_files": ["jarvis/server.py"],
            "tests_to_run": ["python -m unittest discover -s tests"],
            "verification": "/api/self-upgrade/build-id",
        })}
        planner = UpgradePlanner(ROOT, "http://127.0.0.1:11434", "m")
        res = planner.plan("prompt", "AUTO")
        self.assertTrue(res.get("ok"), res)
        plan = res["plan"]
        self.assertIn("objective", plan)
        self.assertIn("target_files", plan)
        self.assertEqual(plan["verification"], "/api/self-upgrade/build-id")

    @mock.patch("jarvis.self_upgrade.planner.OllamaClient.chat")
    def test_plan_fallback_on_bad_json(self, chat):
        chat.return_value = {"text": "pas du json"}
        planner = UpgradePlanner(ROOT, "http://127.0.0.1:11434", "m")
        res = planner.plan("fais un truc", "AUTO")
        self.assertTrue(res["plan"]["objective"] == "fais un truc")
        self.assertFalse(res["ok"])


class TestMisc(unittest.TestCase):
    def test_make_upgrade_id(self):
        import re
        base = make_upgrade_id("Construis une fonctionnalité de consigne !")
        self.assertIsNotNone(re.match(r"^\d{8}-\d{6}-", base))
        # le slug est limité à 30 caractères
        self.assertTrue(base.endswith("-construis-une-fonctionnalit-de") and len(base.split("-", 2)[2]) <= 30)

    def test_detect_python(self):
        p = detect_python()
        self.assertTrue(p.strip())


if __name__ == "__main__":
    unittest.main()