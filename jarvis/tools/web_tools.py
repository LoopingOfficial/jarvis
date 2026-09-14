"""Outils web et services : HTTP, recherche, GitHub, n8n, webhooks, messagerie, Google."""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

from ..connectors import http_json
from ..permissions import READ_ONLY, SAFE_WRITE, SENSITIVE
from ..google_sheets import read_public_sheet
from .base import ToolContext, ToolResult, registry

GOOGLE_APPS = {
    "gmail": ("https://mail.google.com/", "Gmail"),
    "mail": ("https://mail.google.com/", "Gmail"),
    "agenda": ("https://calendar.google.com/", "Google Agenda"),
    "calendar": ("https://calendar.google.com/", "Google Agenda"),
    "drive": ("https://drive.google.com/", "Google Drive"),
    "docs": ("https://docs.google.com/", "Google Docs"),
    "sheets": ("https://sheets.google.com/", "Google Sheets"),
    "slides": ("https://slides.google.com/", "Google Slides"),
    "meet": ("https://meet.google.com/", "Google Meet"),
    "contacts": ("https://contacts.google.com/", "Google Contacts"),
    "photos": ("https://photos.google.com/", "Google Photos"),
    "maps": ("https://maps.google.com/", "Google Maps"),
}


def _http_request(ctx: ToolContext) -> ToolResult:
    url = str(ctx.arguments.get("url") or "").strip()
    method = str(ctx.arguments.get("method") or "GET").upper()
    headers: dict[str, str] = {}
    if ctx.connector:
        base = str(ctx.config.get("base_url") or "").rstrip("/")
        if url and not url.lower().startswith("http") and base:
            url = f"{base}/{url.lstrip('/')}"
        raw_headers = ctx.config.get("headers")
        if raw_headers:
            try:
                headers.update(json.loads(raw_headers) if isinstance(raw_headers, str) else dict(raw_headers))
            except Exception:
                pass
        key = ctx.secret("api_key")
        if key:
            header_name = str(ctx.config.get("auth_header") or "Authorization")
            prefix = str(ctx.config.get("auth_prefix") or "Bearer").strip()
            headers[header_name] = f"{prefix} {key}".strip()
    if not url.lower().startswith("http"):
        return ToolResult(False, "URL absolue requise (ou connecteur http_api avec base_url).")
    extra = ctx.arguments.get("headers")
    if isinstance(extra, dict):
        headers.update({str(k): str(v) for k, v in extra.items()})
    body = ctx.arguments.get("body")
    ok, payload = http_json(url, method=method, headers=headers, body=body, timeout=30)
    if not ok:
        return ToolResult(False, str(payload)[:2000])
    text = json.dumps(payload, ensure_ascii=False, indent=2) if isinstance(payload, (dict, list)) else str(payload)
    return ToolResult(True, text[:15000], data=payload)


registry.add(
    id="http.request", name="Requête HTTP", category="Web",
    description="Appelle une API HTTP/JSON. Avec un connecteur http_api, l'authentification est ajoutée automatiquement.",
    handler=_http_request, connector_type="http_api", connector_optional=True, risk=READ_ONLY,
    risk_resolver=lambda a: READ_ONLY if str(a.get("method", "GET")).upper() in {"GET", "HEAD"} else SAFE_WRITE,
    input_schema={"type": "object", "properties": {
        "connector_id": {"type": "string"}, "url": {"type": "string"},
        "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
        "headers": {"type": "object"}, "body": {"type": "object"}}, "required": ["url"]},
)


