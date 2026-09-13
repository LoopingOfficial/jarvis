"""Persistance de l'historique des upgrades en base SQLite."""
from __future__ import annotations

import json
import time
from typing import Any

from ..db import Database, dumps, loads


class UpgradeHistory:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, upgrade_id: str, prompt: str, mode: str, *, branch: str = "",
               workspace_path: str = "", version_before: str = "") -> None:
        now = time.time()
        self._db.execute(
            "INSERT INTO self_upgrades(id, prompt, mode, status, branch, workspace_path, "
            "version_before, created_at, started_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (upgrade_id, prompt, mode, "queued", branch, workspace_path,
             version_before, now, now),
        )

    def set_status(self, upgrade_id: str, status: str, error: str = "") -> None:
        now = time.time()
        self._db.execute(
            "UPDATE self_upgrades SET status=?, error=?, updated_at=? WHERE id=?",
            (status, error[:4000], now, upgrade_id),
        )

    def update(self, upgrade_id: str, **fields: Any) -> None:
        allowed = {
            "status", "branch", "workspace_path", "version_after", "plan", "files_changed",
            "git_diff", "tests_result", "health_status", "install_status", "rollback_status",
            "error", "candidate_port", "completed_at", "promoted_at", "rolled_back_at", "meta",
        }
        cols = []
        vals: list[Any] = []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if isinstance(v, (dict, list)):
                v = dumps(v)
            cols.append(f"{k}=?")
            vals.append(v)
        if not cols:
            return
        vals.append(upgrade_id)
        self._db.execute(f"UPDATE self_upgrades SET {', '.join(cols)} WHERE id=?", vals)

    def get(self, upgrade_id: str) -> dict[str, Any] | None:
        row = self._db.one("SELECT * FROM self_upgrades WHERE id=?", (upgrade_id,))
        if row is None:
            return None
        return self._to_dict(row)

    def active(self) -> dict[str, Any] | None:
        row = self._db.one("SELECT * FROM self_upgrades WHERE status IN "
                           "('queued','planning','building','testing','candidate','promoting') "
                           "ORDER BY created_at DESC LIMIT 1")
        return self._to_dict(row) if row else None

    def list(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = self._db.query(
            "SELECT * FROM self_upgrades ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self._to_dict(r) for r in rows]

    def _to_dict(self, row) -> dict[str, Any]:
        d = {k: row[k] for k in row.keys()}
        for f in ("plan", "files_changed", "tests_result", "meta"):
            if f in d:
                d[f] = loads(d[f], {} if f == "plan" else ([] if f == "files_changed" else {}))
        return d

    def record_files(self, upgrade_id: str, files: list[str]) -> None:
        now = time.time()
        self._db.executemany(
            "INSERT INTO self_upgrade_files(upgrade_id, path, created_at) VALUES(?,?,?)",
            [(upgrade_id, f, now) for f in files],
        )

    def files(self, upgrade_id: str) -> list[str]:
        rows = self._db.query(
            "SELECT path FROM self_upgrade_files WHERE upgrade_id=? ORDER BY id", (upgrade_id,))
        return [r["path"] for r in rows]