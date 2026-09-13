"""SelfUpgradeService — orchestration PLAN / BUILD / AUTO d'une amélioration autonome."""
from __future__ import annotations

import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .. import __version__
from ..build import JARVIS_BUILD_ID
from ..config import ROOT, SettingsStore
from ..db import Database
from ..events import EventBus
from .agent import LocalCodeAgent
from .candidate import CandidateRunner, http_get
from .git_mgr import GitManager
from .history import UpgradeHistory
from .planner import UpgradePlanner
from .report import UpgradeReport
from .supervisor_client import SupervisorClient
from .testrunner import TestRunner
from .workspace import RollbackStorage, UpgradeWorkspaceManager

DEFAULT_SUPERVISOR_URL = "http://127.0.0.1:8770"
DEFAULT_CANDIDATE_PORT = 8791
SU_URL = "http://127.0.0.1:8770"


def make_upgrade_id(prompt: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:30] or "upgrade"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{slug}"


def detect_python() -> str:
    venv = Path(sys.executable) if "virtualenv" in sys.prefix or ".venv" in str(Path(sys.executable)) else None
    win = ROOT / ".venv" / "Scripts" / "python.exe"
    if win.exists():
        return str(win)
    unix = ROOT / ".venv" / "bin" / "python"
    if unix.exists():
        return str(unix)
    return sys.executable