def _http_check(ctx: ToolContext) -> ToolResult:
    import urllib.error
    import urllib.request

    url = str(ctx.arguments.get("url") or "").strip()
    if not url.lower().startswith("http"):
        url = "https://" + url
    import time

    started = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "JARVIS/3.0"}, method="GET")
        with urllib.request.urlopen(req, timeout=20) as resp:
            code = resp.getcode()
            body = resp.read(4096).decode("utf-8", errors="replace")
        elapsed = int((time.time() - started) * 1000)
        title = ""
        m = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
        if m:
            title = m.group(1).strip()[:120]
        return ToolResult(True, f"{url} → HTTP {code} en {elapsed} ms" + (f" — « {title} »" if title else ""),
                          data={"status": code, "ms": elapsed, "title": title})
    except urllib.error.HTTPError as exc:
        elapsed = int((time.time() - started) * 1000)
        return ToolResult(False, f"{url} → HTTP {exc.code} ({exc.reason}) en {elapsed} ms",
                          data={"status": exc.code, "ms": elapsed})
    except Exception as exc:
        return ToolResult(False, f"{url} injoignable : {exc}", data={"status": 0})


registry.add(
    id="web.check", name="Vérifier un site", category="Web",
    description="Vérifie qu'une URL répond : code HTTP, temps de réponse, titre de la page.",
    handler=_http_check, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
)


def _web_search(ctx: ToolContext) -> ToolResult:
    query = str(ctx.arguments.get("query") or "").strip()
    if not query:
        return ToolResult(False, "Requête manquante.")
    ok, payload = http_json(
        "https://duckduckgo.com/?" + urllib.parse.urlencode({"q": query, "format": "json", "no_html": 1}),
        timeout=15)
    results: list[dict[str, str]] = []
    if not ok:
        ok, payload = http_json(
            "https://api.duckduckgo.com/?" + urllib.parse.urlencode(
                {"q": query, "format": "json", "no_html": 1, "skip_disambig": 1}), timeout=15)
    if ok and isinstance(payload, dict):
        if payload.get("AbstractText"):
            results.append({"title": payload.get("Heading", query), "snippet": payload["AbstractText"],
                            "url": payload.get("AbstractURL", "")})
        for topic in (payload.get("RelatedTopics") or [])[:6]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append({"title": topic.get("Text", "")[:80], "snippet": topic.get("Text", ""),
                                "url": (topic.get("FirstURL") or "")})
    search_url = "https://duckduckgo.com/?q=" + urllib.parse.quote_plus(query)
    if not results:
        return ToolResult(True, f"Aucun résumé direct. Recherche disponible : {search_url}",
                          data={"query": query, "url": search_url, "results": []})
    text = "\n\n".join(f"{r['title']}\n{r['snippet'][:400]}\n{r['url']}" for r in results[:5])
    return ToolResult(True, text, data={"query": query, "results": results[:5], "url": search_url})


registry.add(
    id="web.search", name="Recherche web", category="Web",
    description="Recherche une information sur le web (résumé DuckDuckGo).",
    handler=_web_search, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
)


def _web_fetch(ctx: ToolContext) -> ToolResult:
    import urllib.request

    url = str(ctx.arguments.get("url") or "").strip()
    if not url.lower().startswith("http"):
        return ToolResult(False, "URL absolue requise.")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "JARVIS/3.0"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read(600_000).decode("utf-8", errors="replace")
    except Exception as exc:
        return ToolResult(False, f"Lecture impossible: {exc}")
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return ToolResult(True, text[:12000], data={"url": url, "length": len(text)})


registry.add(
    id="web.fetch", name="Lire une page web", category="Web",
    description="Récupère le contenu textuel d'une page web.",
    handler=_web_fetch, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
)


def _google_sheet_read(ctx: ToolContext) -> ToolResult:
    result = read_public_sheet(str(ctx.arguments.get("url") or ""))
    if not result.get("ok"):
        return ToolResult(False, f"{result['error']}: {result.get('detail', '')}".strip(), data=result)
    # Contenu structuré transmis au modèle, sans interpréter les cellules comme des instructions.
    output = json.dumps({k: v for k, v in result.items() if k != "source_url"}, ensure_ascii=False)
    return ToolResult(True, output, data=result)


