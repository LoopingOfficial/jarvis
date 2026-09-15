"""Outils distants : SSH, déploiement, cPanel, WHM, FTP/SFTP, bases de données, Docker."""
from __future__ import annotations

import base64
import json
import os
import subprocess
from typing import Any

from ..connectors import http_json
from ..permissions import DESTRUCTIVE, READ_ONLY, SAFE_WRITE, SENSITIVE, classify_command
from .base import ToolContext, ToolResult, registry
from .ssh_client import shell_quote, ssh_deploy, ssh_download, ssh_exec, ssh_upload
from ..remote_paths import resolveRemotePath


# --- SSH --------------------------------------------------------------------
def _ssh_run(ctx: ToolContext) -> ToolResult:
    command = str(ctx.arguments.get("command") or "").strip()
    if not command:
        return ToolResult(False, "Commande distante manquante.")
    if not ctx.connector:
        return ToolResult(False, "Aucun serveur SSH sélectionné.")
    timeout = int(ctx.arguments.get("timeout") or 90)
    secrets = ctx.secrets("password", "private_key", "passphrase")
    ok, out = ssh_exec(ctx.config, secrets, command, timeout=min(timeout, 600))
    label = ctx.connector["name"]
    return ToolResult(ok, f"[{label}] {out}" if ok else out, data={"connector": ctx.connector["id"]})


registry.add(
    id="ssh.run", name="Commande SSH", category="Infrastructure",
    description="Exécute une commande sur un serveur distant via SSH. Le mot de passe/la clé "
                "restent dans le vault : passer uniquement connector_id.",
    handler=_ssh_run, connector_type="ssh", risk=SAFE_WRITE,
    risk_resolver=lambda a: classify_command(str(a.get("command", ""))),
    permissions=("execute",),
    dangerous_hint="La commande sera exécutée sur le serveur distant.",
    input_schema={"type": "object", "properties": {
        "connector_id": {"type": "string", "description": "Identifiant du serveur SSH"},
        "command": {"type": "string"}, "timeout": {"type": "integer"}}, "required": ["command"]},
)


def _ssh_status(ctx: ToolContext) -> ToolResult:
    cmd = ("echo '### uptime'; uptime; echo '### disque'; df -h / 2>/dev/null | tail -2; "
           "echo '### mémoire'; free -h 2>/dev/null || vm_stat | head -6; "
           "echo '### processus'; ps aux --sort=-%cpu 2>/dev/null | head -6 || ps aux | head -6; "
           "echo '### services web'; (systemctl is-active nginx apache2 php-fpm mysql mariadb 2>/dev/null || "
           "service --status-all 2>/dev/null | head -12) ")
    ok, out = ssh_exec(ctx.config, ctx.secrets("password", "private_key", "passphrase"), cmd, timeout=60)
    return ToolResult(ok, out, data={"connector": (ctx.connector or {}).get("id")})


registry.add(
    id="ssh.status", name="Diagnostic serveur", category="Infrastructure",
    description="Relève uptime, disque, mémoire, top processus et état des services web d'un serveur.",
    handler=_ssh_status, connector_type="ssh", risk=READ_ONLY,
    input_schema={"type": "object", "properties": {"connector_id": {"type": "string"}}, "required": []},
)


def _ssh_logs(ctx: ToolContext) -> ToolResult:
    lines = int(ctx.arguments.get("lines") or 80)
    path = str(ctx.arguments.get("path") or "").strip()
    service = str(ctx.arguments.get("service") or "").strip()
    if path:
        cmd = f"tail -n {lines} {shell_quote(path)}"
    elif service:
        cmd = (f"journalctl -u {shell_quote(service)} -n {lines} --no-pager 2>/dev/null || "
               f"tail -n {lines} /var/log/{shell_quote(service)}.log 2>/dev/null || "
               f"echo 'Aucun log trouvé pour {service}'")
    else:
        cmd = ("for f in /var/log/nginx/error.log /var/log/apache2/error.log "
               "~/logs/error_log /var/log/syslog; do "
               f"[ -r \"$f\" ] && echo \"### $f\" && tail -n {lines} \"$f\"; done")
    ok, out = ssh_exec(ctx.config, ctx.secrets("password", "private_key", "passphrase"), cmd, timeout=90)
    return ToolResult(ok, out[:20000])