class SelfUpgradeService:
    def __init__(self, core) -> None:
        self._core = core
        self._db: Database = core.db
        self._settings: SettingsStore = core.settings
        self._events: EventBus = core.events
        self.history = UpgradeHistory(self._db)
        self._active: dict[str, Any] = {}
        self._cancel = threading.Event()
        self._lock = threading.RLock()
        self._client = SupervisorClient(DEFAULT_SUPERVISOR_URL)

    # -- configuration ----------------------------------------------------
    def config(self) -> dict[str, Any]:
        base = self._settings.section("self_upgrade")
        return {
            "provider": base.get("provider", "ollama"),
            "base_url": base.get("base_url", "http://127.0.0.1:11434"),
            "orchestrator_model": base.get("orchestrator_model", "jarvis-astra"),
            "coder_model": base.get("coder_model", ""),
            "candidate_port": int(base.get("candidate_port", DEFAULT_CANDIDATE_PORT)),
            "supervisor_url": base.get("supervisor_url", DEFAULT_SUPERVISOR_URL),
            "main_port": int(base.get("main_port", int(os.getenv("JARVIS_PORT", "8765")))),
            "python": base.get("python", "") or detect_python(),
            "max_attempts": int(base.get("max_attempts", "2")),
        }

    def update_config(self, values: dict[str, Any]) -> dict[str, Any]:
        return self._settings.update("self_upgrade", values)

    def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        self._events.emit(event_type, data)

    # -- état -------------------------------------------------------------
    def active_upgrade(self) -> dict[str, Any] | None:
        with self._lock:
            if self._active:
                return dict(self._active)
            row = self.history.active()
            return self._with_report(row) if row else None

    def _with_report(self, row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["report"] = UpgradeReport.build(row)
        return row

    # -- lancement --------------------------------------------------------
    def run(self, prompt: str, mode: str = "auto", conversation_id: str = "") -> dict[str, Any]:
        mode = (mode or "auto").lower()
        if mode not in {"plan", "build", "auto"}:
            return {"ok": False, "error": f"Mode inconnu: {mode}"}
        with self._lock:
            if self._active:
                return {"ok": False, "error": "Un upgrade est déjà en cours.",
                        "active": self._active.get("id")}
            upgrade_id = make_upgrade_id(prompt)
            cfg = self.config()
            self.history.create(upgrade_id, prompt, mode, version_before=JARVIS_BUILD_ID)
            self._active = {"id": upgrade_id, "mode": mode, "prompt": prompt,
                            "conversation_id": conversation_id, "status": "queued",
                            "started_at": time.time(), "config": cfg}
        self._emit("upgrade.started", {
            "upgrade_id": upgrade_id, "mode": mode, "prompt": prompt[:400],
            "orchestrator_model": cfg["orchestrator_model"], "coder_model": cfg["coder_model"],
        })
        threading.Thread(target=self._thread, args=(upgrade_id, prompt, mode, conversation_id),
                         daemon=True, name=f"self-upgrade-{upgrade_id}").start()
        return {"ok": True, "upgrade_id": upgrade_id, "mode": mode}

    def cancel(self) -> dict[str, Any]:
        self._cancel.set()
        self.history.set_status(self._active.get("id", ""), "cancelling")
        return {"ok": True}

    def install(self, upgrade_id: str) -> dict[str, Any]:
        """Demande l'installation d'une candidate validée au Supervisor (lui seul exécute)."""
        row = self.history.get(upgrade_id)
        if not row:
            return {"ok": False, "error": "upgrade inconnue"}
        if row["status"] not in {"build_done", "plan_ready", "promotion_blocked", "installed"}:
            return {"ok": False, "error": f"statut non installable: {row['status']}"}
        client = SupervisorClient(self.config().get("supervisor_url", DEFAULT_SUPERVISOR_URL))
        if not client.ping():
            return {"ok": False, "error": "Supervisor injoignable. Démarre JarvisSupervisor "
                                          "(supervisor/jarvis_supervisor.py)."}
        prom = client.promote(upgrade_id, row["branch"], row["workspace_path"], row["prompt"])
        if not prom.get("ok"):
            return {"ok": False, "error": prom.get("error", "promotion refusée")}
        self.history.update(upgrade_id, status="promoting", install_status="requested")
        return {"ok": True, "upgrade_id": upgrade_id, "accepted": True}

    def rollback_u(self, upgrade_id: str) -> dict[str, Any]:
        row = self.history.get(upgrade_id)
        if not row:
            return {"ok": False, "error": "upgrade inconnue"}
        client = SupervisorClient(self.config().get("supervisor_url", DEFAULT_SUPERVISOR_URL))
        if not client.ping():
            return {"ok": False, "error": "Supervisor injoignable."}
        res = client.rollback(upgrade_id, "rollback manuel demandé")
        if res.get("ok"):
            self.history.update(upgrade_id, rollback_status="rolling_back")
        return res

    # -- pipeline ---------------------------------------------------------
    def _thread(self, upgrade_id: str, prompt: str, mode: str, conversation_id: str) -> None:
        cfg = self.config()
        try:
            self._pipeline(upgrade_id, prompt, mode, cfg, conversation_id)
        except Exception as exc:
            self.history.set_status(upgrade_id, "failed", str(exc))
            self._emit("upgrade.failed", {"upgrade_id": upgrade_id, "error": str(exc)[:400]})
        finally:
            with self._lock:
                self._active = {}

    def _log(self, upgrade_id: str, message: str, level: str = "info") -> None:
        self._emit("upgrade.log", {"upgrade_id": upgrade_id, "message": message, "level": level})
        print(f"[SELF-UPGRADE][{upgrade_id}] {message}", flush=True)

    def _pipeline(self, upgrade_id: str, prompt: str, mode: str, cfg: dict[str, Any],
                  conversation_id: str) -> None:
        self._cancel.clear()
        root = ROOT
        self._log(upgrade_id, f"démarrage du mode {mode.upper()}")
        self.history.set_status(upgrade_id, "planning")
        if self._cancel.is_set():
            self.history.set_status(upgrade_id, "cancelled"); return

        planner = UpgradePlanner(root, cfg["base_url"], cfg["orchestrator_model"])
        plan_result = planner.plan(prompt, mode.upper())
        plan = plan_result.get("plan", {})
        self.history.update(upgrade_id, plan=plan)
        self._emit("upgrade.plan_ready", {"upgrade_id": upgrade_id, "plan": plan})

        if mode == "plan":
            self.history.update(upgrade_id, status="plan_ready",
                                completed_at=time.time(),
                                version_after=self.history.get(upgrade_id)["version_before"])
            self._emit("upgrade.completed", {"upgrade_id": upgrade_id, "status": "plan_ready"})
            self._finish(upgrade_id)
            return

        # -- workspace Git isolé
        self.history.set_status(upgrade_id, "building")
        ws_mgr = UpgradeWorkspaceManager(root)
        created = ws_mgr.create(upgrade_id)
        if not created.get("ok"):
            raise RuntimeError(created.get("error", "création workspace impossible"))
        workspace = Path(created["workspace"])
        branch = created["branch"]
        self.history.update(upgrade_id, branch=branch, workspace_path=str(workspace))
        self._emit("upgrade.workspace_ready", {"upgrade_id": upgrade_id, "workspace": str(workspace),
                                               "branch": branch})
        self._log(upgrade_id, f"workspace {workspace} (branch {branch})")

        # -- LocalCodeAgent : build + tests + corrections auto
        agent = LocalCodeAgent(workspace, root, python=cfg["python"],
                               ollama_url=cfg["base_url"], coder_model=cfg["coder_model"])
        objective = plan.get("objective") or prompt
        target_files_hint = ", ".join((plan.get("target_files") or [])[:8])
        agent_prompt = (f"Objectif : {objective}\n"
                        f"Fichiers cibles suggérés : {target_files_hint or 'à déterminer'}\n"
                        f"Prompt opérateur original : {prompt}")
        self._log(upgrade_id, "codegen par LocalCodeAgent…")
        agent_result = agent.run(agent_prompt, test_target=self._test_target(plan))
        self.history.update(upgrade_id, git_diff=agent_result.get("git_diff", ""),
                            files_changed=agent_result.get("files_changed", []))

        # -- tests (avec passes de réparation)
        tests_result = self._test_with_repair(upgrade_id, workspace, cfg, plan, agent, agent_prompt)
        self.history.update(upgrade_id, tests_result=tests_result)
        self._emit("upgrade.testing", {"upgrade_id": upgrade_id, "tests": tests_result})
        if not tests_result.get("ok"):
            self.history.update(upgrade_id, status="build_failed",
                                completed_at=time.time(), error="tests_failed")
            self._finish(upgrade_id)
            return

        # -- commit final du workspace
        git = GitManager(workspace)
        git.commit(f"[{upgrade_id}] {prompt[:200]}")
        version_after = git.head_sha()
        files_changed = agent_result.get("files_changed", []) or git.diff_files("HEAD^")
        self.history.update(upgrade_id, version_after=version_after, files_changed=files_changed)
        self._emit("upgrade.building", {"upgrade_id": upgrade_id,
                                        "files_changed": self.history.get(upgrade_id).get("files_changed", [])})

        if mode == "build":
            self.history.update(upgrade_id, status="build_done", completed_at=time.time())
            self._emit("upgrade.completed", {"upgrade_id": upgrade_id, "status": "build_done"})
            self._finish(upgrade_id)
            return

        # -- candidate + health + vérification fonctionnelle
        self.history.set_status(upgrade_id, "candidate")
        candidate = self._run_candidate(upgrade_id, workspace, cfg, plan)
        self._finish_candidate(upgrade_id, candidate)
        if not candidate.get("ok"):
            raise RuntimeError(candidate.get("error", "candidate en échec"))

        # -- promotion (Supervisor décide/exécute)
        self.history.set_status(upgrade_id, "promoting")
        self._emit("upgrade.promoting", {"upgrade_id": upgrade_id})
        self._log(upgrade_id, "demande de promotion au Supervisor")
        prom = self._client.promote(upgrade_id, branch, str(workspace), prompt)
        if not prom.get("ok"):
            self.history.update(upgrade_id, status="promotion_blocked",
                                install_status=f"blocked: {prom.get('error', '?')}", completed_at=time.time())
            self._finish(upgrade_id)
            return
        self.history.update(upgrade_id, install_status="requested")
        # Le Supervisor exécute l'installation de façon asynchrone ; il peut redémarrer ce process.
        st = self._poll_install(upgrade_id)
        self._finish(upgrade_id)

    def _test_target(self, plan: dict[str, Any]) -> str:
        runs = plan.get("tests_to_run") or []
        for r in runs:
            if "test" in r and "unittest" in r:
                return r.replace("python -m unittest ", "").strip()
        return ""

    def _test_with_repair(self, upgrade_id: str, workspace: Path, cfg: dict[str, Any],
                          plan: dict[str, Any], agent: LocalCodeAgent, agent_prompt: str) -> dict[str, Any]:
        runner = TestRunner(workspace, python=cfg["python"])
        target = self._test_target(plan)
        self._log(upgrade_id, f"tests (target={target or 'discover'})")
        result = runner.run(target)
        attempts = 0
        while not result.get("ok") and attempts < max(1, int(cfg.get("max_attempts", 2))):
            attempts += 1
            self._log(upgrade_id, f"tests KO — réparation {attempts}…")
            repair = agent.run(f"Les tests échouent.\nDernière sortie tests :\n"
                               f"{result.get('output', '')[-6000:]}\n\nCorrige et relance jusqu'à succès.",
                               test_target=target)
            result = runner.run(target)
        return result

    def _run_candidate(self, upgrade_id: str, workspace: Path, cfg: dict[str, Any],
                       plan: dict[str, Any]) -> dict[str, Any]:
        port = int(cfg.get("candidate_port", DEFAULT_CANDIDATE_PORT))
        verification = (plan.get("verification") or "").strip()
        candidate = CandidateRunner(workspace, python=cfg["python"], port=port,
                                    health_endpoint="/api/health")
        started = candidate.start()
        self.history.update(upgrade_id, candidate_port=port)
        if not started.get("ok"):
            return {"ok": False, "error": f"candidate injoignable: {started.get('error', '?')}",
                    "health_status": {"ok": False, "error": started.get("error", "?")}}
        health = started.get("health", {})
        verification_result = None
        if verification and verification.startswith("/"):
            verification_result = candidate.check(verification)
            health_ok = health.get("ok") and verification_result.get("ok")
        else:
            health_ok = health.get("ok")
        return {"ok": health_ok, "port": port,
                "health_status": {"ok": health_ok, "health": health,
                                  "verification": verification_result,
                                  "error": "" if health_ok else "verification candidature en échec"}}

    def _finish_candidate(self, upgrade_id: str, candidate: dict[str, Any]) -> None:
        self.history.update(upgrade_id, health_status=candidate.get("health_status", {}))
        self._emit("upgrade.candidate", {"upgrade_id": upgrade_id, "candidate": candidate})
        port = candidate.get("port")
        if port:
            CandidateRunner(ROOT, port=port).stop()

    def _poll_install(self, upgrade_id: str) -> dict[str, Any]:
        client = SupervisorClient(self.config().get("supervisor_url", DEFAULT_SUPERVISOR_URL))
        deadline = time.time() + 240
        while time.time() < deadline:
            try:
                st = client.status()
            except Exception:
                st = {"ok": False}
            if isinstance(st, dict) and st.get("last_installed_upgrade_id") == upgrade_id:
                self.history.update(upgrade_id, install_status="installed",
                                    promoted_at=time.time())
                self._emit("upgrade.installed", {"upgrade_id": upgrade_id})
                return st
            if isinstance(st, dict) and st.get("last_failed_upgrade_id") == upgrade_id:
                self.history.update(upgrade_id, install_status="install_failed",
                                    rollback_status=st.get("last_rollback_status", "rolled_back"),
                                    promoted_at=time.time())
                self._emit("upgrade.failed", {"upgrade_id": upgrade_id,
                                              "error": "promotion en échec, rollback effectué"})
                return st
            if self._cancel.is_set():
                break
            time.sleep(4)
        self.history.update(upgrade_id, install_status="timeout")
        return {"ok": False, "error": "timeout promotion"}

    def _finish(self, upgrade_id: str) -> None:
        row = self.history.get(upgrade_id)
        self._emit("upgrade.progress", {"upgrade_id": upgrade_id, "status": row["status"],
                                        "report": UpgradeReport.build(row)})