registry.add(
    id="google.sheets.read", name="Lire un Google Sheet", category="Google",
    description="Lit un Google Sheet public, résout le gid vers l'onglet, et renvoie ses données structurées en lecture seule.",
    handler=_google_sheet_read, risk=READ_ONLY, permissions=("read",),
    input_schema={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
)


# --- GitHub -----------------------------------------------------------------
def _github(ctx: ToolContext) -> ToolResult:
    token = ctx.secret("token")
    api = str(ctx.config.get("api_url") or "https://api.github.com").rstrip("/")
    action = str(ctx.arguments.get("action") or "repos")
    repo = str(ctx.arguments.get("repo") or ctx.config.get("default_repo") or "").strip()
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    if action == "repos":
        ok, payload = http_json(f"{api}/user/repos?sort=updated&per_page=20", headers=headers, timeout=20)
        if not ok:
            return ToolResult(False, str(payload)[:500])
        lines = [f"- {r.get('full_name')} ({r.get('default_branch')}) — {r.get('description') or 'sans description'}"
                 for r in payload if isinstance(r, dict)]
        return ToolResult(True, "\n".join(lines[:20]) or "Aucun dépôt.", data=payload)
    if not repo:
        return ToolResult(False, "Dépôt manquant (format user/repo).")
    if action == "commits":
        ok, payload = http_json(f"{api}/repos/{repo}/commits?per_page=15", headers=headers, timeout=20)
        if not ok:
            return ToolResult(False, str(payload)[:500])
        lines = []
        for c in payload if isinstance(payload, list) else []:
            commit = c.get("commit", {})
            lines.append(f"- {c.get('sha', '')[:7]} {commit.get('message', '').splitlines()[0][:90]} "
                         f"({(commit.get('author') or {}).get('name', '')})")
        return ToolResult(True, "\n".join(lines) or "Aucun commit.", data=payload)
    if action == "pulls":
        ok, payload = http_json(f"{api}/repos/{repo}/pulls?state=open&per_page=20", headers=headers, timeout=20)
        if not ok:
            return ToolResult(False, str(payload)[:500])
        lines = [f"- #{p.get('number')} {p.get('title')} par {(p.get('user') or {}).get('login')}"
                 for p in payload if isinstance(p, dict)]
        return ToolResult(True, "\n".join(lines) or "Aucune pull request ouverte.", data=payload)
    if action == "issues":
        ok, payload = http_json(f"{api}/repos/{repo}/issues?state=open&per_page=20", headers=headers, timeout=20)
        if not ok:
            return ToolResult(False, str(payload)[:500])
        lines = [f"- #{i.get('number')} {i.get('title')}" for i in payload
                 if isinstance(i, dict) and not i.get("pull_request")]
        return ToolResult(True, "\n".join(lines) or "Aucune issue ouverte.", data=payload)
    if action == "workflows":
        ok, payload = http_json(f"{api}/repos/{repo}/actions/runs?per_page=10", headers=headers, timeout=20)
        if not ok:
            return ToolResult(False, str(payload)[:500])
        runs = payload.get("workflow_runs", []) if isinstance(payload, dict) else []
        lines = [f"- {r.get('name')} [{r.get('status')}/{r.get('conclusion')}] {r.get('head_branch')}" for r in runs]
        return ToolResult(True, "\n".join(lines) or "Aucune exécution.", data=payload)
    return ToolResult(False, "Action GitHub inconnue.")


registry.add(
    id="github.query", name="GitHub", category="Développement",
    description="Consulte GitHub : repos, commits, pulls, issues, workflows.",
    handler=_github, connector_type="github", risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "connector_id": {"type": "string"},
        "action": {"type": "string", "enum": ["repos", "commits", "pulls", "issues", "workflows"]},
        "repo": {"type": "string"}}, "required": ["action"]},
)