registry.add(
    id="ssh.logs", name="Logs serveur", category="Infrastructure",
    description="Lit les logs d'un serveur distant (fichier précis, service systemd, ou logs web usuels).",
    handler=_ssh_logs, connector_type="ssh", risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "connector_id": {"type": "string"}, "path": {"type": "string"},
        "service": {"type": "string"}, "lines": {"type": "integer"}}, "required": []},
)


def _ssh_service(ctx: ToolContext) -> ToolResult:
    service = str(ctx.arguments.get("service") or "").strip()
    action = str(ctx.arguments.get("action") or "status").strip()
    if not service:
        return ToolResult(False, "Nom du service manquant.")
    if action not in {"status", "start", "stop", "restart", "reload"}:
        return ToolResult(False, "Action invalide.")
    cmd = (f"(sudo -n systemctl {action} {shell_quote(service)} 2>/dev/null || "
           f"systemctl {action} {shell_quote(service)} 2>/dev/null || "
           f"service {shell_quote(service)} {action}) 2>&1 | head -30")
    ok, out = ssh_exec(ctx.config, ctx.secrets("password", "private_key", "passphrase"), cmd, timeout=90)
    return ToolResult(ok, out or f"{service}: {action} exécuté.")


registry.add(
    id="ssh.service", name="Gérer un service distant", category="Infrastructure",
    description="status/start/stop/restart/reload d'un service sur un serveur distant.",
    handler=_ssh_service, connector_type="ssh", permissions=("execute",),
    risk=SENSITIVE,
    risk_resolver=lambda a: READ_ONLY if str(a.get("action", "status")) == "status" else SENSITIVE,
    dangerous_hint="Redémarrer un service coupe temporairement le site.",
    input_schema={"type": "object", "properties": {
        "connector_id": {"type": "string"}, "service": {"type": "string"},
        "action": {"type": "string", "enum": ["status", "start", "stop", "restart", "reload"]}},
        "required": ["service", "action"]},
)


def _ssh_list(ctx: ToolContext) -> ToolResult:
    try:
        path = resolveRemotePath(ctx.connector or {}, ctx.arguments.get("path"), {})
    except Exception as exc:
        return ToolResult(False, str(exc))
    if not ctx.connector:
        return ToolResult(False, "Aucun serveur SSH sélectionné.")
    cmd = f"ls -la {shell_quote(path)} 2>&1 | head -100"
    ok, out = ssh_exec(ctx.config, ctx.secrets("password", "private_key", "passphrase"), cmd, timeout=60)
    return ToolResult(ok, out or "Dossier vide.", data={"source": "ssh", "connector": ctx.connector["id"], "path": path})


registry.add(
    id="ssh.list", name="Lister un dossier distant", category="Infrastructure",
    description="Liste le contenu d'un dossier sur un serveur distant (ls -la).",
    handler=_ssh_list, connector_type="ssh", risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "connector_id": {"type": "string"}, "path": {"type": "string"}}, "required": []},
)


