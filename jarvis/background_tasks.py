"""Moteur de missions multitâches en arrière-plan.

Ce module ajoute à JARVIS la capacité d'exécuter PLUSIEURS missions en même
temps, dans un pool de workers dédiés, sans bloquer le chat (le thread
principal ne lance jamais une mission lui-même).

Composants :

- `ResourceScheduler` : sémaphores par ressource (LLM_GPU max 1, CPU, réseau,
  fichiers, git, navigateur). Une mission déclare ses ressources ; le
  scheduler ne la démarre que lorsque toutes sont disponibles. Le slot LLM_GPU
  est aussi acquis, puis libéré, AUTOUR de chaque inférence réelle.
- `LockRegistry` : verrous de concurrence par cible (`project:<path>`,
  `git:<repository>`, `file:<path>`, `database:<name>`). Deux missions ne
  touchent jamais le même fichier ou dépôt en même temps.
- `WorktreeManager` : missions `CODE` isolées dans un worktree git
  (`.worktrees/task-<task_id>/`, branche `task/<task_id>`). Aucun auto-merge :
  la mission se termine en READY_TO_MERGE avec sa branche et son commit.
- `TaskStore` : persistance SQLite (tables `background_tasks`,
  `background_task_logs`, `background_task_artifacts`) + reprise après crash :
  une mission COMPLETED reste complète, une mission QUEUED repart, une mission
  RUNNING au moment du redémarrage est marquée comme interrompue.
- `BackgroundTaskManager` : orchestrateur (file d'attente, priorités,
  dépendances, pause / reprise / annulation, WAITING_USER).
- `TaskContext` : contexte isolé par mission (logs, progression, pose de
  question, LLM réel, tools réels via SecureToolRunner, artefacts).

Aucun résultat n'est simulé : les modèles passent par le vrai `LLMManager`,
les outils par le vrai `SecureToolRunner`, les agents par les vrais `AGENTS`.
Si un composant est indisponible (ex. aucun fournisseur de modèle), la mission
échoue avec l'erreur RÉELLE du composant.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import agents as agents_module
from .db import dumps, loads, new_id, row_to_dict
from .tools.base import ToolResult, registry
from .tools.runner import ConfirmationRequired, ToolDenied

# ---------------------------------------------------------------------------
# États du moteur.
# Ils reprennent les états de Mission Control quand ils existent
# (WAITING/THINKING/RUNNING/TOOL/VERIFYING/COMPLETED/FAILED) et ajoutent les
# états propres au multitâches : attente de ressource/verrou, attente
# utilisateur, pause, blocage par dépendance et prêt à merger (jamais
# auto-merge). Aucun vocabulaire incompatible n'est introduit.
# ---------------------------------------------------------------------------
QUEUED = "QUEUED"
WAITING_RESOURCE = "WAITING_RESOURCE"
WAITING_USER = "WAITING_USER"
RUNNING = "RUNNING"
THINKING = "THINKING"
TOOL = "TOOL"
VERIFYING = "VERIFYING"
PAUSED = "PAUSED"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
CANCELLED = "CANCELLED"
BLOCKED = "BLOCKED"
READY_TO_MERGE = "READY_TO_MERGE"

STATES = (
    QUEUED, WAITING_RESOURCE, WAITING_USER, RUNNING, THINKING, TOOL,
    VERIFYING, PAUSED, COMPLETED, FAILED, CANCELLED, BLOCKED, READY_TO_MERGE,
)
PENDING_STATES = {QUEUED, WAITING_RESOURCE}
RUN_IN_PROGRESS = {RUNNING, THINKING, TOOL, VERIFYING}
TERMINAL_STATES = {COMPLETED, FAILED, CANCELLED, READY_TO_MERGE}

PRIORITIES = ("LOW", "NORMAL", "HIGH", "URGENT")
_PRIORITY_RANK = {label: i for i, label in enumerate(PRIORITIES)}

TASK_TYPES = ("CODE", "DOCUMENT", "REPORT", "GENERIC_AGENT")

# Ressources connues. Une resource inconnue obtient une capacité de 1 par
# défaut : mieux vaut sérialiser que laisser deux missions se marcher dessus.
RESOURCE_NAMES = ("LLM_GPU", "CPU", "NETWORK", "FILESYSTEM", "GIT", "BROWSER")


def _default_limits() -> dict[str, int]:
    cores = max(2, os_cpu_count())
    return {
        "LLM_GPU": 1,
        "CPU": cores,
        "NETWORK": max(2, cores // 2),
        "FILESYSTEM": 8,
        "GIT": 1,
        "BROWSER": 1,
    }


def os_cpu_count() -> int:
    return max(1, int(os.cpu_count() or 4))


# ---------------------------------------------------------------------------
# Exceptions de contrôle
# ---------------------------------------------------------------------------
class TaskCancelled(Exception):
    """Levée par un point de contrôle quand la mission est annulée."""

    def __init__(self, task_id: str = "") -> None:
        super().__init__(f"Mission {task_id} annulée.")
        self.task_id = task_id


# ---------------------------------------------------------------------------
# ResourceScheduler
# ---------------------------------------------------------------------------
class ResourceSlot:
    """Créneau d'une ressource : capacité totale et répartition par mission."""

    def __init__(self, capacity: int) -> None:
        self.capacity = max(1, int(capacity))
        self.used = 0
        self.holders: dict[str, int] = {}

    def snapshot(self) -> dict[str, Any]:
        return {
            "capacity": self.capacity, "used": self.used,
            "free": max(0, self.capacity - self.used),
            "holders": {tid: share for tid, share in self.holders.items()},
        }