# --- n8n --------------------------------------------------------------------
def _n8n(ctx: ToolContext) -> ToolResult:
    url = str(ctx.config.get("url") or "").rstrip("/")
    key = ctx.secret("api_key")
    headers = {"X-N8N-API-KEY": key} if key else {}
    action = str(ctx.arguments.get("action") or "list")

    if action == "list":
        ok, payload = http_json(f"{url}/api/v1/workflows?limit=50", headers=headers, timeout=20)
        if not ok:
            return ToolResult(False, f"n8n: {payload}"[:500])
        items = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(items, list) or not items:
            return ToolResult(True, "Aucun workflow n8n.", data={"workflows": []})
        lines = [f"- {w.get('name')} [{'actif' if w.get('active') else 'inactif'}] id={w.get('id')}" for w in items]
        return ToolResult(True, "Workflows n8n :\n" + "\n".join(lines[:40]), data={"workflows": items})

    name = str(ctx.arguments.get("workflow") or "").strip()
    if not name:
        return ToolResult(False, "Nom ou id du workflow manquant.")
    ok, payload = http_json(f"{url}/api/v1/workflows?limit=100", headers=headers, timeout=20)
    if not ok:
        return ToolResult(False, f"n8n: {payload}"[:400])
    items = payload.get("data") if isinstance(payload, dict) else []
    q = name.casefold()
    target = next((w for w in items if str(w.get("id")) == name or str(w.get("name", "")).casefold() == q), None)
    if not target:
        target = next((w for w in items if q in str(w.get("name", "")).casefold()), None)
    if not target:
        return ToolResult(False, f"Workflow « {name} » introuvable.")
    wid = urllib.parse.quote(str(target.get("id")))

    if action == "activate" or action == "deactivate":
        verb = "activate" if action == "activate" else "deactivate"
        ok, result = http_json(f"{url}/api/v1/workflows/{wid}/{verb}", method="POST", headers=headers, timeout=20)
        return ToolResult(ok, f"Workflow « {target.get('name')} » {verb}." if ok else str(result)[:400], risk=SAFE_WRITE)

    ok, result = http_json(f"{url}/api/v1/workflows/{wid}/run", method="POST", headers=headers,
                           body=ctx.arguments.get("data") or {}, timeout=60)
    if not ok:
        ok2, result2 = http_json(f"{url}/api/v1/workflows/{wid}/activate", method="POST", headers=headers, timeout=20)
        if ok2:
            return ToolResult(True, f"Workflow « {target.get('name')} » activé (exécution directe indisponible "
                                    f"avec cette clé API).", risk=SAFE_WRITE)
        return ToolResult(False, f"Exécution impossible : {result}"[:500])
    return ToolResult(True, f"Workflow « {target.get('name')} » déclenché.", risk=SAFE_WRITE, data=result)


registry.add(
    id="n8n.workflow", name="n8n", category="Automatisation",
    description="Liste, lance, active ou désactive un workflow n8n.",
    handler=_n8n, connector_type="n8n", risk=READ_ONLY,
    risk_resolver=lambda a: READ_ONLY if a.get("action") == "list" else SAFE_WRITE,
    permissions=("execute",),
    input_schema={"type": "object", "properties": {
        "connector_id": {"type": "string"},
        "action": {"type": "string", "enum": ["list", "run", "activate", "deactivate"]},
        "workflow": {"type": "string"}, "data": {"type": "object"}}, "required": ["action"]},
)


# --- Webhook / messagerie ---------------------------------------------------
def _webhook(ctx: ToolContext) -> ToolResult:
    url = str(ctx.config.get("url") or ctx.arguments.get("url") or "")
    if not url:
        return ToolResult(False, "URL du webhook manquante.")
    method = str(ctx.config.get("method") or "POST").upper()
    headers: dict[str, str] = {}
    raw_headers = ctx.config.get("headers")
    if raw_headers:
        try:
            headers.update(json.loads(raw_headers) if isinstance(raw_headers, str) else dict(raw_headers))
        except Exception:
            pass
    secret = ctx.secret("secret")
    if secret:
        headers.setdefault("X-Jarvis-Signature", secret)
    ok, payload = http_json(url, method=method, headers=headers,
                            body=ctx.arguments.get("payload") or {}, timeout=25)
    return ToolResult(ok, "Webhook déclenché." if ok else str(payload)[:400], risk=SAFE_WRITE, data=payload)


