"""Harness de test partagé pour le moteur de missions multitâches.

Fournit un « core » minimal mais réel pour les tests :
  - vraie base SQLite temporaire (Database + migrations) ;
  - vrais SettingsStore / EventBus / AuditLog / PermissionManager ;
  - vrai SecureToolRunner et vrai registry d'outils ;
  - stubs uniquement pour ce qui est hors sujet (connecteurs, documents,
    tâches du chat, vault, LLM).

Le LLM est volontairement un stub qui échoue avec l'erreur réelle rencontrée
en production sans fournisseur : cela vérifie qu'aucun résultat n'est simulé.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

warnings.simplefilter("ignore", ResourceWarning)
from jarvis.audit import AuditLog
from jarvis.config import SettingsStore
from jarvis.db import Database
from jarvis.events import EventBus
from jarvis.permissions import PermissionManager
# L'import enregistre les outils machine réels (terminal.run, fs.*) dans le
# registre, comme le fait `jarvis.core` (import = enregistrement).
from jarvis.tools import system_tools  # noqa: F401
from jarvis.tools.base import registry
from jarvis.tools.runner import SecureToolRunner


def make_core(tmp_path: str | Path, *, max_background: int = 4,
              roots: list[str] | None = None) -> SimpleNamespace:
    tmp = Path(tmp_path)
    db = Database(tmp / "jarvis_test.db")
    settings = SettingsStore(db)
    roots = roots or [str(tmp)]
    settings.update("security", {"filesystem_roots": roots})
    settings.update("background", {"enabled": True, "max_background_tasks": max_background})
    events = EventBus(db)
    audit = AuditLog(db, settings=settings)
    permissions = PermissionManager(settings, audit)

    class StubConnectors:
        def raw(self, connector_id: str) -> dict[str, Any] | None:
            return None

        def active(self, connector_type: str) -> list[dict[str, Any]]:
            return []

        def routing_candidates(self, connector_type: str) -> list[dict[str, Any]]:
            return []

    class StubDocuments:
        documents: dict[str, Any] = {}

    class StubTasks:
        def get(self, task_id: str) -> None:
            return None

        def add_tool(self, task_id: str, tool_id: str) -> None:
            return None

    class StubVault:
        def scrub(self, text: Any) -> str:
            return str(text or "")

    class StubLLM:
        def chat(self, messages: Any, **kwargs: Any) -> None:
            raise RuntimeError("Aucun fournisseur de modèle disponible.")

    core = SimpleNamespace(
        db=db, settings=settings, events=events, audit=audit,
        permissions=permissions, connectors=StubConnectors(),
        documents=StubDocuments(), tasks=StubTasks(), vault=StubVault(),
        llm=StubLLM(), agents=None, registry=registry,
    )
    core.runner = SecureToolRunner(core)
    return core


class EventRecorder:
    """Enregistre les événements du bus pour vérifier l'ordre des états."""

    def __init__(self, core: Any, *event_types: str) -> None:
        self.records: dict[str, list[dict[str, Any]]] = {t: [] for t in event_types}
        for t in event_types:
            core.events.on(t, self._make_record(t))

    def _make_record(self, event_type: str) -> Callable[[dict], None]:
        def record(event: dict[str, Any]) -> None:
            self.records[event_type].append(dict(event.get("data") or {}))
        return record

    def count(self, event_type: str) -> int:
        return len(self.records.get(event_type, []))

    def first(self, event_type: str, key: str = "id") -> Any:
        items = self.records.get(event_type, [])
        return items[0].get(key) if items else None


def wait_until(predicate: Callable[[], bool], *, timeout: float = 20.0,
               interval: float = 0.02) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def make_git_repo(directory: str | Path) -> Path:
    """Crée un mini dépôt git committé, prêt pour `git worktree add`."""
    repo = Path(directory)
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / "README.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    env = {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True,
                   capture_output=True, env=env)
    return repo


def shell_sleep(ms: int = 600) -> str:
    """Commande `ping` (classée READ_ONLY, sans confirmation) utilisée comme
    sleep portable sous cmd.exe dans les plans de mission."""
    seconds = max(1, int(ms // 1000 * 2) or 1)
    return f"ping -n {seconds} 127.0.0.1 > nul"


def new_temp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="bt_"))


def cleanup_tree(path: Path) -> None:
    try:
        if path.exists():
            shutil.rmtree(path)
    except OSError:
        pass