class ResourceScheduler:
    """Arbitrage des ressources entre les missions.

    `acquire` est non-bloquant (utilisé par le scheduler avant de démarrer une
    mission) ; `acquire_blocking` est utilisé au milieu d'une exécution (slot
    LLM_GPU autour d'une inférence) et reste coopératif : il abandonne dès que
    la mission est en pause ou annulée.
    """

    def __init__(self, limits: dict[str, int] | None = None) -> None:
        self._lock = threading.RLock()
        raw = dict(limits or {})
        self._limits: dict[str, int] = {}
        self._slots: dict[str, ResourceSlot] = {}
        for name in RESOURCE_NAMES:
            cap = int(raw.pop(name.upper(), _default_limits()[name]))
            self._limits[name.upper()] = cap
            self._slots[name.upper()] = ResourceSlot(cap)
        for name, cap in raw.items():
            if name.upper() in self._slots:
                continue
            cap = max(1, int(cap))
            self._limits[name.upper()] = cap
            self._slots[name.upper()] = ResourceSlot(cap)

    @staticmethod
    def normalize(resources: Any) -> list[tuple[str, int]]:
        """Transforme une déclaration de ressources en liste (nom, part)."""
        out: list[tuple[str, int]] = []
        if not resources:
            return out
        if isinstance(resources, dict):
            for name, count in resources.items():
                count = int(count) if isinstance(count, (int, float)) else 1
                if count > 0:
                    out.append((str(name).upper(), count))
            return out
        if isinstance(resources, str):
            resources = [resources]
        for name in resources:
            count = 1
            if isinstance(name, (list, tuple)) and len(name) == 2:
                count = int(name[1]) if isinstance(name[1], (int, float)) else 1
                name = name[0]
            if count > 0:
                out.append((str(name).upper(), count))
        return out

    # -- acquisitions ------------------------------------------------------
    def acquire(self, task_id: str, resources: Any) -> bool:
        """Tente d'acquérir toutes les ressources d'un coup. Atomique."""
        wanted = self.normalize(resources)
        if not wanted:
            return True
        with self._lock:
            for name, count in wanted:
                slot = self._slots.setdefault(name, ResourceSlot(self._limits.get(name, 1)))
                if slot.used + count > slot.capacity:
                    return False
            for name, count in wanted:
                slot = self._slots.setdefault(name, ResourceSlot(self._limits.get(name, 1)))
                slot.used += count
                slot.holders[task_id] = slot.holders.get(task_id, 0) + count
            return True

    def acquire_blocking(self, task_id: str, resources: Any,
                         engine: "BackgroundTaskManager") -> None:
        """Attend que les ressources soient libres, de façon coopérative."""
        wanted = self.normalize(resources)
        if not wanted:
            return
        notified = False
        while not self.acquire(task_id, wanted):
            if not notified:
                engine._enter_waiting_resource(task_id, "ressources occupées")
                notified = True
            engine._checkpoint(task_id)
            time.sleep(0.25)
        if notified:
            engine._leave_waiting_resource(task_id)

    # -- libérations -------------------------------------------------------
    def release(self, task_id: str, resources: Any = None) -> None:
        names = {c[0] for c in self.normalize(resources)}
        with self._lock:
            for name, slot in self._slots.items():
                if names and name not in names:
                    continue
                share = slot.holders.pop(task_id, 0)
                if share:
                    slot.used = max(0, slot.used - share)

    def release_all(self, task_id: str) -> None:
        self.release(task_id)

    # -- lecture -----------------------------------------------------------
    def has(self, task_id: str) -> bool:
        with self._lock:
            return any(task_id in slot.holders for slot in self._slots.values())

    def holders(self) -> list[dict[str, Any]]:
        with self._lock:
            into: dict[str, list[str]] = {}
            for name, slot in self._slots.items():
                for tid, share in slot.holders.items():
                    into.setdefault(tid, []).append(f"{name}×{share}")
            return [{"task_id": tid, "resources": sorted(res)} for tid, res in into.items()]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {name: slot.snapshot() for name, slot in self._slots.items()}


# ---------------------------------------------------------------------------
# LockRegistry
# ---------------------------------------------------------------------------
class LockInfo:
    __slots__ = ("name", "holder", "acquired_at")

    def __init__(self, name: str, holder: str) -> None:
        self.name = name
        self.holder = holder
        self.acquired_at = time.time()