registry.add(
    id="webhook.trigger", name="Déclencher un webhook", category="Automatisation",
    description="Envoie une charge utile JSON vers un webhook configuré.",
    handler=_webhook, connector_type="webhook", risk=SAFE_WRITE, permissions=("execute",),
    input_schema={"type": "object", "properties": {
        "connector_id": {"type": "string"}, "payload": {"type": "object"}}, "required": []},
)


def _chat_send(ctx: ToolContext) -> ToolResult:
    message = str(ctx.arguments.get("message") or "").strip()
    if not message:
        return ToolResult(False, "Message vide.")
    ctype = (ctx.connector or {}).get("type", "")
    hook = ctx.secret("webhook_url")
    token = ctx.secret("bot_token")
    channel = str(ctx.arguments.get("channel") or ctx.config.get("default_channel") or "")
    if ctype == "slack":
        if token and channel:
            ok, payload = http_json("https://slack.com/api/chat.postMessage", method="POST",
                                    headers={"Authorization": f"Bearer {token}"},
                                    body={"channel": channel, "text": message}, timeout=20)
            if ok and isinstance(payload, dict) and payload.get("ok"):
                return ToolResult(True, f"Message envoyé sur {channel}.", risk=SAFE_WRITE)
            return ToolResult(False, str(payload)[:300])
        if hook:
            ok, payload = http_json(hook, method="POST", body={"text": message}, timeout=20)
            return ToolResult(ok, "Message envoyé." if ok else str(payload)[:300], risk=SAFE_WRITE)
        return ToolResult(False, "Aucun webhook ni token Slack configuré.")
    if hook:
        ok, payload = http_json(hook, method="POST", body={"content": message[:1900]}, timeout=20)
        return ToolResult(ok, "Message envoyé sur Discord." if ok else str(payload)[:300], risk=SAFE_WRITE)
    return ToolResult(False, "Aucun webhook Discord configuré.")


registry.add(
    id="chat.send", name="Envoyer un message", category="Communication",
    description="Envoie un message sur Discord ou Slack via le connecteur configuré.",
    handler=_chat_send, connector_type="discord", connector_optional=True, risk=SAFE_WRITE,
    permissions=("write",),
    input_schema={"type": "object", "properties": {
        "connector_id": {"type": "string"}, "message": {"type": "string"},
        "channel": {"type": "string"}}, "required": ["message"]},
)


def _email_send(ctx: ToolContext) -> ToolResult:
    import smtplib
    from email.message import EmailMessage

    to = str(ctx.arguments.get("to") or "").strip()
    subject = str(ctx.arguments.get("subject") or "(sans objet)")
    body = str(ctx.arguments.get("body") or "")
    if not to:
        return ToolResult(False, "Destinataire manquant.")
    cfg = ctx.config
    msg = EmailMessage()
    msg["From"] = str(cfg.get("from_address") or cfg.get("username") or "")
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        server = smtplib.SMTP(str(cfg.get("host")), int(cfg.get("port") or 587), timeout=25)
        if cfg.get("tls", True):
            server.starttls()
        server.login(str(cfg.get("username")), ctx.secret("password"))
        server.send_message(msg)
        server.quit()
        return ToolResult(True, f"Email envoyé à {to}.", risk=SAFE_WRITE)
    except Exception as exc:
        return ToolResult(False, f"Envoi impossible: {exc}")


