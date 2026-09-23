"""N8nConnector : accès RÉEL à l'API publique n8n (v1) + index des workflows.

Aucune donnée n'est simulée : chaque liste provient d'un appel HTTP authentifié.
La clé API vit dans le SecretVault (champ ``api_key`` du connecteur n8n) et
n'apparaît jamais dans une réponse, un log ou un événement.
"""
from __future__ import annotations

import re
import threading
import time
import unicodedata
import urllib.parse
from typing import Any

from .connectors import http_json

# Synonymes pour la recherche « sémantique » légère (noms, tags, nodes, description).
_SYNONYMS = {
    "email": ["email", "mail", "gmail", "smtp", "imap", "courriel", "outlook", "sendgrid", "mailjet"],
    "calendrier": ["calendar", "calendrier", "agenda", "rendez", "event"],
    "discord": ["discord", "bot", "salon", "moderation"],
    "base": ["postgres", "mysql", "database", "sql", "table", "supabase", "base"],
    "memoire": ["memory", "memoire", "qdrant", "embedding", "rag", "knowledge"],
    "site": ["website", "site", "http", "seo", "blog", "wordpress"],
    "analytics": ["analytics", "statistique", "rapport", "report", "kpi", "analyse"],
    "fichier": ["file", "fichier", "drive", "document", "pdf"],
    "planification": ["schedule", "cron", "planifi", "hebdo", "weekly", "daily"],
}
_STOP = set("le la les un une des de du d l mon ma mes qui que quel quelle quels quelles gere gerent "
            "j ai workflow workflows n8n est a il y pour avec sur en et ou ce cet cette the".split())


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return text.casefold()


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", _norm(text)) if t not in _STOP and len(t) > 1]


class N8nError(Exception):
    def __init__(self, message: str, category: str = "EXTERNAL_SERVICE_ERROR", status: int = 0):
        super().__init__(message)
        self.category, self.status = category, status


def classify_http_failure(payload: Any) -> tuple[str, int]:
    text = str(payload or "")
    m = re.match(r"HTTP (\d{3})", text)
    code = int(m.group(1)) if m else 0
    if code in (401, 403):
        return "AUTH_REQUIRED", code
    if code == 404:
        return "INVALID_CONFIGURATION", code
    if code >= 500:
        return "EXTERNAL_SERVICE_ERROR", code
    if re.search(r"timed out|timeout|refused|unreachable|Name or service|nodename|urlopen error|"
                 r"getaddrinfo|No route|SSL", text, re.I):
        return "NETWORK_ERROR", code
    return "EXTERNAL_SERVICE_ERROR", code