class LockRegistry:
    """Verrous de concurrence par cible réelle.

    Clefs canoniques :
        project:<path>  — un dossier projet entier
        git:<repository> — un dépôt git
        file:<path>     — un fichier précis
        database:<name> — une base de données nommée
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._locks: dict[str, LockInfo] = {}

    # -- construction de clefs --------------------------------------------
    @staticmethod
    def key(kind: str, value: str) -> str:
        kind = str(kind).lower()
        value = str(value or "").strip()
        if kind in {"project", "git", "file"}:
            try:
                value = str(Path(value).expanduser().resolve())
            except Exception:
                value = str(Path(value).expanduser())
        elif kind == "database":
            value = value.casefold()
        return f"{kind}:{value}"

    def project(self, path: str) -> str:
        return self.key("project", path)

    def git(self, repo: str) -> str:
        return self.key("git", repo)

    def file(self, path: str) -> str:
        return self.key("file", path)

    def database(self, name: str) -> str:
        return self.key("database", name)

    def lock_names_for(self, resources: Any, workspace: str = "") -> list[str]:
        """Traduit les verrous déclarés en clefs canoniques.

        Accepte soit des tuples (kind, value) soit des strings `file:` etc.
        Un workspace de mission `project:<path>` est préréglé si non fourni.
        """
        names: list[str] = []
        if workspace:
            names.append(self.project(workspace))
        for item in resources or []:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                names.append(self.key(str(item[0]), str(item[1])))
                continue
            text = str(item)
            if ":" in text:
                kind, _, value = text.partition(":")
                names.append(self.key(kind, value))
        return self._dedupe(names)

    @staticmethod
    def _dedupe(names: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for name in names:
            if name and name not in seen:
                seen.add(name)
                out.append(name)
        return out

    # -- verrouillage ------------------------------------------------------
    def acquire(self, task_id: str, names: list[str], wait: bool = False) -> bool:
        names = self._dedupe(names)
        if not names:
            return True
        with self._lock:
            if all(name not in self._locks for name in names):
                for name in names:
                    self._locks[name] = LockInfo(name, task_id)
                return True
        if wait:
            deadline = time.time() + 5.0
            while time.time() < deadline:
                time.sleep(0.1)
                with self._lock:
                    if all(name not in self._locks for name in names):
                        for name in names:
                            self._locks[name] = LockInfo(name, task_id)
                        return True
        return False

    def release(self, task_id: str, name: str | None = None) -> None:
        with self._lock:
            if name is not None:
                info = self._locks.get(name)
                if info and info.holder == task_id:
                    del self._locks[name]
                return
            for key in [k for k, v in self._locks.items() if v.holder == task_id]:
                del self._locks[key]

    def release_all(self, task_id: str) -> None:
        self.release(task_id)

    # -- lecture -----------------------------------------------------------
    def holding(self, task_id: str) -> list[str]:
        with self._lock:
            return [k for k, v in self._locks.items() if v.holder == task_id]

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{"name": name, "holder": info.holder, "acquired_at": info.acquired_at}
                    for name, info in self._locks.items()]


# ---------------------------------------------------------------------------
# WorktreeManager
# ---------------------------------------------------------------------------
class WorktreeManager:
    """Missions CODE isolées via `git worktree`.

    Toutes les modifications vivent dans `.worktrees/task-<task_id>` du dépôt,
    sur la branche `task/<task_id>`. La mission s'arrête à READY_TO_MERGE :
    rien n'est jamais fusionné automatiquement.
    """

    def __init__(self, core: Any) -> None:
        self._core = core
        self._visible: dict[str, dict[str, Any]] = {}

    def _git_exe(self) -> str:
        exe = shutil.which("git")
        if not exe:
            raise RuntimeError(
                "git est introuvable sur cette machine : impossible de créer un worktree.")
        return exe

    def _run(self, args: list[str], *, cwd: Path | None = None, timeout: int = 180) -> subprocess.CompletedProcess:
        proc = subprocess.run([self._git_exe(), *args], capture_output=True, text=True,
                              cwd=str(cwd) if cwd else None, timeout=timeout)
        return proc

    # -- création ----------------------------------------------------------
    def create(self, task_id: str, repo: str, branch: str | None = None) -> dict[str, Any]:
        repo_path = Path(str(repo)).expanduser().resolve()
        if not repo_path.is_dir() or not (repo_path / ".git").exists():
            raise RuntimeError(f"{repo_path} n'est pas un dépôt git.")
        branch = branch or f"task/{task_id}"
        worktree_path = repo_path / ".worktrees" / f"task-{task_id}"
        # Reprise : le worktree existe déjà après un redémarrage.
        if (worktree_path / ".git").exists():
            info = {"worktree": str(worktree_path), "branch": branch,
                    "repo": str(repo_path), "state": "WORKING"}
            self._visible[task_id] = info
            return dict(info)
        branches = self._run(["branch", "--list", branch], cwd=repo_path)
        exists = branch in (branches.stdout or "").split()
        if exists:
            proc = self._run(["worktree", "add", "--quiet", str(worktree_path), "--detach", branch],
                             cwd=repo_path)
        else:
            proc = self._run(["worktree", "add", "--quiet", str(worktree_path), "-b", branch],
                             cwd=repo_path)
        if proc.returncode != 0:
            raise RuntimeError(f"git worktree add a échoué : {(proc.stderr or proc.stdout).strip()[:400]}")
        info = {"worktree": str(worktree_path), "branch": branch,
                "repo": str(repo_path), "state": "WORKING"}
        self._visible[task_id] = info
        return dict(info)

    # -- lecture -----------------------------------------------------------
    def info(self, task_id: str) -> dict[str, Any] | None:
        info = self._visible.get(task_id)
        return dict(info) if info else None

    def path(self, task_id: str) -> str:
        info = self._visible.get(task_id)
        if not info:
            raise RuntimeError(f"Aucun worktree pour la mission {task_id}.")
        return info["worktree"]

    def changes(self, task_id: str) -> list[str]:
        wt = Path(self.path(task_id))
        proc = self._run(["status", "--porcelain"], cwd=wt)
        return [line[:300] for line in (proc.stdout or "").splitlines() if line.strip()]

    def commit(self, task_id: str, message: str = "Mission JARVIS") -> dict[str, Any]:
        wt = Path(self.path(task_id))
        changes = self.changes(task_id)
        if not changes:
            raise RuntimeError("Rien à committer : le worktree ne contient aucune modification.")
        add = self._run(["add", "-A"], cwd=wt)
        if add.returncode != 0:
            raise RuntimeError(f"git add a échoué : {add.stderr.strip()[:300]}")
        commit = self._run(
            ["-c", "user.name=JARVIS", "-c", "user.email=jarvis@local", "commit", "-q", "-m", message[:400]],
            cwd=wt)
        if commit.returncode != 0:
            raise RuntimeError(f"git commit a échoué : {commit.stderr.strip()[:300]}")
        rev = self._run(["rev-parse", "--short", "HEAD"], cwd=wt)
        return {"commit": (rev.stdout or "").strip(), "changes": changes}

    def ready(self, task_id: str) -> dict[str, Any]:
        info = self._visible.get(task_id)
        if info:
            info["state"] = "READY_TO_MERGE"
        return {**info} if info else {}

    def cleanup(self, task_id: str) -> None:
        info = self._visible.pop(task_id, None)
        if not info:
            return
        repo = Path(info["repo"])
        wt = Path(info["worktree"])
        if wt.exists():
            try:
                # `--quiet` n'existe pas pour `worktree remove` (uniquement le add).
                self._run(["worktree", "remove", "--force", str(wt)], cwd=repo, timeout=60)
            except Exception:
                pass
        # La branche `task/<id>` est conservée délibérément : c'est elle qui
        # porte le diff à merger par un humain.

    def snapshot(self) -> list[dict[str, Any]]:
        return [dict(info) for info in self._visible.values()]


# ---------------------------------------------------------------------------
# TaskStore
# ---------------------------------------------------------------------------
_TASK_FIELDS = (
    "task_id", "mission_id", "title", "description", "task_type", "priority",
    "status", "agent", "workspace", "resources", "dependencies", "context",
    "metadata", "step", "note", "result", "error", "progress",
    "created_at", "started_at", "updated_at", "completed_at",
)


class TaskStore:
    """Persistance SQLite des missions en arrière-plan."""

    def __init__(self, db: Any) -> None:
        self._db = db

    def create(self, fields: dict[str, Any]) -> None:
        cols = list(_TASK_FIELDS)
        values = [fields.get(c) for c in cols]
        # Colonnes JSON : sérialisées avant l'écriture (le dumps gère le None).
        for index, col in enumerate(cols):
            if col in ("resources", "dependencies", "context", "metadata"):
                value = values[index] if values[index] is not None else {}
                values[index] = dumps(value)
        self._db.execute(
            "INSERT INTO background_tasks ({}) VALUES ({})".format(
                ", ".join(cols), ", ".join(["?"] * len(cols))), values)

    def get(self, task_id: str) -> dict[str, Any] | None:
        row = self._db.one("SELECT * FROM background_tasks WHERE task_id=?", (task_id,))
        if row is None:
            return None
        data = row_to_dict(row, json_fields=("resources", "dependencies", "context", "metadata")) or {}
        return data

    def set(self, task_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        self._db.execute(f"UPDATE background_tasks SET {cols} WHERE task_id=?",
                         (*fields.values(), task_id))

    def status(self, task_id: str) -> str:
        return str(self._db.scalar("SELECT status FROM background_tasks WHERE task_id=?", (task_id,)) or "?")

    def list(self, status: str = "", limit: int = 50) -> list[dict[str, Any]]:
        if status:
            rows = self._db.query(
                "SELECT * FROM background_tasks WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, min(max(1, int(limit)), 500)))
        else:
            rows = self._db.query(
                "SELECT * FROM background_tasks ORDER BY created_at DESC LIMIT ?",
                (min(max(1, int(limit)), 500),))
        return [row_to_dict(r, json_fields=("resources", "dependencies", "context", "metadata"))
                or {} for r in rows]

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = dict.fromkeys(STATES, 0)
        for row in self._db.query("SELECT status, COUNT(*) AS n FROM background_tasks GROUP BY status"):
            counts[row["status"]] = int(row["n"])
        counts["total"] = sum(counts.values())
        return counts

    # -- logs & artefacts --------------------------------------------------
    def add_log(self, task_id: str, message: str, level: str = "info", data: Any = None) -> None:
        try:
            self._db.execute(
                "INSERT INTO background_task_logs(task_id, ts, level, message, data) VALUES(?,?,?,?,?)",
                (task_id, time.time(), level, str(message)[:2000], dumps(data or {})))
        except Exception:
            pass

    def logs(self, task_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._db.query(
            "SELECT * FROM background_task_logs WHERE task_id=? ORDER BY id DESC LIMIT ?",
            (task_id, min(max(1, int(limit)), 500)))
        out = []
        for r in reversed(rows):
            item = {k: r[k] for k in r.keys()}
            item["data"] = loads(item.get("data"), {})
            out.append(item)
        return out

    def add_artifact(self, task_id: str, path: str, artifact_type: str = "file",
                     name: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        now = time.time()
        self._db.execute(
            "INSERT INTO background_task_artifacts(task_id, path, artifact_type, name, metadata, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (task_id, str(path)[:1200], str(artifact_type)[:60], str(name or Path(str(path)).name)[:200],
             dumps(metadata or {}), now))
        return {"id": self._db.conn().execute("SELECT last_insert_rowid()").fetchone()[0],
                "task_id": task_id, "path": str(path)[:1200], "artifact_type": str(artifact_type)[:60],
                "name": str(name or Path(str(path)).name)[:200], "metadata": metadata or {}, "created_at": now}

    def artifacts(self, task_id: str) -> list[dict[str, Any]]:
        rows = self._db.query(
            "SELECT * FROM background_task_artifacts WHERE task_id=? ORDER BY id", (task_id,))
        out = []
        for r in rows:
            item = {k: r[k] for k in r.keys()}
            item["metadata"] = loads(item.get("metadata"), {})
            out.append(item)
        return out


# ---------------------------------------------------------------------------
# TaskContext
# ---------------------------------------------------------------------------
class TaskContext:
    """Contexte de travail isolé pour une mission en arrière-plan.

    Chaque mission reçoit le sien : les logs, la progression, les questions
    utilisateur et les artefacts d'une mission ne sont jamais visibles par une
    autre. Le LLM, les tools et les agents manipulés ici sont les VRAIS
    composants de JARVIS (LLMManager, SecureToolRunner, AGENTS).
    """

    def __init__(self, engine: "BackgroundTaskManager", core: Any, task_id: str) -> None:
        self.engine = engine
        self._core = core
        self._task_id = task_id

    # -- identité ----------------------------------------------------------
    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def task(self) -> dict[str, Any]:
        return self.engine.get(self._task_id) or {}

    @property
    def workspace(self) -> str:
        return str(self.task.get("workspace") or "")

    def agent_spec(self) -> agents_module.AgentSpec | None:
        return self._core.agents.spec(str(self.task.get("agent") or "jarvis")) \
            if self._core.agents else None

    def semantics(self, key: str, default: Any = None) -> Any:
        return (self.task.get("metadata") or {}).get(key, default)

    # -- points de contrôle -------------------------------------------------
    def checkpoint(self) -> None:
        self.engine._checkpoint(self._task_id)

    # -- traçabilité --------------------------------------------------------
    def log(self, message: str, level: str = "info", data: Any = None) -> None:
        self.engine.log(self._task_id, message, level=level, data=data)

    def set_progress(self, value: float) -> None:
        self.engine.set_progress(self._task_id, value)

    def set_phase(self, phase: str, progress: float | None = None) -> None:
        self.engine.set_phase(self._task_id, phase, progress)

    def add_artifact(self, path: str, artifact_type: str = "file", name: str = "",
                     metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.engine.store.add_artifact(self._task_id, path, artifact_type, name, metadata)

    # -- utilisateur --------------------------------------------------------
    def ask_user(self, prompt: str) -> str:
        return self.engine.ask_user(self._task_id, prompt)

    # -- LLM réel -----------------------------------------------------------
    def llm(self, messages: Any, *, role: str = "default",
            tools: list[dict[str, Any]] | None = None, temperature: float | None = None,
            max_tokens: int = 2048) -> Any:
        self.set_phase("Réflexion du modèle")
        self.engine._set_status(self._task_id, THINKING)
        self.engine.events.emit("llm.started", {"task_id": self._task_id})
        self.engine.resources.acquire_blocking(self._task_id, ["LLM_GPU"], self.engine)
        try:
            return self._core.llm.chat(messages, role=role, tools=tools,
                                       temperature=temperature, max_tokens=max_tokens)
        finally:
            self.engine.resources.release(self._task_id, ["LLM_GPU"])

    def agent_tools(self, spec: agents_module.AgentSpec | None) -> list[Any]:
        try:
            if spec is not None and self._core.agents:
                allowed = self._core.agents.allowed_tools(spec.id, self._core.registry)
                if allowed is not None:
                    return list(allowed)
        except Exception:
            pass
        return [t for t in self._core.registry.all() if t.enabled]

    # -- tools réels --------------------------------------------------------
    def run_tool(self, tool_id: str, arguments: dict[str, Any] | None = None,
                 *, agent: str = "jarvis", confirmed: bool = False) -> ToolResult:
        args = dict(arguments or {})
        try:
            result = self._core.runner.run(tool_id, args, agent=agent,
                                           task_id=self._task_id, confirmed=confirmed)
        except ConfirmationRequired as exc:
            answer = self.ask_user(f"La mission demande une confirmation : {exc.message}")
            if str(answer).strip().lower() in ("oui", "yes", "ok", "vale", "valide", "approuve"):
                result = self._core.runner.run(tool_id, args, agent=agent,
                                               task_id=self._task_id, confirmed=True)
            else:
                result = ToolResult(False, "Refusé par l'utilisateur lors de la confirmation.")
                self.engine.events.emit("tool.denied", {"tool": tool_id, "reason": result.output,
                                                        "agent": agent, "task_id": self._task_id})
        except ToolDenied as exc:
            result = ToolResult(False, str(exc))
            self.engine.events.emit("tool.denied", {"tool": tool_id, "reason": str(exc),
                                                    "agent": agent, "task_id": self._task_id})
        except Exception as exc:
            result = ToolResult(False, f"{tool_id}: {exc}")
        level = "info" if result.ok else "error"
        self.log(f"Outil {tool_id} : {result.output[:400]}", level=level,
                 data={"tool": tool_id, "ok": result.ok})
        for artifact in getattr(result, "artifacts", []) or []:
            if isinstance(artifact, dict) and artifact.get("path"):
                self.add_artifact(str(artifact["path"]),
                                  str(artifact.get("type") or "file"))
        return result


# ---------------------------------------------------------------------------
# Handlers de missions (types réels, pas de résultats simulés)
# ---------------------------------------------------------------------------
def _execute_plan(ctx: TaskContext, plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Exécute des étapes déterminées (outils réels via SecureToolRunner).

    Le plan est fourni par celui qui lance la mission (API, workflow,
    utilisateur) : c'est un pipeline déterministe documenté, comme le
    compare/sync Sheet. Les outils exécutés sont les vrais outils JARVIS.
    """
    results: list[dict[str, Any]] = []
    cwd = ctx.workspace or None
    for index, step in enumerate(plan, start=1):
        ctx.checkpoint()
        step = dict(step or {})
        if step.get("ask_user"):
            # Étape « question à l'utilisateur » : la mission se met en
            # WAITING_USER et ne reprend qu'une fois la réponse donnée.
            answer = ctx.ask_user(str(step["ask_user"])[:300])
            results.append({"step": index, "tool": "ask_user", "ok": True,
                            "output": str(answer)[:200]})
            continue
        shell = step.get("shell")
        if shell:
            tool_id = "terminal.run"
            arguments = {"command": str(shell), "timeout": int(step.get("timeout") or 180)}
            if step.get("cwd") or cwd:
                arguments["cwd"] = str(step.get("cwd") or cwd)
        else:
            tool_id = str(step.get("tool") or "")
            if not tool_id:
                raise RuntimeError(f"Étape {index} du plan sans outil (tool) ni commande (shell).")
            arguments = dict(step.get("arguments") or {})
        result = ctx.run_tool(tool_id, arguments)
        preview = str(result.output or "")[:400]
        results.append({"step": index, "tool": tool_id, "ok": result.ok, "output": preview})
        if not result.ok:
            raise RuntimeError(f"Plan : étape {index} ({tool_id}) en échec — {preview}")
    return results