def _ssh_read_file(ctx: ToolContext) -> ToolResult:
    connector = ctx.connector or {}
    cfg = connector.get("config") if isinstance(connector.get("config"), dict) else {}
    root = (cfg or {}).get("working_directory") or (cfg or {}).get("deployment_path") or (cfg or {}).get("remote_path") or ""
    # Le workspace configuré dans le contexte actif est prioritaire : il
    # représente la cible choisie pour ce job et évite tout fallback inventé.
    # Never reuse a global directory belonging to another connector/job.
    active = {"remote_root": root} if root else {}
    try:
        path = resolveRemotePath(connector, ctx.arguments.get("path"), active)
    except Exception as exc:
        return ToolResult(False, str(exc))
    if not path:
        return ToolResult(False, "Chemin manquant.")
    if not ctx.connector:
        return ToolResult(False, "Aucun serveur SSH sélectionné.")
    q = shell_quote(path)
    # Le canal de données doit rester le contenu brut, sans préfixe de
    # diagnostic ni résumé : Coding est la destination de ce texte exact.
    # Encode to preserve whitespace through the command-oriented SSH transport.
    # No pipeline may hide the file-read exit status.
    cmd = f"printf 'JARVIS_FILE_B64:'; base64 < {q}"
    ok, out = ssh_exec(ctx.config, ctx.secrets("password", "private_key", "passphrase"), cmd, timeout=60)
    if not ok:
        return ToolResult(False, out)
    try:
        if not out.startswith('JARVIS_FILE_B64:'):
            raise ValueError('enveloppe de lecture absente')
        content = base64.b64decode(''.join(out.split(':', 1)[1].split()), validate=True).decode('utf-8')
    except (ValueError, UnicodeError) as exc:
        return ToolResult(False, f"Lecture exacte impossible : {exc}")
    print(f"[CODE-TRACE] ssh.read_file success\n[CODE-TRACE] file path {path}\n[CODE-TRACE] content length {len(content)}", flush=True)
    return ToolResult(True, content, data={"source": "ssh", "connector": ctx.connector["id"], "path": path, "content": content})


registry.add(
    id="ssh.read_file", name="Lire un fichier distant", category="Infrastructure",
    description="Affiche le contenu d'un fichier situé sur un serveur distant.",
    handler=_ssh_read_file, connector_type="ssh", risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "connector_id": {"type": "string"}, "path": {"type": "string"}}, "required": ["path"]},
)


def _ssh_write_file(ctx: ToolContext) -> ToolResult:
    path = str(ctx.arguments.get("path") or "").strip()
    if not path:
        return ToolResult(False, "Chemin manquant.")
    if not ctx.connector:
        return ToolResult(False, "Aucun serveur SSH sélectionné.")
    content = str(ctx.arguments.get("content") or "")
    mode = str(ctx.arguments.get("mode") or "write")
    if mode not in {"write", "append"}:
        return ToolResult(False, "mode doit être write ou append.")
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    op = ">>" if mode == "append" else ">"
    cmd = (f"echo {shell_quote(b64)} | base64 -d {op} {shell_quote(path)} && "
           f"echo 'OK — ' && wc -c {shell_quote(path)} 2>&1 | head -1")
    ok, out = ssh_exec(ctx.config, ctx.secrets("password", "private_key", "passphrase"), cmd, timeout=90)
    return ToolResult(ok, f"Fichier {path} {('complété' if mode == 'append' else 'écrit')} " + (out or ""), risk=SENSITIVE)


registry.add(
    id="ssh.write_file", name="Écrire un fichier distant", category="Infrastructure",
    description="Écrit (mode=write) ou complète (mode=append) un fichier sur un serveur distant.",
    handler=_ssh_write_file, connector_type="ssh", risk=SENSITIVE, permissions=("write",),
    dangerous_hint="Le fichier distant sera écrasé sur la version envoyée.",
    input_schema={"type": "object", "properties": {
        "connector_id": {"type": "string"}, "path": {"type": "string"},
        "content": {"type": "string"},
        "mode": {"type": "string", "enum": ["write", "append"]}},
        "required": ["path", "content"]},
)


def _ssh_upload(ctx: ToolContext) -> ToolResult:
    if not ctx.connector:
        return ToolResult(False, "Aucun serveur SSH sélectionné.")
    local = str(ctx.arguments.get("local_path") or "").strip()
    remote = str(ctx.arguments.get("remote_path") or "").strip()
    if not local or not remote:
        return ToolResult(False, "local_path et remote_path requis.")
    ok, out = ssh_upload(ctx.config, ctx.secrets("password", "private_key", "passphrase"), local, remote)
    return ToolResult(ok, out, risk=SENSITIVE)