class N8nConnector:
    """Client n8n au-dessus du ConnectorManager et du SecretVault existants."""

    def __init__(self, core) -> None:
        self._core = core
        self._lock = threading.RLock()
        self._ensure_schema()

    # -- configuration -----------------------------------------------------
    def _ensure_schema(self) -> None:
        self._core.db.execute(
            "CREATE TABLE IF NOT EXISTS n8n_workflow_index ("
            " workflow_id TEXT PRIMARY KEY, connector_id TEXT, name TEXT, active INTEGER,"
            " nodes TEXT, tags TEXT, description TEXT, triggers TEXT, updated_at TEXT,"
            " last_execution TEXT, last_status TEXT, synced_at REAL)")

    def connector(self) -> dict[str, Any] | None:
        items = self._core.connectors.by_type("n8n")
        return self._core.connectors.raw(items[0]["id"]) if items else None

    def _cfg(self) -> tuple[dict[str, Any], str, str]:
        c = self.connector()
        if not c:
            raise N8nError("Aucun connecteur n8n n'est configuré.", "CONNECTOR_MISSING")
        cfg = c.get("config") or {}
        url = str(cfg.get("url") or "").rstrip("/")
        if not url:
            raise N8nError("URL n8n manquante.", "INVALID_CONFIGURATION")
        key = self._core.vault.get(c["id"], "api_key", "")
        if not key:
            raise N8nError("Clé API n8n absente du coffre.", "AUTH_REQUIRED")
        return c, url, key

    def _api(self, path: str, *, method: str = "GET", body: Any = None) -> Any:
        c, url, key = self._cfg()
        cfg = c.get("config") or {}
        ok, payload = http_json(f"{url}/api/v1{path}", method=method, body=body,
                                headers={"X-N8N-API-KEY": key},
                                verify_ssl=bool(cfg.get("verify_ssl", True)),
                                timeout=float(cfg.get("timeout") or 20))
        if not ok:
            category, code = classify_http_failure(payload)
            detail = self._core.vault.scrub(str(payload))[:300]
            raise N8nError(detail, category, code)
        return payload

    def configure(self, url: str, api_key: str = "", timeout: int = 20, verify_ssl: bool = True,
                  webhook_base: str = "") -> dict[str, Any]:
        url = str(url or "").strip().rstrip("/")
        if not re.match(r"^https?://[^\s/]+", url):
            raise ValueError("URL n8n invalide (http:// ou https:// requis).")
        cfg: dict[str, Any] = {"url": url, "timeout": max(3, min(int(timeout or 20), 120)),
                               "verify_ssl": bool(verify_ssl), "webhook_base": webhook_base.strip()}
        if api_key:
            cfg["api_key"] = api_key.strip()
        existing = self.connector()
        if existing:
            return self._core.connectors.update(existing["id"], {"config": cfg})
        if not api_key:
            raise ValueError("Clé API n8n requise.")
        return self._core.connectors.create({"type": "n8n", "name": "n8n", "id": "n8n", "config": cfg,
                                             "permissions": ["read", "execute"]})

    # -- lecture -----------------------------------------------------------
    def status(self) -> dict[str, Any]:
        c = self.connector()
        if not c:
            return {"state": "NOT_CONFIGURED", "detail": "Aucun connecteur n8n.", "configured": False}
        cfg = c.get("config") or {}
        host = urllib.parse.urlparse(str(cfg.get("url") or "")).hostname or ""
        base = {"configured": True, "connector_id": c["id"], "instance": host,
                "url": cfg.get("url", ""), "timeout": cfg.get("timeout") or 20,
                "verify_ssl": bool(cfg.get("verify_ssl", True)),
                "api_key": {"configured": self._core.vault.has(c["id"], "api_key"),
                            "preview": self._core.vault.preview(c["id"], "api_key")}}
        started = time.time()
        try:
            workflows = self.list_workflows(sync=True)
        except N8nError as exc:
            state = {"AUTH_REQUIRED": "WAITING_USER", "NETWORK_ERROR": "DISCONNECTED",
                     "CONNECTOR_MISSING": "NOT_CONFIGURED"}.get(exc.category, "ERROR")
            human = {"AUTH_REQUIRED": "Authentification refusée — clé API invalide ou expirée",
                     "NETWORK_ERROR": "Instance injoignable — vérifiez l'URL et que n8n est en ligne",
                     "INVALID_CONFIGURATION": "URL incorrecte — l'API /api/v1 est introuvable",
                     "EXTERNAL_SERVICE_ERROR": "n8n a renvoyé une erreur"}.get(exc.category, "")
            return {**base, "state": state, "category": exc.category,
                    "detail": f"{human} ({exc})" if human else str(exc),
                    "http_status": exc.status, "checked_at": time.time()}
        latency = int((time.time() - started) * 1000)
        version = ""
        ok, payload = http_json(f"{str(cfg.get('url')).rstrip('/')}/rest/settings", timeout=5,
                                verify_ssl=bool(cfg.get("verify_ssl", True)))
        if ok and isinstance(payload, dict):
            version = str((payload.get("data") or payload).get("versionCli") or "")
        active = sum(1 for w in workflows if w["active"])
        return {**base, "state": "DEGRADED" if latency > 4000 else "CONNECTED",
                "detail": f"Connexion réussie · latence {latency} ms", "latency_ms": latency,
                "version": version, "workflows": len(workflows), "active": active,
                "inactive": len(workflows) - active, "checked_at": time.time()}

    def list_workflows(self, sync: bool = True) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor = ""
        for _ in range(20):                      # 20 pages × 100 = 2000 workflows max
            q = "?limit=100" + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else "")
            payload = self._api("/workflows" + q)
            data = payload.get("data") if isinstance(payload, dict) else payload
            items.extend(data or [])
            cursor = payload.get("nextCursor") if isinstance(payload, dict) else ""
            if not cursor:
                break
        rows = [self._summarize(w) for w in items]
        if sync:
            self._index(rows)
        return rows

    def get_workflow(self, ref: str) -> dict[str, Any]:
        known = self.resolve(ref)
        w = self._api(f"/workflows/{urllib.parse.quote(known['workflow_id'])}")
        summary = self._summarize(w)
        if not summary["name"] or summary["workflow_id"] in ("", "None"):   # réponse incomplète
            summary = {**summary, **{k: known[k] for k in ("workflow_id", "name", "active")}}
        summary["node_list"] = [{"name": n.get("name"), "type": str(n.get("type", "")).split(".")[-1]}
                                for n in (w.get("nodes") or [])]
        return summary

    def executions(self, ref: str = "", limit: int = 10, status: str = "") -> list[dict[str, Any]]:
        q = f"?limit={max(1, min(limit, 50))}"
        if status in ("error", "success", "waiting"):
            q += f"&status={status}"
        if ref:
            q += f"&workflowId={urllib.parse.quote(self.resolve(ref)['workflow_id'])}"
        payload = self._api("/executions" + q)
        data = payload.get("data") if isinstance(payload, dict) else payload
        return [{"id": e.get("id"), "workflow_id": e.get("workflowId"), "status": e.get("status")
                 or ("success" if e.get("finished") else "error"), "mode": e.get("mode"),
                 "started_at": e.get("startedAt"), "stopped_at": e.get("stoppedAt")} for e in data or []]

    # -- écriture (toujours confirmée en amont) ----------------------------
    def set_active(self, ref: str, active: bool) -> dict[str, Any]:
        w = self.resolve(ref)
        verb = "activate" if active else "deactivate"
        self._api(f"/workflows/{urllib.parse.quote(w['workflow_id'])}/{verb}", method="POST")
        self._core.db.execute("UPDATE n8n_workflow_index SET active=? WHERE workflow_id=?",
                              (1 if active else 0, w["workflow_id"]))
        return {**w, "active": active}

    def run(self, ref: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Déclenche un workflow via son Webhook réel. L'API publique n8n n'offre
        pas d'exécution directe : sans déclencheur webhook, on le dit franchement."""
        c, url, _ = self._cfg()
        w = self.get_workflow(ref)
        hooks = [t for t in w["triggers"] if t.get("kind") == "webhook" and t.get("path")]
        if not hooks:
            raise N8nError(f"« {w['name']} » n'a pas de déclencheur Webhook : l'API publique n8n ne "
                           "permet pas de le lancer directement. Ouvrez-le dans n8n ou ajoutez un Webhook.",
                           "TOOL_UNAVAILABLE")
        if not w["active"]:
            raise N8nError(f"« {w['name']} » est inactif : son webhook de production ne répond pas.",
                           "INVALID_CONFIGURATION")
        cfg = c.get("config") or {}
        base = str(cfg.get("webhook_base") or f"{url}/webhook").rstrip("/")
        hook = hooks[0]
        ok, payload = http_json(f"{base}/{hook['path'].lstrip('/')}", method=hook.get("method") or "POST",
                                body=data or {}, verify_ssl=bool(cfg.get("verify_ssl", True)),
                                timeout=float(cfg.get("timeout") or 20) * 3)
        if not ok:
            category, code = classify_http_failure(payload)
            raise N8nError(self._core.vault.scrub(str(payload))[:300], category, code)
        return {"workflow": w, "response": payload}

    # -- index & recherche -------------------------------------------------
    @staticmethod
    def _summarize(w: dict[str, Any]) -> dict[str, Any]:
        nodes = w.get("nodes") or []
        triggers = []
        for n in nodes:
            t = str(n.get("type") or "")
            if "trigger" in t.lower() or t.endswith(".webhook") or t.endswith(".cron"):
                p = n.get("parameters") or {}
                triggers.append({"kind": "webhook" if t.endswith(".webhook") else t.split(".")[-1],
                                 "path": p.get("path") or "", "method": p.get("httpMethod") or "POST"})
        return {"workflow_id": str(w.get("id")), "name": str(w.get("name") or ""),
                "active": bool(w.get("active")), "tags": [t.get("name") for t in w.get("tags") or []
                                                          if isinstance(t, dict)],
                "nodes": sorted({str(n.get("type", "")).split(".")[-1] for n in nodes})[:40],
                "description": str(w.get("description") or (w.get("meta") or {}).get("description") or "")[:400],
                "triggers": triggers, "updated_at": str(w.get("updatedAt") or ""),
                "created_at": str(w.get("createdAt") or "")}

    def _index(self, rows: list[dict[str, Any]]) -> None:
        import json
        c = self.connector() or {}
        now = time.time()
        with self._lock:
            self._core.db.execute("DELETE FROM n8n_workflow_index WHERE connector_id=?", (c.get("id", ""),))
            for r in rows:
                self._core.db.execute(
                    "INSERT OR REPLACE INTO n8n_workflow_index(workflow_id, connector_id, name, active, nodes,"
                    " tags, description, triggers, updated_at, synced_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (r["workflow_id"], c.get("id", ""), r["name"], 1 if r["active"] else 0,
                     json.dumps(r["nodes"]), json.dumps(r["tags"]), r["description"],
                     json.dumps(r["triggers"]), r["updated_at"], now))

    def indexed(self) -> list[dict[str, Any]]:
        import json
        c = self.connector()
        if not c:
            return []
        out = []
        for r in self._core.db.query("SELECT * FROM n8n_workflow_index WHERE connector_id=? ORDER BY name",
                                     (c["id"],)):
            d = {k: r[k] for k in r.keys()}
            for k in ("nodes", "tags", "triggers"):
                d[k] = json.loads(d[k] or "[]")
            d["active"] = bool(d["active"])
            out.append(d)
        return out

    def search(self, query: str, workflows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        rows = workflows if workflows is not None else (self.indexed() or self.list_workflows())
        terms = set(_tokens(query))
        for group in _SYNONYMS.values():
            if terms & {_norm(g) for g in group} or any(t.startswith(g[:5]) for t in terms for g in group):
                terms |= {_norm(g) for g in group}
        scored = []
        for w in rows:
            name = _norm(w["name"])
            hay = " ".join([name, " ".join(w.get("tags") or []), " ".join(w.get("nodes") or []),
                            _norm(w.get("description") or "")]).casefold()
            score = sum(3 if t in name else 1 for t in terms if t in hay)
            if score:
                scored.append((score, w))
        return [w for _, w in sorted(scored, key=lambda x: -x[0])]

    def resolve(self, ref: str) -> dict[str, Any]:
        rows = self.indexed() or self.list_workflows()
        q = _norm(ref).strip(" «»\"'")
        for w in rows:
            if w["workflow_id"] == ref or _norm(w["name"]) == q:
                return w
        partial = [w for w in rows if q and q in _norm(w["name"])]
        if len(partial) == 1:
            return partial[0]
        hits = self.search(ref, rows)
        if hits and (len(hits) == 1 or partial):
            return (partial or hits)[0]
        if len(partial) > 1:
            raise N8nError("Plusieurs workflows correspondent : " + ", ".join(w["name"] for w in partial[:6]),
                           "USER_CONFIRMATION_REQUIRED")
        raise N8nError(f"Workflow « {ref} » introuvable dans l'instance n8n.", "INVALID_CONFIGURATION")