def _llm_agent_loop(ctx: TaskContext) -> dict[str, Any]:
    """Boucle agentique RÉELLE : vrai LLMManager + vrais tools + vrai agent."""
    core = ctx._core
    spec = ctx.agent_spec()
    max_iterations = int((spec.max_iterations if spec else 12) or 12)
    system = ""
    if spec and spec.system_prompt:
        system = spec.system_prompt.replace("{user}", str(core.settings.get("general", "user_name", "")))
    prompt = ctx.task.get("description") or ctx.task.get("title") or ""
    if not system:
        system = "Tu es un agent JARVIS qui exécute une mission en arrière-plan."
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": str(prompt)[:12000]}]
    tools = ctx.agent_tools(spec)
    schemas = [t.llm_schema() for t in tools][:40]
    for iteration in range(1, max_iterations + 1):
        ctx.checkpoint()
        ctx.set_phase(f"Iteration {iteration}")
        if not schemas:
            schemas = None
        response = ctx.llm(messages, role=spec.model_role if spec else "default",
                           tools=schemas, max_tokens=2048)
        if not response.ok:
            raise RuntimeError(str(response.error or "Le modèle n'a pas répondu."))
        if not getattr(response, "tool_calls", None):
            text = str(response.text or "").strip() or "Mission terminée."
            ctx.set_progress(1.0)
            return {"result": text}
        for call in list(response.tool_calls)[:6]:
            ctx.checkpoint()
            result = ctx.run_tool(str(call.name), dict(call.arguments or {}),
                                  agent=spec.id if spec else "jarvis")
            messages.append({
                "role": "tool", "tool_call_id": str(call.id or ""),
                "content": str(result.output or "")[:4000]})
    raise RuntimeError(f"Mission abandonnée : plus de {max_iterations} itérations.")