registry.add(
    id="ssh.upload", name="Envoyer un fichier distant", category="Infrastructure",
    description="Téléverse un fichier local vers un serveur distant (SFTP).",
    handler=_ssh_upload, connector_type="ssh", risk=SENSITIVE, permissions=("write",),
    dangerous_hint="Un fichier sera envoyé sur le serveur distant.",
    input_schema={"type": "object", "properties": {
        "connector_id": {"type": "string"}, "local_path": {"type": "string"},
        "remote_path": {"type": "string"}}, "required": ["local_path", "remote_path"]},
)


def _ssh_download(ctx: ToolContext) -> ToolResult:
    if not ctx.connector:
        return ToolResult(False, "Aucun serveur SSH sélectionné.")
    remote = str(ctx.arguments.get("remote_path") or "").strip()
    local = str(ctx.arguments.get("local_path") or "").strip()
    if not remote or not local:
        return ToolResult(False, "remote_path et local_path requis.")
    ok, out = ssh_download(ctx.config, ctx.secrets("password", "private_key", "passphrase"), remote, local)
    return ToolResult(ok, out, risk=SENSITIVE)


registry.add(
    id="ssh.download", name="Récupérer un fichier distant", category="Infrastructure",
    description="Télécharge un fichier distant vers la machine locale (SFTP).",
    handler=_ssh_download, connector_type="ssh", risk=SENSITIVE, permissions=("read",),
    input_schema={"type": "object", "properties": {
        "connector_id": {"type": "string"}, "remote_path": {"type": "string"},
        "local_path": {"type": "string"}}, "required": ["remote_path", "local_path"]},
)


def _deploy(ctx: ToolContext) -> ToolResult:
    local = str(ctx.arguments.get("local_path") or ctx.config.get("local_path")
                or ctx.core.settings.get("general", "default_project", "")).strip()
    if not local:
        return ToolResult(False, "Aucun dossier local à déployer.")
    remote = str(ctx.arguments.get("remote_path") or "").strip()
    ok, out = ssh_deploy(ctx.config, ctx.secrets("password", "private_key", "passphrase"), local, remote)
    return ToolResult(ok, out[:8000], risk=SENSITIVE)


registry.add(
    id="deploy.rsync", name="Déployer un projet", category="Infrastructure",
    description="Synchronise un dossier local vers le chemin distant du serveur (rsync/scp/SFTP).",
    handler=_deploy, connector_type="ssh", risk=SENSITIVE, permissions=("write",),
    dangerous_hint="Les fichiers distants seront remplacés par la version locale.",
    input_schema={"type": "object", "properties": {
        "connector_id": {"type": "string"}, "local_path": {"type": "string"},
        "remote_path": {"type": "string"}}, "required": []},
)


# --- cPanel / WHM -----------------------------------------------------------
def _cpanel_call(ctx: ToolContext, module: str, func: str, params: dict[str, Any] | None = None):
    host = str(ctx.config.get("host") or "").rstrip("/")
    user = str(ctx.config.get("username") or "")
    token = ctx.secret("token")
    if not (host and user and token):
        return False, "Connecteur cPanel incomplet (hôte, utilisateur, jeton)."
    import urllib.parse

    qs = urllib.parse.urlencode(params or {})
    url = f"{host}/execute/{module}/{func}" + (f"?{qs}" if qs else "")
    return http_json(url, headers={"Authorization": f"cpanel {user}:{token}"},
                     verify_ssl=bool(ctx.config.get("verify_ssl", True)), timeout=25)


