"""Gestion des workspaces Git isolés pour chaque upgrade."""
from __future__ import annotations

import os
import time
from pathlib import Path

from .git_mgr import GitManager
from . import paths

UPGRADE_WORKSPACES_DIR = "upgrade-workspaces"
BRANCH_PREFIX = "upgrade/"


class UpgradeWorkspaceManager:
    def __init__(self, project_root: Path) -> None:
        self._root = Path(project_root)
        self._ws_dir = self._root / UPGRADE_WORKSPACES_DIR
        self._ws_dir.mkdir(parents=True, exist_ok=True)
        self._git = GitManager(self._root)

    def workspace_dir(self, upgrade_id: str) -> Path:
        return self._ws_dir / upgrade_id

    @staticmethod
    def branch_name(upgrade_id: str) -> str:
        return f"{BRANCH_PREFIX}{upgrade_id}"

    def created(self, upgrade_id: str) -> bool:
        return self.workspace_dir(upgrade_id).exists()

    def create(self, upgrade_id: str) -> dict:
        branch = self.branch_name(upgrade_id)
        ws = self.workspace_dir(upgrade_id)
        if ws.exists():
            return {"ok": True, "workspace": str(ws), "branch": branch, "exists": True}
        base = self._git.head_sha()
        if not self._git.is_repo():
            return {"ok": False, "error": "Le projet n'est pas un dépôt Git."}
        if not self._git.worktree_add(ws, branch, base):
            return {"ok": False, "error": "Impossible de créer le worktree Git.", "branch": branch}
        return {"ok": True, "workspace": str(ws), "branch": branch, "base_commit": base}

    def remove(self, upgrade_id: str) -> bool:
        ws = self.workspace_dir(upgrade_id)
        if ws.exists():
            self._git.worktree_remove(ws)
        try:
            self._git("branch", "-D", self.branch_name(upgrade_id))
        except Exception:
            pass
        return not ws.exists()

    def list_workspaces(self) -> list[str]:
        if not self._ws_dir.exists():
            return []
        return [d.name for d in self._ws_dir.iterdir() if d.is_dir()]

    def release_cleanup(self, upgrade_id: str) -> None:
        ws = self.workspace_dir(upgrade_id)
        if ws.exists():
            try:
                self._git.worktree_remove(ws)
            except Exception:
                pass
        try:
            self._git("branch", "-D", self.branch_name(upgrade_id))
        except Exception:
            pass


class RollbackStorage:
    """Espace de stockage pré-promotion : copie de secours des fichiers modifiés."""

    BACKUP_DIR = "backups/pre-upgrade"

    def __init__(self, project_root: Path) -> None:
        self._root = Path(project_root)
        self._base = self._root / BACKUP_DIR
        self._base.mkdir(parents=True, exist_ok=True)

    def snapshot_file(self, upgrade_id: str, rel_path: str) -> Path | None:
        """Copie un fichier (modifié) avant promotion pour permettre un rollback réel."""
        src = self._root / rel_path
        if not src.exists():
            return None
        dest_dir = self._base / upgrade_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(src, dest)
        return dest

    def snapshot_workspace(self, upgrade_id: str, workspace: Path) -> list[str]:
        """Sauvegarde tous les fichiers modifiés (par git status) avant promotion."""
        git = GitManager(workspace)
        modified = []
        for rel in git.diff_files("HEAD"):
            rel = rel.replace("\\", "/")
            if rel and not paths.is_protected(self._root, rel):
                copied = self.snapshot_file(upgrade_id, rel)
                modified.append(rel)
        return modified