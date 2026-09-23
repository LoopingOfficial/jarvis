"""ConnectorHealthManager : état RÉEL des connecteurs, vérifié périodiquement.

CONNECTED signifie qu'un vrai test a réussi — jamais « une clé existe ».
Un connecteur en panne n'affecte que lui : les autres restent utilisables.
Vérification toutes les 90 s, en quiet mode (pas d'audit ni de flux à chaque tour).
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any

STATES = ("CONNECTED", "DEGRADED", "WAITING_USER", "DISCONNECTED", "ERROR", "NOT_CONFIGURED")
INTERVAL_S = 90
DEGRADED_MS = 3000

# Connecteurs que VELKO sait utiliser même s'ils ne sont pas encore créés.
EXPECTED = [("n8n", "n8n", "Automatisation"), ("ssh", "Serveur SSH", "Infrastructure"),
            ("mysql", "Base de données", "Base de données"), ("discord", "Discord Bot", "Communication"),
            ("google", "Google", "Google"), ("email", "Email", "Email")]


def state_for(ok: bool, detail: str, latency_ms: int | None) -> str:
    if ok:
        return "DEGRADED" if (latency_ms or 0) > DEGRADED_MS else "CONNECTED"
    d = str(detail or "")
    if re.search(r"HTTP 40[13]|unauthori[sz]ed|forbidden|authentif|cl[ée] api|token|mot de passe", d, re.I):
        return "WAITING_USER"
    if re.search(r"manquant|absente?|non configur", d, re.I):
        return "NOT_CONFIGURED"
    if re.search(r"timed out|timeout|refused|unreachable|injoignable|getaddrinfo|nodename|urlopen|ferm[ée]", d, re.I):
        return "DISCONNECTED"
    return "ERROR"


class ConnectorHealthManager:
    def __init__(self, core) -> None:
        self._core = core
        self._lock = threading.RLock()
        self._results: dict[str, dict[str, Any]] = {}
        self._thread: threading.Thread | None = None

    def start(self, stop_event: threading.Event | None = None) -> None:
        if self._thread:
            return
        stop = stop_event or threading.Event()

        def loop():
            if stop.wait(20):                 # laisse le boot respirer
                return
            while not stop.is_set():
                try:
                    self.check_all()
                except Exception as exc:     # pragma: no cover - défense runtime
                    print(f"[health] {exc!r}", flush=True)
                if stop.wait(INTERVAL_S):
                    return

        self._thread = threading.Thread(target=loop, daemon=True, name="velko-connector-health")
        self._thread.start()

    def check(self, connector_id: str) -> dict[str, Any]:
        core = self._core
        c = core.connectors.raw(connector_id)
        if not c:
            return {"id": connector_id, "state": "NOT_CONFIGURED", "detail": "Connecteur introuvable."}
        if not c.get("enabled"):
            return self._store(c, False, "Connecteur désactivé.", None, forced="DISCONNECTED")
        started = time.time()
        if c["type"] == "n8n":
            s = core.n8n.connector.status()
            return self._store(c, s["state"] in ("CONNECTED", "DEGRADED"), s.get("detail", ""),
                               s.get("latency_ms"), forced=s["state"], extra=s)
        ok, detail = core.connectors.test(connector_id, quiet=True)
        return self._store(c, ok, detail, int((time.time() - started) * 1000))

    def _store(self, c: dict[str, Any], ok: bool, detail: str, latency: int | None,
               forced: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
        state = forced or state_for(ok, detail, latency)
        previous = self._results.get(c["id"], {}).get("state")
        row = {"id": c["id"], "type": c["type"], "name": c["name"], "state": state,
               "detail": self._core.vault.scrub(str(detail or ""))[:300], "latency_ms": latency if ok else None,
               "last_check": time.time()}
        if extra:
            row.update({k: extra[k] for k in ("instance", "workflows", "active", "inactive", "version")
                        if k in extra})
        with self._lock:
            self._results[c["id"]] = row
        if previous and previous != state:     # seul un CHANGEMENT d'état est signalé
            self._core.events.emit("connector.health", {"id": c["id"], "state": state, "previous": previous})
            if state not in ("CONNECTED", "DEGRADED"):
                self._core.events.feed(f"{c['name']} : {state}", level="warn", kind="connector",
                                       detail=row["detail"], source=c["type"])
        return row

    def check_all(self) -> list[dict[str, Any]]:
        out = []
        for c in self._core.connectors.list(include_disabled=True):
            try:
                out.append(self.check(c["id"]))
            except Exception as exc:          # un connecteur ne bloque jamais les autres
                out.append({"id": c["id"], "state": "ERROR", "detail": str(exc)[:200]})
        return out

    def snapshot(self) -> list[dict[str, Any]]:
        """Liste pour le tableau de bord : connecteurs réels + attendus non configurés."""
        rows = []
        present = set()
        for c in self._core.connectors.list(include_disabled=True):
            present.add(c["type"])
            r = self._results.get(c["id"])
            if not r:   # pas encore vérifié : on n'affirme rien
                r = {"id": c["id"], "type": c["type"], "name": c["name"], "state": "DISCONNECTED"
                     if not c.get("enabled") else ("CONNECTED" if c.get("status") == "connected" else
                                                   state_for(False, c.get("status_detail", ""), None)),
                     "detail": c.get("status_detail", ""), "latency_ms": None,
                     "last_check": c.get("last_test_at"), "stale": True}
            rows.append({**r, "label": c.get("label"), "category": c.get("category"),
                         "secret_fields": c.get("secret_fields"), "enabled": c.get("enabled")})
        for ctype, label, cat in EXPECTED:
            if ctype not in present and not (ctype == "ssh" and "sftp" in present):
                rows.append({"id": "", "type": ctype, "name": label, "label": label, "category": cat,
                             "state": "NOT_CONFIGURED", "detail": "Aucun connecteur configuré.",
                             "latency_ms": None, "last_check": None})
        return rows