def _handle_generic(ctx: TaskContext) -> dict[str, Any]:
    plan = ctx.semantics("plan") or []
    if plan:
        results = _execute_plan(ctx, plan)
        ok = sum(1 for r in results if r["ok"])
        summary = f"Plan exécuté : {ok} étape(s) sur {len(results)} réussies."
        ctx.log(summary)
        ctx.set_progress(1.0)
        return {"result": summary}
    return _llm_agent_loop(ctx)


def _handle_report(ctx: TaskContext) -> dict[str, Any]:
    outcome = _handle_generic(ctx)
    return outcome


def _handle_document(ctx: TaskContext) -> dict[str, Any]:
    plan = ctx.semantics("plan") or []
    for path in (ctx.semantics("artifacts") or []):
        if path:
            ctx.add_artifact(str(path), "document", metadata={"mission_type": "DOCUMENT"})
    if plan:
        results = _execute_plan(ctx, plan)
        ok = sum(1 for r in results if r["ok"])
        summary = f"Document produit : {ok} étape(s) du plan réussies."
        ctx.log(summary)
        ctx.set_progress(1.0)
        return {"result": summary}
    return _llm_agent_loop(ctx)


def _handle_code(ctx: TaskContext) -> dict[str, Any]:
    repo = ctx.workspace or ctx.semantics("repo") or ""
    if not repo or not Path(repo).expanduser().is_dir():
        raise RuntimeError("Une mission CODE exige un workspace : chemin d'un dépôt git.")
    branch = f"task/{ctx.task_id}"
    ctx.log(f"Création du worktree git sur {branch}…")
    wt = ctx.engine.worktrees.create(ctx.task_id, repo, branch=branch)
    ctx.set_phase("Worktree prêt", 0.2)
    worktree_path = Path(wt["worktree"])
    plan = ctx.semantics("plan") or []
    for path in (ctx.semantics("artifacts") or []):
        if path:
            ctx.add_artifact(str(path), "code", metadata={"branch": branch})
    for index, step in enumerate(plan, start=1):
        ctx.checkpoint()
        step = dict(step or {})
        shell = step.get("shell")
        if shell:
            proc = subprocess.run(str(shell), shell=True, capture_output=True, text=True,
                                  cwd=str(worktree_path), timeout=int(step.get("timeout") or 300))
            output = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
            if proc.returncode != 0:
                raise RuntimeError(f"Plan CODE, étape {index} : {output[:500]}")
            ctx.log(f"Plan CODE, étape {index} : {output[:200]}")
        else:
            tool_id = str(step.get("tool") or "")
            if not tool_id:
                raise RuntimeError(f"Étape {index} du plan CODE sans outil ni commande.")
            result = ctx.run_tool(tool_id, dict(step.get("arguments") or {}),
                                  agent=ctx.task.get("agent") or "jarvis")
            if not result.ok:
                raise RuntimeError(f"Plan CODE, étape {index} ({tool_id}) : {result.output[:500]}")
    ctx.set_phase("Vérification et commit", 0.6)
    tests: dict[str, Any] = {"ran": False}
    test_command = ctx.semantics("test_command") or ctx.semantics("tests")
    if test_command:
        started = time.time()
        try:
            proc = subprocess.run(str(test_command), shell=True, capture_output=True, text=True,
                                  cwd=str(worktree_path), timeout=300)
            tests = {"ran": True, "ok": proc.returncode == 0, "command": str(test_command)[:300],
                     "duration_ms": int((time.time() - started) * 1000),
                     "output": ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()[:2000]}
        except subprocess.TimeoutExpired:
            tests = {"ran": True, "ok": False, "command": str(test_command)[:300],
                     "duration_ms": int((time.time() - started) * 1000), "output": "Dépassement du délai (300 s)."}
        if not tests["ok"]:
            raise RuntimeError(f"Les tests échouent :\n{str(tests.get('output') or '')[:600]}")
        ctx.log(f"Tests : {'réussis' if tests['ok'] else 'en échec'} ({tests.get('duration_ms')} ms)")
    changes = ctx.engine.worktrees.changes(ctx.task_id)
    commit = ctx.engine.worktrees.commit(ctx.task_id, message=f"{ctx.task.get('title') or 'Mission'} (JARVIS)")
    ctx.set_progress(1.0)
    summary = (f"Code prêt à merger : branche {branch}, commit {commit['commit']}, "
               f"{len(commit['changes'])} fichier(s) modifié(s).")
    ctx.log(summary)
    ready = {"branch": branch, "commit": commit["commit"], "worktree": wt["worktree"],
             "files_changed": commit["changes"], "tests": tests, "summary": summary}
    ctx.engine.worktrees.ready(ctx.task_id)
    return {"result": summary, "ready_to_merge": ready}


HANDLERS: dict[str, Callable[[TaskContext], dict[str, Any]]] = {
    "GENERIC_AGENT": _handle_generic,
    "REPORT": _handle_report,
    "DOCUMENT": _handle_document,
    "CODE": _handle_code,
}


# ---------------------------------------------------------------------------
# BackgroundTaskManager
# ---------------------------------------------------------------------------
class _TaskLive:
    """Runtime d'une mission : synchronisation pause/cancel/input."""

    __slots__ = ("task_id", "cancel", "go", "cond", "input_q", "started")

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.cancel = threading.Event()
        self.go = threading.Event()
        self.cond = threading.Condition()
        self.input_q: list[str] = []
        self.started = True