registry.add(
    id="email.send", name="Envoyer un email", category="Communication",
    description="Envoie un email via le connecteur SMTP configuré.",
    handler=_email_send, connector_type="smtp", risk=SENSITIVE, permissions=("write",),
    dangerous_hint="Un email va être envoyé depuis ton adresse.",
    input_schema={"type": "object", "properties": {
        "connector_id": {"type": "string"}, "to": {"type": "string"},
        "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["to", "body"]},
)


def _email_read(ctx: ToolContext) -> ToolResult:
    import email as email_lib
    import imaplib

    cfg = ctx.config
    limit = int(ctx.arguments.get("limit") or 10)
    try:
        box = imaplib.IMAP4_SSL(str(cfg.get("host")), int(cfg.get("port") or 993)) if cfg.get("ssl", True) \
            else imaplib.IMAP4(str(cfg.get("host")), int(cfg.get("port") or 143))
        box.login(str(cfg.get("username")), ctx.secret("password"))
        box.select("INBOX")
        typ, data = box.search(None, "UNSEEN" if ctx.arguments.get("unread_only", True) else "ALL")
        ids = (data[0].split() if data and data[0] else [])[-limit:]
        lines = []
        for mid in reversed(ids):
            typ, raw = box.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if typ != "OK" or not raw or not raw[0]:
                continue
            msg = email_lib.message_from_bytes(raw[0][1])
            lines.append(f"- {msg.get('From', '')[:60]} — {msg.get('Subject', '')[:80]}")
        box.close()
        box.logout()
        return ToolResult(True, "\n".join(lines) or "Aucun message.", data={"count": len(lines)})
    except Exception as exc:
        return ToolResult(False, f"IMAP: {exc}")


registry.add(
    id="email.read", name="Lire les emails", category="Communication",
    description="Liste les derniers emails (expéditeur + objet) via IMAP.",
    handler=_email_read, connector_type="imap", risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "connector_id": {"type": "string"}, "limit": {"type": "integer"},
        "unread_only": {"type": "boolean"}}, "required": []},
)


def _email_process_inbox(ctx: ToolContext) -> ToolResult:
    """Lit la boîte et classe chaque message. Ne modifie jamais la boîte."""
    from ..mail import MailProcessor, summarize

    args = ctx.arguments
    use_mock = args.get("use_mock")
    result = MailProcessor(ctx.core).process(
        limit=int(args.get("limit") or 20),
        unread_only=bool(args.get("unread_only", False)),
        connector_id=str(args.get("connector_id") or ""),
        use_mock=None if use_mock is None else bool(use_mock),
        task_id=ctx.task_id,
    )
    if not result.get("ok"):
        return ToolResult(False, summarize(result))
    return ToolResult(True, summarize(result), data={
        "source": result["source"], "total": result["total"],
        "counts": result["counts"], "cards": result["cards"],
    })


registry.add(
    id="email.process_inbox", name="Trier la boîte de réception", category="Communication",
    description=(
        "Lit les messages reçus et les classe en cinq catégories : à répondre, "
        "à transférer, factures, devis, archives. Chaque message est rendu avec "
        "le motif de son classement. Lecture seule : rien n'est envoyé, déplacé "
        "ni supprimé. Utilise le connecteur IMAP configuré, ou la boîte de "
        "démonstration locale si aucun n'est disponible."
    ),
    # Le connecteur est résolu par MailProcessor (IMAP réel ou mock local) :
    # on ne déclare pas connector_type, sinon l'absence de connecteur IMAP
    # rendrait l'outil inappelable même avec la boîte de démonstration.
    handler=_email_process_inbox, risk=READ_ONLY, agents=("jarvis", "email"),
    input_schema={"type": "object", "properties": {
        "connector_id": {"type": "string", "description": "Connecteur IMAP à utiliser (optionnel)."},
        "limit": {"type": "integer", "description": "Nombre maximum de messages à trier (défaut 20)."},
        "unread_only": {"type": "boolean", "description": "Ne trier que les messages non lus."},
        "use_mock": {"type": "boolean", "description": "Forcer la boîte de démonstration locale."}},
        "required": []},
)


# --- Météo (Open-Meteo, gratuit sans clé) -----------------------------------
_WMO_CODES = {
    0: "ciel dégagé", 1: "peu nuageux", 2: "partiellement nuageux", 3: "couvert",
    45: "brouillard", 48: "brouillard givrant",
    51: "bruine légère", 53: "bruine", 55: "bruine dense",
    56: "bruine verglaçante légère", 57: "bruine verglaçante dense",
    61: "pluie faible", 63: "pluie", 65: "forte pluie",
    66: "pluie verglaçante légère", 67: "pluie verglaçante forte",
    71: "neige faible", 73: "neige", 75: "forte neige", 77: "grains de neige",
    80: "averses légères", 81: "averses", 82: "averses violentes",
    85: "averses de neige légères", 86: "averses de neige",
    95: "orage", 96: "orage avec grêle légère", 99: "orage avec grêle forte",
}