def _cpanel(ctx: ToolContext) -> ToolResult:
    action = str(ctx.arguments.get("action") or "summary")
    if action == "emails":
        ok, payload = _cpanel_call(ctx, "Email", "list_pops")
        if not ok:
            return ToolResult(False, str(payload)[:600])
        data = payload.get("data") if isinstance(payload, dict) else payload
        if isinstance(data, list):
            lines = [f"- {r.get('email') or r.get('login')}" for r in data[:60]]
            return ToolResult(True, "Comptes email :\n" + "\n".join(lines), data={"emails": data})
        return ToolResult(True, json.dumps(payload, ensure_ascii=False)[:3000])
    if action == "domains":
        ok, payload = _cpanel_call(ctx, "DomainInfo", "list_domains")
        return ToolResult(ok, json.dumps(payload, ensure_ascii=False, indent=2)[:4000] if ok else str(payload)[:600],
                          data=payload if ok else None)
    if action == "disk":
        ok, payload = _cpanel_call(ctx, "Quota", "get_quota_info")
        return ToolResult(ok, json.dumps(payload, ensure_ascii=False, indent=2)[:3000] if ok else str(payload)[:600])
    if action == "databases":
        ok, payload = _cpanel_call(ctx, "Mysql", "list_databases")
        return ToolResult(ok, json.dumps(payload, ensure_ascii=False, indent=2)[:4000] if ok else str(payload)[:600])
    ok, payload = _cpanel_call(ctx, "StatsBar", "get_stats",
                               {"display": "hostname|dedicatedip|loadavg|diskusage|bandwidthusage"})
    if not ok:
        return ToolResult(False, str(payload)[:600])
    stats = (payload.get("data") if isinstance(payload, dict) else None) or []
    lines = [f"{s.get('name', s.get('id', ''))}: {s.get('value')}" for s in stats] if isinstance(stats, list) else []
    return ToolResult(True, "\n".join(lines) or json.dumps(payload, ensure_ascii=False)[:2000], data=payload)


registry.add(
    id="cpanel.query", name="cPanel", category="Hébergement",
    description="Interroge cPanel : summary (statistiques), emails, domains, disk, databases.",
    handler=_cpanel, connector_type="cpanel", risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "connector_id": {"type": "string"},
        "action": {"type": "string", "enum": ["summary", "emails", "domains", "disk", "databases"]}},
        "required": ["action"]},
)


def _whm(ctx: ToolContext) -> ToolResult:
    func = str(ctx.arguments.get("function") or "loadavg").strip()
    allowed = {"loadavg", "systemloadavg", "listaccts", "version", "gethostname",
               "servicestatus", "showbw", "get_disk_usage"}
    if func not in allowed:
        return ToolResult(False, f"Fonction WHM non autorisée. Disponibles: {', '.join(sorted(allowed))}")
    host = str(ctx.config.get("host") or "").rstrip("/")
    user = str(ctx.config.get("username") or "root")
    token = ctx.secret("token")
    if not (host and token):
        return ToolResult(False, "Connecteur WHM incomplet.")
    ok, payload = http_json(f"{host}/json-api/{func}?api.version=1",
                            headers={"Authorization": f"whm {user}:{token}"},
                            verify_ssl=bool(ctx.config.get("verify_ssl", True)), timeout=25)
    return ToolResult(ok, json.dumps(payload, ensure_ascii=False, indent=2)[:6000] if ok else str(payload)[:600],
                      data=payload if ok else None)


registry.add(
    id="whm.query", name="WHM", category="Hébergement",
    description="Appelle une fonction WHM en lecture (loadavg, listaccts, servicestatus, version…).",
    handler=_whm, connector_type="whm", risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "connector_id": {"type": "string"}, "function": {"type": "string"}}, "required": ["function"]},
)


# --- FTP --------------------------------------------------------------------
def _ftp(ctx: ToolContext) -> ToolResult:
    import ftplib

    action = str(ctx.arguments.get("action") or "list")
    path = str(ctx.arguments.get("path") or ctx.config.get("remote_path") or "/")
    host = str(ctx.config.get("host") or "")
    port = int(ctx.config.get("port") or 21)
    user = str(ctx.config.get("username") or "")
    password = ctx.secret("password")
    try:
        client = ftplib.FTP_TLS() if ctx.config.get("tls", True) else ftplib.FTP()
        client.connect(host, port, timeout=20)
        client.login(user, password)
        if isinstance(client, ftplib.FTP_TLS):
            client.prot_p()
        if action == "list":
            entries: list[str] = []
            client.cwd(path)
            client.retrlines("LIST", entries.append)
            client.quit()
            return ToolResult(True, "\n".join(entries[:200]) or "Dossier vide.", data={"entries": entries[:200]})
        if action == "upload":
            local = str(ctx.arguments.get("local_path") or "")
            from pathlib import Path

            src = Path(local).expanduser()
            if not src.is_file():
                client.quit()
                return ToolResult(False, f"Fichier local introuvable: {src}")
            with src.open("rb") as fh:
                client.storbinary(f"STOR {path.rstrip('/')}/{src.name}", fh)
            client.quit()
            return ToolResult(True, f"{src.name} envoyé dans {path}", risk=SAFE_WRITE)
        client.quit()
        return ToolResult(False, "Action FTP inconnue.")
    except Exception as exc:
        return ToolResult(False, f"FTP: {exc}")


