"""Rapport d'upgrade structuré (upgrade_id, branch, versions, fichiers, tests, santé…)."""
from __future__ import annotations

from typing import Any

CHECK_KEYS = (
    ("workspace", "workspace_path", "ok"),
    ("build", "status", "build_done"),
    ("tests", "tests_result", "summary"),
    ("candidate", "health_status", "ok"),
    ("health", "health_status", "ok"),
)


class UpgradeReport:
    @staticmethod
    def build(record: dict[str, Any] | None) -> dict[str, Any]:
        if record is None:
            return {"ok": False, "error": "upgrade inconnue"}
        tests = record.get("tests_result") or {}
        health = record.get("health_status") or {}
        if isinstance(health, str):
            health = {"ok": health.startswith("ok") or health == "ok"}
        return {
            "upgrade_id": record.get("id"),
            "prompt": record.get("prompt", ""),
            "mode": record.get("mode", ""),
            "status": record.get("status", ""),
            "branch": record.get("branch", ""),
            "workspace": record.get("workspace_path", ""),
            "version_before": record.get("version_before", ""),
            "version_after": record.get("version_after", record.get("version_before", "")),
            "plan": record.get("plan", {}),
            "files_changed": record.get("files_changed", []),
            "git_diff": record.get("git_diff", ""),
            "tests": tests,
            "health_status": health,
            "install_status": record.get("install_status", ""),
            "rollback_status": record.get("rollback_status", ""),
            "error": record.get("error", ""),
            "candidate_port": record.get("candidate_port", 0),
            "created_at": record.get("created_at"),
            "completed_at": record.get("completed_at"),
            "promoted_at": record.get("promoted_at"),
            "rolled_back_at": record.get("rolled_back_at"),
        }

    @staticmethod
    def summary(report: dict[str, Any]) -> str:
        status = report.get("status", "?")
        tests = report.get("tests", {})
        health = report.get("health_status", {})
        lines = [
            f"Upgrade {report.get('upgrade_id')} : {status}",
            f"  Mode        : {report.get('mode')}",
            f"  Branche     : {report.get('branch')}",
            f"  Fichiers    : {len(report.get('files_changed', []))}",
            f"  Tests       : {tests.get('summary', '—')} "
            f"({tests.get('tests_run', 0)} lancés, {tests.get('tests_failed', 0)} échecs)",
            f"  Healthcheck : {'OK' if health.get('ok') else health.get('error', '—')}",
            f"  Install     : {report.get('install_status') or 'en attente du Supervisor'}",
            f"  Rollback    : {report.get('rollback_status') or '—'}",
        ]
        if report.get("error"):
            lines.append(f"  Erreur      : {report.get('error')[:300]}")
        return "\n".join(lines)