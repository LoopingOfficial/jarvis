"""Demande de rollback auprès du Supervisor (décision réelle côté Supervisor)."""
from __future__ import annotations

import json
import urllib.request
from typing import Any


class SupervisorClient:
    def __init__(self, supervisor_url: str = "http://127.0.0.1:8770",
                 timeout: float = 120.0) -> None:
        self._base = supervisor_url.rstrip("/")
        self._timeout = timeout

    def _post(self, path: str, body: dict[str, Any], timeout: float = 0.0) -> dict[str, Any]:
        url = f"{self._base}{path}"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            return {"ok": False, "error": str(exc), "reachable": False}

    def ping(self) -> bool:
        try:
            import urllib.request as u
            with u.urlopen(f"{self._base}/api/supervisor/ping", timeout=3) as r:
                return r.status == 200
        except Exception:
            return False

    def status(self) -> dict[str, Any]:
        try:
            import urllib.request as u
            with u.urlopen(f"{self._base}/api/supervisor/status", timeout=5) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as exc:
            return {"ok": False, "error": str(exc), "reachable": False}

    def promote(self, upgrade_id: str, branch: str, workspace: str, prompt: str) -> dict[str, Any]:
        return self._post("/api/supervisor/promote", {
            "upgrade_id": upgrade_id, "branch": branch,
            "workspace": workspace, "prompt": prompt[:2000],
        })

    def rollback(self, upgrade_id: str, reason: str = "") -> dict[str, Any]:
        return self._post("/api/supervisor/rollback", {
            "upgrade_id": upgrade_id, "reason": reason[:500],
        })


class RollbackManager:
    def __init__(self, supervisor_url: str = "http://127.0.0.1:8770") -> None:
        self._client = SupervisorClient(supervisor_url)

    def can_rollback(self, upgrade_id: str) -> bool:
        if not self._client.ping():
            return False
        st = self._client.status()
        return bool(st.get("ok")) and upgrade_id == st.get("last_installed_upgrade_id")

    def rollback(self, upgrade_id: str, reason: str = "") -> dict[str, Any]:
        return self._client.rollback(upgrade_id, reason)