registry.add(
    id="ftp.op", name="FTP", category="Infrastructure",
    description="Liste un dossier distant ou envoie un fichier via FTP/FTPS.",
    handler=_ftp, connector_type="ftp", risk=READ_ONLY,
    risk_resolver=lambda a: SAFE_WRITE if a.get("action") == "upload" else READ_ONLY,
    input_schema={"type": "object", "properties": {
        "connector_id": {"type": "string"},
        "action": {"type": "string", "enum": ["list", "upload"]},
        "path": {"type": "string"}, "local_path": {"type": "string"}}, "required": ["action"]},
)


# --- Bases de données -------------------------------------------------------
def _sql_risk(args: dict[str, Any]) -> str:
    return classify_command(str(args.get("query", "")))


def _db_query(ctx: ToolContext) -> ToolResult:
    query = str(ctx.arguments.get("query") or "").strip()
    if not query:
        return ToolResult(False, "Requête SQL manquante.")
    cfg = ctx.config
    ctype = (ctx.connector or {}).get("type", "mysql")
    database = str(ctx.arguments.get("database") or cfg.get("database") or "")
    password = ctx.secret("password")
    via_ssh = str(cfg.get("via_ssh") or "").strip()

    if ctype == "mysql":
        # MYSQL_PWD et non `-p<motdepasse>` : sur la branche SSH, la commande est
        # exécutée telle quelle sur le serveur distant, où tout compte peut lire
        # les lignes de commande des autres via `ps`. Un mot de passe placé là
        # fuite vers l'ensemble des utilisateurs d'un hébergement mutualisé, le
        # temps de la requête. La branche locale utilisait déjà l'environnement,
        # et psql fait de même avec PGPASSWORD juste en dessous.
        inner = (f"MYSQL_PWD={shell_quote(password)} "
                 f"mysql -h {shell_quote(str(cfg.get('host', '127.0.0.1')))} "
                 f"-P {int(cfg.get('port') or 3306)} -u {shell_quote(str(cfg.get('username', '')))} "
                 f"{shell_quote(database) if database else ''} -e {shell_quote(query)} 2>&1")
    else:
        inner = (f"PGPASSWORD={shell_quote(password)} psql -h {shell_quote(str(cfg.get('host', '127.0.0.1')))} "
                 f"-p {int(cfg.get('port') or 5432)} -U {shell_quote(str(cfg.get('username', '')))} "
                 f"-d {shell_quote(database or 'postgres')} -c {shell_quote(query)} 2>&1")

    if via_ssh:
        ssh_conn = ctx.core.connectors.find(via_ssh, "ssh")
        if not ssh_conn:
            return ToolResult(False, f"Connecteur SSH « {via_ssh} » introuvable pour ce tunnel.")
        ssh_secrets = {f: ctx.core.vault.get(ssh_conn["id"], f, "") for f in ("password", "private_key", "passphrase")}
        ok, out = ssh_exec(ssh_conn["config"], ssh_secrets, inner, timeout=120)
    else:
        try:
            env = os.environ.copy()
            if ctype == "mysql":
                argv = ["mysql", "-h", str(cfg.get("host", "127.0.0.1")), "-P", str(int(cfg.get("port") or 3306)),
                        "-u", str(cfg.get("username", ""))]
                if database:
                    argv.append(database)
                argv += ["-e", query]
                env["MYSQL_PWD"] = password
            else:
                argv = ["psql", "-h", str(cfg.get("host", "127.0.0.1")), "-p", str(int(cfg.get("port") or 5432)),
                        "-U", str(cfg.get("username", "")), "-d", database or "postgres", "-c", query]
                env["PGPASSWORD"] = password
            proc = subprocess.run(argv, shell=False, env=env, capture_output=True, text=True, timeout=120)
            out = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
            ok = proc.returncode == 0
        except FileNotFoundError:
            return ToolResult(False, "Le client SQL n'est pas installé localement.")
        except Exception as exc:
            return ToolResult(False, f"SQL: {exc}")
    return ToolResult(ok, ctx.core.vault.scrub(out)[:20000] or "Requête exécutée.")