def _wmo_label(code) -> str:
    try:
        return _WMO_CODES.get(int(code), f"code {code}")
    except (TypeError, ValueError):
        return "conditions inconnues"


def _weather_forecast(ctx: ToolContext) -> ToolResult:
    city = str(ctx.arguments.get("city") or "").strip()
    lat = ctx.arguments.get("latitude")
    lon = ctx.arguments.get("longitude")
    days = min(int(ctx.arguments.get("forecast_days") or 3), 7)

    place = city or "vous"
    if (lat is None or lon is None) and city:
        ok, payload = http_json(
            "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
                {"name": city, "count": 1, "language": "fr", "format": "json"}), timeout=15)
        if not ok:
            return ToolResult(False, str(payload)[:400])
        results = payload.get("results", []) if isinstance(payload, dict) else []
        if not results:
            return ToolResult(False, f"Ville « {city} » introuvable.")
        first = results[0]
        lat, lon = first["latitude"], first["longitude"]
        place = f"{first.get('name', city)} ({first.get('country', '')})"
    if lat is None or lon is None:
        return ToolResult(False, "Indique une ville (city) ou des coordonnées (latitude/longitude).")

    ok, payload = http_json(
        "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
            "latitude": lat, "longitude": lon, "forecast_days": days, "timezone": "auto",
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                       "precipitation,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
        }), timeout=20)
    if not ok:
        return ToolResult(False, str(payload)[:400])

    lines = [f"Météo à {place}"]
    cur = payload.get("current", {}) if isinstance(payload, dict) else {}
    if cur:
        t = cur.get("temperature_2m")
        feel = cur.get("apparent_temperature")
        code = cur.get("weather_code")
        hum = cur.get("relative_humidity_2m")
        wind = cur.get("wind_speed_10m")
        rain = cur.get("precipitation")
        detail = f"Maintenant : {t} °C" + (f" (ressenti {feel} °C)" if feel is not None else "")
        if code is not None:
            detail += f" — {_wmo_label(code)}"
        lines.append(detail)
        parts = []
        if hum is not None:
            parts.append(f"humidité {hum} %")
        if wind is not None:
            parts.append(f"vent {wind} km/h")
        if rain is not None:
            parts.append(f"précipitations {rain} mm")
        if parts:
            lines.append(", ".join(parts))
    daily = payload.get("daily", {}) if isinstance(payload, dict) else {}
    dates = daily.get("time", [])
    for i in range(len(dates)):
        tmax = (daily.get("temperature_2m_max") or [])[i]
        tmin = (daily.get("temperature_2m_min") or [])[i]
        dcode = (daily.get("weather_code") or [])[i]
        prob = (daily.get("precipitation_probability_max") or [])[i]
        day = dates[i][:10]
        label = "Aujourd'hui" if i == 0 else f"Jour {i}"
        lines.append(f"{label} ({day}) : {tmin}–{tmax} °C, "
                     f"{_wmo_label(dcode)}" + (f", pluie {prob} %" if prob is not None else ""))
    return ToolResult(True, "\n".join(lines), data=payload)


registry.add(
    id="weather.forecast", name="Météo", category="Météo",
    description=("Donne la météo actuelle et les prévisions pour les prochains jours "
                 "(Open-Meteo, gratuit, sans clé API)."),
    handler=_weather_forecast, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "city": {"type": "string", "description": "Ville (ex. « Paris »)"},
        "latitude": {"type": "number"}, "longitude": {"type": "number"},
        "forecast_days": {"type": "integer", "minimum": 1, "maximum": 7}},
        "required": []},
)