class BackgroundTaskManager:
    """Orchestrateur multitâches : file d'attente, priorités, dépendances,
    pool de workers, états, persistance et reprise après crash."""

    def __init__(self, core: Any) -> None:
        self._core = core
        self._db = core.db
        self.events = core.events
        self._settings = core.settings
        self.store = TaskStore(self._db)
        self.resources = ResourceScheduler()
        self.locks = LockRegistry()
        self.worktrees = WorktreeManager(core)
        self._lock = threading.RLock()
        self._live: dict[str, _TaskLive] = {}
        self._workers: dict[str, threading.Thread] = {}
        self._sched = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wait_notified: set[str] = set()
        self._refresh_after_restart()

    # -- propriétés --------------------------------------------------------
    @property
    def max_workers(self) -> int:
        value = int(self._settings.get("background", "max_background_tasks", 4) or 4)
        return max(1, value)

    @property
    def enabled(self) -> bool:
        return bool(self._settings.get("background", "enabled", True))

    # -- cycle de vie ------------------------------------------------------
    def start(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            if self._thread is not None:
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._scheduler_loop,
                                            name="jarvis-background-scheduler", daemon=True)
            self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        with self._lock:
            for live in self._live.values():
                live.cancel.set()
                live.go.set()
        self._sched.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=3)
        with self._lock:
            for t in list(self._workers.values()):
                t.join(timeout=2)
            self._live.clear()
            self._workers.clear()

    def _wake_scheduler(self) -> None:
        self._sched.set()

    # -- repars après crash --------------------------------------------------
    def _refresh_after_restart(self) -> None:
        for row in self.store.list(status="", limit=500):
            status = row.get("status")
            # Une mission qui tournait quand JARVIS est tombé ne doit jamais
            # redevenir « running » du silence : on la marque interrompue.
            if status in RUN_IN_PROGRESS:
                self.store.set(row["task_id"], status=FAILED, error="Interrompue par le redémarrage de JARVIS.",
                               completed_at=time.time())
                self.store.add_log(row["task_id"], "Mission interrompue par un redémarrage.", level="warn")
            # WAITING_USER est conservée : l'utilisateur relance via resume_task.

    def _refresh(self) -> None:
        self._refresh_after_restart()

    # -- création / soumission ----------------------------------------------
    def create_task(
        self, title: str, *, description: str = "", task_type: str = "GENERIC_AGENT",
        priority: str = "NORMAL", agent: str = "jarvis", workspace: str = "",
        resources: Any = (), dependencies: Any = (), context: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None, mission_id: str = "",
        auto_submit: bool = True,
    ) -> dict[str, Any]:
        title = str(title or "").strip()
        if not title:
            raise ValueError("Une mission exige un titre.")
        task_type = (task_type or "GENERIC_AGENT").upper()
        if task_type not in TASK_TYPES:
            raise ValueError(f"Type de mission inconnu : {task_type}. "
                             f"Attendus : {', '.join(TASK_TYPES)}.")
        priority = (priority or "NORMAL").upper()
        if priority not in PRIORITIES:
            raise ValueError(f"Priorité inconnue : {priority}. Attendues : {', '.join(PRIORITIES)}.")
        if agent and agent not in agents_module.AGENTS:
            agent = "jarvis"
        if isinstance(resources, str):
            resources = [resources]
        if isinstance(dependencies, str):
            dependencies = [dependencies]
        deps = [str(d) for d in (dependencies or [])]
        for dep in deps:
            if dep == "":
                continue
            if self.store.get(dep) is None:
                raise ValueError(f"La mission dépend de {dep} qui n'existe pas.")
        resources = [str(r) for r in (resources or [])]
        if not resources:
            resources = ["CPU", "NETWORK", "FILESYSTEM"] if task_type == "CODE" else []
        deps = [d for d in deps if d]
        now = time.time()
        task_id = new_id("bt")
        mission_id = mission_id or task_id
        row = {
            "task_id": task_id, "mission_id": mission_id, "title": title,
            "description": str(description or ""), "task_type": task_type,
            "priority": priority, "status": QUEUED, "agent": agent or "jarvis",
            "workspace": str(workspace or ""),
            "resources": resources, "dependencies": deps,
            "context": dict(context or {}), "metadata": dict(metadata or {}),
            "step": "", "note": "", "result": "", "error": "", "progress": 0.0,
            "created_at": now, "started_at": None, "updated_at": now, "completed_at": None,
        }
        self.store.create(row)
        self.events.emit("task.created", {
            "id": task_id, "name": title, "kind": task_type, "agent": agent or "jarvis",
            "conversation_id": "", "mission_id": mission_id})
        self.events.feed(f"Mission « {title} » créée", level="info", kind="mission",
                         detail=f"Type {task_type}, priorité {priority}.", source="background")
        if auto_submit:
            self.submit(task_id)
        return self.get(task_id) or row

    def submit(self, task_id: str) -> dict[str, Any]:
        row = self.store.get(task_id)
        if row is None:
            raise ValueError(f"Mission inconnue : {task_id}")
        if row["status"] not in (QUEUED, BLOCKED, WAITING_RESOURCE):
            raise ValueError(f"Impossible de planifier une mission à l'état {row['status']}.")
        self.store.set(task_id, status=QUEUED, note="")
        self._wake_scheduler()
        return self.get(task_id) or row

    # -- lecture ------------------------------------------------------------
    def get(self, task_id: str) -> dict[str, Any] | None:
        row = self.store.get(task_id)
        return row

    def list(self, status: str = "", limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list(status=status or "", limit=limit)

    def logs(self, task_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.logs(task_id, limit=limit)

    def artifacts(self, task_id: str) -> list[dict[str, Any]]:
        return self.store.artifacts(task_id)

    def stats(self) -> dict[str, Any]:
        counts = self.store.counts()
        live_running = len(self._live)
        return {
            **counts,
            "workers": {"capacity": self.max_workers, "active": live_running,
                        "queued": counts.get(QUEUED, 0) + counts.get(WAITING_RESOURCE, 0)},
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            running = [self.get(tid) for tid in self._live if self.store.status(tid) not in TERMINAL_STATES]
        return {
            "running": [r for r in running if r],
            "resources": self.resources.snapshot(),
            "resource_holders": self.resources.holders(),
            "locks": self.locks.snapshot(),
            "worktrees": self.worktrees.snapshot(),
            "max_workers": self.max_workers,
        }

    # -- états --------------------------------------------------------------
    def _set_status(self, task_id: str, status: str, note: str | None = None) -> bool:
        current = self.store.status(task_id)
        if current in TERMINAL_STATES and current != status:
            return False
        fields: dict[str, Any] = {"status": status}
        if note is not None:
            fields["note"] = str(note)[:500]
        if status in TERMINAL_STATES and current not in TERMINAL_STATES:
            fields["completed_at"] = time.time()
            if fields.get("started_at") is None:
                fields["started_at"] = time.time()
        if status in (QUEUED, RUN_IN_PROGRESS, WAITING_USER, PAUSED) and current in PENDING_STATES:
            if "started_at" not in fields:
                row = self.store.get(task_id) or {}
                if row.get("started_at") is None:
                    fields["started_at"] = time.time()
        self.store.set(task_id, **fields)
        return True

    # -- logs et progression --------------------------------------------------
    def log(self, task_id: str, message: str, level: str = "info", data: Any = None) -> None:
        self.store.add_log(task_id, message, level=level, data=data)
        self.events.emit("task.progress", {"id": task_id, "log": {
            "level": level, "message": str(message)[:2000], "data": data or {}}})

    def set_progress(self, task_id: str, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        self.store.set(task_id, progress=value)
        self.events.emit("task.progress", {"id": task_id, "progress": value})

    def set_phase(self, task_id: str, phase: str, progress: float | None = None) -> None:
        self.store.set(task_id, step=str(phase)[:300])
        if progress is not None:
            self.store.set(task_id, progress=max(0.0, min(1.0, float(progress))))
        self.events.emit("task.progress", {"id": task_id,
                                           "log": {"level": "info", "message": "",
                                                   "data": {"phase": str(phase)[:300]}}})

    # -- attente de ressources -----------------------------------------------
    def _enter_waiting_resource(self, task_id: str, reason: str) -> None:
        if self.store.status(task_id) in TERMINAL_STATES:
            return
        if task_id in self._wait_notified:
            return
        self._wait_notified.add(task_id)
        self._set_status(task_id, WAITING_RESOURCE, note=reason)
        self.events.emit("task.waiting_resource", {"id": task_id, "action": reason})

    def _leave_waiting_resource(self, task_id: str) -> None:
        self._wait_notified.discard(task_id)
        if self.store.status(task_id) not in TERMINAL_STATES:
            self._set_status(task_id, RUNNING, note="")

    # -- contrôle coopératif --------------------------------------------------
    def _checkpoint(self, task_id: str) -> None:
        if self._stop.is_set():
            raise TaskCancelled(task_id)
        live = self._live.get(task_id)
        if live is None:
            if self.store.status(task_id) in TERMINAL_STATES:
                raise TaskCancelled(task_id)
            return
        if live.cancel.is_set() or self.store.status(task_id) == CANCELLED:
            raise TaskCancelled(task_id)
        if self.store.status(task_id) == PAUSED:
            while not live.cancel.is_set():
                if not live.go.wait(0.25) and self.store.status(task_id) != PAUSED:
                    break
                if self.store.status(task_id) != PAUSED:
                    break
                if self._stop.is_set():
                    raise TaskCancelled(task_id)
            if live.cancel.is_set():
                raise TaskCancelled(task_id)

    # -- pause / reprise / annulation ----------------------------------------
    def pause(self, task_id: str, reason: str = "") -> dict[str, Any]:
        with self._lock:
            row = self.store.get(task_id)
            if row is None:
                raise ValueError(f"Mission inconnue : {task_id}")
            status = row["status"]
            if status in TERMINAL_STATES:
                raise ValueError(f"Mission déjà terminée ({status}) : mise en pause impossible.")
            if status == PAUSED:
                return self.get(task_id) or row
            live = self._live.get(task_id)
            if live is not None:
                # Efface l'événement `go` : sinon le worker en pause spinnerait
                # sur un signal résiduel d'une reprise précédente.
                live.go.clear()
            self.store.set(task_id, status=PAUSED, note=str(reason or "En pause.")[:300])
            self.events.emit("task.paused", {"id": task_id, "reason": reason,
                                             "was": status, "running": live is not None})
            self.events.feed(f"Mission « {row['title']} » en pause", level="info", kind="mission",
                             detail=(reason or "Pause demandée."), source="background")
            return self.get(task_id) or row

    def resume_task(self, task_id: str, user_input: str | None = None) -> dict[str, Any]:
        with self._lock:
            row = self.store.get(task_id)
            if row is None:
                raise ValueError(f"Mission inconnue : {task_id}")
            status = row["status"]
            if status in TERMINAL_STATES:
                raise ValueError(f"Mission déjà terminée ({status}) : reprise impossible.")
            live = self._live.get(task_id)
            if status == WAITING_USER:
                if live is None:
                    live = _TaskLive(task_id)
                    self._live[task_id] = live
                if user_input is not None:
                    with live.cond:
                        live.input_q.append(str(user_input))
                self._set_status(task_id, RUNNING, note="")
                live.go.set()
                self.events.emit("task.resumed", {"id": task_id, "ran": True})
                return self.get(task_id) or row
            if status == PAUSED:
                if live is not None:
                    self._set_status(task_id, RUNNING, note="")
                    live.go.set()
                else:
                    self._set_status(task_id, QUEUED, note="")
                    self._wake_scheduler()
                self.events.emit("task.resumed", {"id": task_id, "ran": live is not None})
                return self.get(task_id) or row
            if status in PENDING_STATES or status == BLOCKED:
                self.store.set(task_id, status=QUEUED, note="")
                self._wake_scheduler()
                return self.get(task_id) or row
            raise ValueError(f"Impossible de reprendre une mission à l'état {status}.")

    def cancel(self, task_id: str, reason: str = "") -> dict[str, Any]:
        with self._lock:
            row = self.store.get(task_id)
            if row is None:
                raise ValueError(f"Mission inconnue : {task_id}")
            status = row["status"]
            if status in TERMINAL_STATES:
                raise ValueError(f"Mission déjà terminée ({status}) : annulation impossible.")
            live = self._live.get(task_id)
            if live is not None:
                live.cancel.set()
                live.go.set()
            if status in PENDING_STATES or status in RUN_IN_PROGRESS or status in (WAITING_USER, PAUSED):
                self.store.set(task_id, status=CANCELLED,
                               error=str(reason or "Annulée par l'utilisateur."), completed_at=time.time())
                self.events.emit("task.cancelled", {"id": task_id,
                                                    "error": str(reason or "Annulée par l'utilisateur.")})
                self.events.feed(f"Mission « {row['title']} » annulée", level="warn", kind="mission",
                                 detail=(reason or "Annulation."), source="background")
            self._wake_scheduler()
            return self.get(task_id) or row

    # -- question à l'utilisateur ----------------------------------------------
    def ask_user(self, task_id: str, prompt: str) -> str:
        live = self._live.get(task_id) or _TaskLive(task_id)
        self._live.setdefault(task_id, live)
        self.events.emit("task.waiting_user", {"id": task_id, "action": str(prompt)[:500],
                                               "prompt": str(prompt)[:500]})
        self.log(task_id, f"Question à l'utilisateur : {prompt}", data={"kind": "ask_user"})
        self._set_status(task_id, WAITING_USER, note=str(prompt)[:500])
        self.events.feed(f"Mission « {self.get(task_id).get('title', '')} » en attente",
                         level="info", kind="mission", detail=str(prompt)[:500], source="background")
        while not live.cancel.is_set():
            if self._stop.is_set():
                raise TaskCancelled(task_id)
            self._checkpoint(task_id)
            with live.cond:
                if live.input_q:
                    return live.input_q.pop(0)
            live.go.wait(0.25)
        raise TaskCancelled(task_id)

    def deliver_user_input(self, task_id: str, user_input: str) -> dict[str, Any]:
        """Alias pratique : fournit une réponse à une mission en WAITING_USER."""
        return self.resume_task(task_id, user_input=user_input)

    # -- terminé ---------------------------------------------------------------
    def _finish(self, task_id: str, status: str, *, result: str = "", error: str = "",
                merge: dict[str, Any] | None = None) -> None:
        with self._lock:
            current = self.store.status(task_id)
            if current == status:
                return
            if current in TERMINAL_STATES:
                return
            row = self.store.get(task_id) or {}
            self.store.set(task_id, status=status, result=str(result or "")[:6000],
                           error=str(error or "")[:2000],
                           completed_at=time.time())
            metadata = dict(row.get("metadata") or {})
            if merge is not None:
                metadata["merge"] = merge
                self.store.set(task_id, metadata=metadata)
            title = str(row.get("title") or "Mission")
            if status == READY_TO_MERGE:
                self.events.emit("task.completed", {"id": task_id, "result": result,
                                                    "ready_to_merge": merge})
                self.events.feed(f"Mission « {title} » prête à merger", level="tip", kind="mission",
                                 detail=(merge or {}).get("summary", "")[:400], source="background")
            elif status == COMPLETED:
                self.events.emit("task.completed", {"id": task_id, "result": result})
                self.events.feed(f"Mission « {title} » terminée", level="info", kind="mission",
                                 detail=str(result)[:400], source="background")
            elif status == FAILED:
                self.events.emit("task.failed", {"id": task_id, "error": error})
                self.events.feed(f"Mission « {title} » en échec", level="warn", kind="mission",
                                 detail=str(error)[:400], source="background")
            elif status == CANCELLED:
                self.events.emit("task.cancelled", {"id": task_id, "error": error})
            self._wake_scheduler()

    # -- scheduler ---------------------------------------------------------------
    def _scheduler_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                pass
            self._sched.wait(0.5)
            self._sched.clear()

    def _busy(self) -> int:
        return sum(1 for tid in self._live
                   if self.store.status(tid) not in TERMINAL_STATES)

    def _deps_status(self, row: dict[str, Any]) -> tuple[bool, str]:
        for dep in row.get("dependencies") or []:
            dep_row = self.store.get(dep)
            if dep_row is None:
                return False, f"dépendance {dep} introuvable"
            dstatus = dep_row["status"]
            if dstatus in (COMPLETED, READY_TO_MERGE):
                continue
            if dstatus in (FAILED, CANCELLED):
                return False, f"dépendance {dep} en échec ({dstatus})"
            return False, f"en attente de {dep}"
        return True, ""

    def _tick(self) -> None:
        pending: list[dict[str, Any]] = []
        for row in self.store.list(status="", limit=500):
            status = row.get("status")
            if status in PENDING_STATES or status == BLOCKED:
                pending.append(row)
        if not pending:
            return
        # Ordre : priorité, puis ancienneté.
        pending.sort(key=lambda r: (-_PRIORITY_RANK.get(r.get("priority"), 1), r.get("created_at") or 0))
        capacity = self.max_workers
        for row in pending:
            task_id = row["task_id"]
            if self._busy() >= capacity:
                break
            if self.store.status(task_id) in TERMINAL_STATES:
                continue
            ok, reason = self._deps_status(row)
            if not ok:
                if self.store.status(task_id) != BLOCKED:
                    self._set_status(task_id, BLOCKED, note=reason)
                    self.events.emit("task.blocked", {"id": task_id, "action": reason})
                    self.events.feed(f"Mission « {row.get('title') or ''} » bloquée",
                                     level="warn", kind="mission", detail=reason, source="background")
                continue
            if self.store.status(task_id) == BLOCKED:
                self.store.set(task_id, status=QUEUED, note="")
            resources = list(row.get("resources") or [])
            locks = self.locks.lock_names_for(resources, workspace=row.get("workspace") or "")
            if not self._try_start(task_id, resources, locks):
                continue

    def _try_start(self, task_id: str, resources: list[str], locks: list[str]) -> bool:
        row = self.store.get(task_id)
        if row is None or self.store.status(task_id) in TERMINAL_STATES:
            return False
        if self._busy() >= self.max_workers:
            return False
        if resources and not self.resources.acquire(task_id, resources):
            self._enter_waiting_resource(task_id, "ressource(s) occupée(s) : "
                                                    ", ".join(resources))
            return False
        if locks and not self.locks.acquire(task_id, locks):
            self.resources.release(task_id)
            self._enter_waiting_resource(task_id, "verrou(s) occupé(s) : " + ", ".join(locks))
            return False
        self._start_worker(task_id)
        return True

    def _start_worker(self, task_id: str) -> None:
        now = time.time()
        row = self.store.get(task_id) or {}
        self.store.set(task_id, status=RUNNING, started_at=row.get("started_at") or now)
        self.events.emit("task.started", {"id": task_id, "name": row.get("title"),
                                          "agent": row.get("agent"), "kind": row.get("task_type")})
        # Une mission démarrée n'attend plus de ressources : on lève le flag
        # de déduplication, sinon une future attente ne ré-émettrait pas.
        self._wait_notified.discard(task_id)
        live = _TaskLive(task_id)
        with self._lock:
            self._live[task_id] = live
        thread = threading.Thread(target=self._run_worker, args=(task_id,),
                                  name=f"jarvis-bg-{task_id}", daemon=True)
        with self._lock:
            self._workers[task_id] = thread
        thread.start()

    def _run_worker(self, task_id: str) -> None:
        ctx = TaskContext(self, self._core, task_id)
        try:
            row = self.store.get(task_id) or {}
            if row.get("status") in TERMINAL_STATES:
                return
            handler = HANDLERS.get(row.get("task_type"), _handle_generic)
            outcome = handler(ctx)
            outcome = outcome or {}
            merge = outcome.get("ready_to_merge")
            if merge:
                self._finish(task_id, READY_TO_MERGE, result=str(outcome.get("result") or ""), merge=merge)
            elif outcome.get("error"):
                self._finish(task_id, FAILED, error=str(outcome["error"]))
            else:
                self._finish(task_id, COMPLETED, result=str(outcome.get("result") or "Mission terminée."))
        except TaskCancelled:
            self._finish(task_id, CANCELLED, error="Annulée par l'utilisateur.")
        except Exception as exc:
            self._log_worker_error(task_id, exc)
            self._finish(task_id, FAILED, error=f"{type(exc).__name__}: {exc}")
        finally:
            with self._lock:
                self._live.pop(task_id, None)
                self._workers.pop(task_id, None)
            self.locks.release_all(task_id)
            self.resources.release_all(task_id)
            self._wake_scheduler()

    def _log_worker_error(self, task_id: str, exc: Exception) -> None:
        try:
            import traceback
            self.store.add_log(task_id, f"{type(exc).__name__}: {exc}", level="error",
                               data={"traceback": traceback.format_exc()[:4000]})
        except Exception:
            pass


# Raccourcis de lecture publique (pratiques pour l'intégration serveur).
def list_tasks(engine: BackgroundTaskManager, status: str = "", limit: int = 50) -> list[dict[str, Any]]:
    return engine.list(status=status or "", limit=limit)


def task_detail(engine: BackgroundTaskManager, task_id: str) -> dict[str, Any] | None:
    row = engine.get(task_id)
    if row is None:
        return None
    row = dict(row)
    row["logs"] = engine.logs(task_id, limit=100)
    row["artifacts"] = engine.artifacts(task_id)
    return row