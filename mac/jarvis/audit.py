"""Journal d'audit — toute action est tracée, jamais les secrets."""
from __future__ import annotations

import time
from typing import Any

from .db import Database, dumps


class AuditLog:
    def __init__(self, db: Database, vault=None, settings=None) -> None:
        self._db = db
        self._vault = vault
        self._settings = settings

    def _clean(self, text: str) -> str:
        if not text:
            return ""
        text = str(text)
        if self._vault is not None and (self._settings is None or self._settings.get("security", "mask_secrets_in_logs", True)):
            try:
                text = self._vault.scrub(text)
            except Exception:
                pass
        return text[:4000]

    def record(
        self,
        *,
        action: str,
        status: str = "ok",
        user: str = "jerome",
        agent: str = "jarvis",
        tool: str = "",
        connector_id: str = "",
        duration_ms: int = 0,
        task_id: str = "",
        detail: Any = "",
    ) -> int:
        if not isinstance(detail, str):
            detail = dumps(detail)
        cur = self._db.execute(
            "INSERT INTO audit_log(ts, user, agent, tool, connector_id, action, status, duration_ms, task_id, detail) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (time.time(), user, agent, tool, connector_id, self._clean(action)[:500],
             status, int(duration_ms), task_id, self._clean(detail)),
        )
        return int(cur.lastrowid or 0)

    def entries(self, limit: int = 100, offset: int = 0, search: str = "") -> list[dict[str, Any]]:
        if search:
            rows = self._db.query(
                "SELECT * FROM audit_log WHERE action LIKE ? OR tool LIKE ? OR detail LIKE ? "
                "ORDER BY ts DESC LIMIT ? OFFSET ?",
                (f"%{search}%", f"%{search}%", f"%{search}%", limit, offset),
            )
        else:
            rows = self._db.query("SELECT * FROM audit_log ORDER BY ts DESC LIMIT ? OFFSET ?", (limit, offset))
        return [{k: r[k] for k in r.keys()} for r in rows]

    def count(self) -> int:
        return int(self._db.scalar("SELECT COUNT(*) FROM audit_log") or 0)

    def prune(self, retention_days: int = 90) -> int:
        cutoff = time.time() - retention_days * 86400
        cur = self._db.execute("DELETE FROM audit_log WHERE ts < ?", (cutoff,))
        return cur.rowcount or 0