# --- Google -----------------------------------------------------------------
def _google_open(ctx: ToolContext) -> ToolResult:
    app = str(ctx.arguments.get("app") or "").strip().casefold()
    app = re.sub(r"^google\s+", "", app)
    pair = GOOGLE_APPS.get(app)
    if not pair:
        return ToolResult(False, f"Application Google inconnue: {app}")
    from .system_tools import _open_url  # réutilise l'ouverture navigateur

    ctx.arguments["url"] = pair[0]
    result = _open_url(ctx)
    if result.ok:
        result.output = f"{pair[1]} est ouvert."
    return result


registry.add(
    id="google.open", name="Ouvrir un service Google", category="Productivité",
    description="Ouvre Gmail, Agenda, Drive, Docs, Sheets, Meet… dans le navigateur.",
    handler=_google_open, risk=SAFE_WRITE, permissions=("execute",),
    input_schema={"type": "object", "properties": {"app": {"type": "string"}}, "required": ["app"]},
)


def _google_calendar(ctx: ToolContext) -> ToolResult:
    """Lecture de l'agenda Google si OAuth configuré ; sinon message explicite."""
    cfg = ctx.config
    refresh = ctx.secret("refresh_token")
    client_id = str(cfg.get("client_id") or "")
    client_secret = ctx.secret("client_secret")
    token_file = str(cfg.get("token_file") or "")
    access_token = ""

    if refresh and client_id and client_secret:
        ok, payload = http_json("https://oauth2.googleapis.com/token", method="POST",
                                headers={"Content-Type": "application/x-www-form-urlencoded"},
                                body=urllib.parse.urlencode({
                                    "client_id": client_id, "client_secret": client_secret,
                                    "refresh_token": refresh, "grant_type": "refresh_token"}), timeout=20)
        if ok and isinstance(payload, dict):
            access_token = payload.get("access_token", "")
    elif token_file:
        from pathlib import Path

        p = Path(token_file).expanduser()
        if p.is_file():
            try:
                stored = json.loads(p.read_text(encoding="utf-8"))
                access_token = stored.get("token") or stored.get("access_token") or ""
                if not access_token and stored.get("refresh_token") and stored.get("client_id"):
                    ok, payload = http_json("https://oauth2.googleapis.com/token", method="POST",
                                            headers={"Content-Type": "application/x-www-form-urlencoded"},
                                            body=urllib.parse.urlencode({
                                                "client_id": stored["client_id"],
                                                "client_secret": stored.get("client_secret", ""),
                                                "refresh_token": stored["refresh_token"],
                                                "grant_type": "refresh_token"}), timeout=20)
                    if ok and isinstance(payload, dict):
                        access_token = payload.get("access_token", "")
            except Exception:
                pass
    if not access_token:
        return ToolResult(False, "Agenda Google non connecté (OAuth requis). "
                                 "Les événements locaux restent disponibles via calendar.list.")
    import time as _t

    days = int(ctx.arguments.get("days") or 7)
    now = _t.time()
    params = urllib.parse.urlencode({
        "timeMin": _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(now)),
        "timeMax": _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(now + days * 86400)),
        "singleEvents": "true", "orderBy": "startTime", "maxResults": 25,
    })
    ok, payload = http_json(f"https://www.googleapis.com/calendar/v3/calendars/primary/events?{params}",
                            headers={"Authorization": f"Bearer {access_token}"}, timeout=25)
    if not ok:
        return ToolResult(False, str(payload)[:400])
    items = payload.get("items", []) if isinstance(payload, dict) else []
    if not items:
        return ToolResult(True, f"Aucun événement dans les {days} prochains jours.", data={"events": []})
    lines = []
    for ev in items:
        start = (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date", "")
        lines.append(f"- {start[:16].replace('T', ' ')} — {ev.get('summary', '(sans titre)')}")
    ctx.core.calendar.sync_google(items)
    return ToolResult(True, "\n".join(lines), data={"events": items})


registry.add(
    id="google.calendar", name="Agenda Google", category="Productivité",
    description="Liste les prochains événements de l'agenda Google (nécessite OAuth).",
    handler=_google_calendar, connector_type="google", risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "connector_id": {"type": "string"}, "days": {"type": "integer"}}, "required": []},
)