registry.add(
    id="db.query", name="Requête SQL", category="Base de données",
    description="Exécute une requête SQL via le client mysql/psql (localement ou à travers un connecteur SSH).",
    handler=_db_query, connector_type="mysql", risk=READ_ONLY, risk_resolver=_sql_risk,
    permissions=("read",),
    dangerous_hint="Cette requête modifie ou supprime des données.",
    input_schema={"type": "object", "properties": {
        "connector_id": {"type": "string"}, "query": {"type": "string"},
        "database": {"type": "string"}}, "required": ["query"]},
)


# --- Docker -----------------------------------------------------------------
def _docker(ctx: ToolContext) -> ToolResult:
    action = str(ctx.arguments.get("action") or "ps")
    container = str(ctx.arguments.get("container") or "").strip()
    mapping = {
        "ps": "docker ps --format '{{.Names}}\t{{.Status}}\t{{.Image}}'",
        "logs": f"docker logs --tail 100 {shell_quote(container)}",
        "restart": f"docker restart {shell_quote(container)}",
        "stop": f"docker stop {shell_quote(container)}",
        "start": f"docker start {shell_quote(container)}",
        "inspect": f"docker inspect {shell_quote(container)} --format '{{{{json .State}}}}'",
    }
    cmd = mapping.get(action)
    if not cmd:
        return ToolResult(False, "Action Docker inconnue.")
    if action != "ps" and not container:
        return ToolResult(False, "Nom du conteneur manquant.")
    cfg = ctx.config
    if str(cfg.get("mode") or "local") == "ssh":
        ssh_conn = ctx.core.connectors.find(str(cfg.get("via_ssh") or ""), "ssh")
        if not ssh_conn:
            return ToolResult(False, "Connecteur SSH associé introuvable.")
        secrets = {f: ctx.core.vault.get(ssh_conn["id"], f, "") for f in ("password", "private_key", "passphrase")}
        ok, out = ssh_exec(ssh_conn["config"], secrets, cmd, timeout=120)
    else:
        try:
            argv = {
                "ps": ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}"],
                "logs": ["docker", "logs", "--tail", "100", container],
                "inspect": ["docker", "inspect", container, "--format", "{{json .State}}"],
            }.get(action, ["docker", action, container])
            proc = subprocess.run(argv, shell=False, capture_output=True, text=True, timeout=120)
            out = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
            ok = proc.returncode == 0
        except Exception as exc:
            return ToolResult(False, f"Docker: {exc}")
    return ToolResult(ok, out[:15000] or "Exécuté.")


registry.add(
    id="docker.control", name="Docker", category="Infrastructure",
    description="Liste, inspecte, redémarre ou consulte les logs de conteneurs Docker.",
    handler=_docker, connector_type="docker", connector_optional=True, risk=READ_ONLY,
    risk_resolver=lambda a: SENSITIVE if a.get("action") in {"restart", "stop", "start"} else READ_ONLY,
    permissions=("execute",),
    dangerous_hint="Le conteneur va être arrêté ou redémarré.",
    input_schema={"type": "object", "properties": {
        "connector_id": {"type": "string"},
        "action": {"type": "string", "enum": ["ps", "logs", "restart", "stop", "start", "inspect"]},
        "container": {"type": "string"}}, "required": ["action"]},
)
