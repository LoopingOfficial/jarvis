"""Outils machine locale : système, fichiers, terminal, applications."""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from ..permissions import DESTRUCTIVE, READ_ONLY, SAFE_WRITE, SENSITIVE, classify_command
from .base import ToolContext, ToolResult, registry


def _roots(ctx: ToolContext) -> list[Path]:
    raw = ctx.core.settings.get("security", "filesystem_roots", ["~"]) or ["~"]
    out = []
    for r in raw:
        try:
            out.append(Path(str(r)).expanduser().resolve())
        except Exception:
            continue
    return out or [Path.home()]


def _check_path(ctx: ToolContext, path: str) -> tuple[Path | None, str]:
    if not path:
        return None, "Chemin manquant."
    p = Path(str(path)).expanduser()
    if not p.is_absolute():
        # Sans projet sélectionné, un chemin relatif se résoudrait contre le
        # répertoire du processus JARVIS : une écriture atterrissait dans le
        # code source du moteur. On refuse et on dit quoi faire.
        return None, (f"Chemin relatif « {path} » sans projet sélectionné : "
                      "appelle project.select d'abord, ou donne un chemin absolu.")
    try:
        resolved = p.resolve()
    except Exception as exc:
        return None, f"Chemin invalide: {exc}"
    for root in _roots(ctx):
        if resolved == root or root in resolved.parents:
            return resolved, ""
    return None, f"Accès refusé : {resolved} est hors des dossiers autorisés (Settings → Security)."


def _resolve_arg_path(ctx: ToolContext, raw: str) -> str:
    """Résout un chemin relatif dans le projet sélectionné (project.select).

    Sans projet sélectionné, le chemin est renvoyé tel quel. C'est ce qui
    permet de « travailler dans le projet » sans chemins absolus : quand VELKO
    sélectionne Bot Discord, `fs.read "package.json"` lit
    « /Users/jerome/Desktop/Bot Discord/package.json ».
    """
    if not raw:
        return raw
    p = Path(str(raw)).expanduser()
    if p.is_absolute():
        return str(p)
    try:
        proj = getattr(ctx.core, "projects", None)
    except Exception:
        proj = None
    # Dossier de travail RÉEL de la tâche : le dernier répertoire que le moteur
    # a effectivement utilisé (cwd d'une commande, dossier d'un fichier lu ou
    # écrit). C'est le comportement d'un shell, pas une supposition : sans lui,
    # un « fs.read calc.py » juste après un « terminal.run cwd=/…/projet »
    # échouait alors que l'intention était sans ambiguïté.
    if proj is None or proj.current is None:
        cwd = str(getattr(ctx.core, "active_task_context", {}).get("working_dir") or "")
        if cwd:
            return str(Path(cwd) / p)
    if proj is not None and proj.current is not None:
        rel = p
        # Si le modèle répète le nom du dossier projet (« Bot Discord/x », déjà
        # sous le projet sélectionné), on retire le segment redondant.
        parts = rel.parts
        if parts and parts[0].casefold() in {proj.current.name.casefold(),
                                            proj.current.display_name.casefold()}:
            rel = Path(*parts[1:])
        return str(Path(proj.current.path) / rel)
    return raw


# ---------------------------------------------------------------------------
def _system_info(ctx: ToolContext) -> ToolResult:
    m = ctx.core.monitor.snapshot()
    lines = [
        f"Système : {platform.platform()}",
        f"CPU : {m['cpu']['percent']}% ({m['cpu']['cores']} cœurs, charge {m['cpu']['load1']})",
    ]
    if m["memory"]["total_gb"]:
        lines.append(f"Mémoire : {m['memory']['used_gb']} / {m['memory']['total_gb']} Go ({m['memory']['percent']}%)")
    if m["disk"]["total_gb"]:
        lines.append(f"Disque : {m['disk']['used_gb']} / {m['disk']['total_gb']} Go ({m['disk']['percent']}%)")
    lines.append(f"Réseau : {m['network']['status']}")
    lines.append(f"Uptime JARVIS : {int(m['uptime_s'] // 60)} min")
    return ToolResult(True, "\n".join(lines), data=m)


registry.add(
    id="system.info", name="État du système", category="Système",
    description="Retourne CPU, mémoire, disque, réseau et uptime réels de la machine.",
    handler=_system_info, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {}, "required": []},
)


# ---------------------------------------------------------------------------
def _emit_terminal(ctx: ToolContext, event_type: str, payload: dict, *, cache: bool = True) -> None:
    """Diffuse un fait réel du terminal. Le contenu n'est jamais fabriqué."""
    try:
        ctx.core.events.emit(event_type, {"task_id": ctx.task_id, "ts": time.time(), **payload},
                             cache=cache)
    except Exception:
        pass


def _shell(ctx: ToolContext) -> ToolResult:
    if not ctx.core.settings.get("security", "allowed_shell", True):
        return ToolResult(False, "L'exécution de commandes locales est désactivée dans les réglages.")
    command = str(ctx.arguments.get("command") or "").strip()
    if not command:
        return ToolResult(False, "Commande manquante.")
    cwd = str(ctx.arguments.get("cwd") or "").strip()
    workdir = None
    if cwd:
        workdir, err = _check_path(ctx, _resolve_arg_path(ctx, cwd))
        if err:
            return ToolResult(False, err)
    workdir_s = str(workdir) if workdir else ""
    if workdir:
        _remember_dir(ctx, workdir)
    timeout = int(ctx.arguments.get("timeout") or ctx.core.settings.get("security", "shell_timeout_s", 120))
    _emit_terminal(ctx, "terminal.command", {"command": command, "cwd": workdir_s,
                                             "timestamp": time.time()}, cache=False)
    out_lines: list[str] = []
    err_lines: list[str] = []
    started = time.time()

    def _pump(pipe, stream: str) -> None:
        try:
            for line in iter(pipe.readline, ""):
                if stream == "stdout":
                    out_lines.append(line)
                else:
                    err_lines.append(line)
                _emit_terminal(ctx, "terminal.output",
                               {"command": command, "cwd": workdir_s, "timestamp": time.time(),
                                "stream": stream, "text": line}, cache=False)
        except Exception:
            pass
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    try:
        proc = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace",
                                bufsize=1, cwd=str(workdir) if workdir else None)
    except Exception as exc:
        _emit_terminal(ctx, "terminal.failed", {"command": command, "cwd": workdir_s,
                                                "error": str(exc)}, cache=False)
        return ToolResult(False, f"Terminal: {exc}")
    readers = [threading.Thread(target=_pump, args=(proc.stdout, "stdout"), daemon=True),
               threading.Thread(target=_pump, args=(proc.stderr, "stderr"), daemon=True)]
    for r in readers:
        r.start()
    try:
        proc.wait(timeout=min(timeout, 900))
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass
        _emit_terminal(ctx, "terminal.completed",
                       {"command": command, "cwd": workdir_s, "exit_code": -1,
                        "timed_out": True, "duration_ms": int((time.time() - started) * 1000),
                        "stdout": "".join(out_lines), "stderr": "".join(err_lines)}, cache=False)
        return ToolResult(False, f"La commande a dépassé {timeout}s.")
    except Exception as exc:
        try:
            proc.kill()
        except Exception:
            pass
        return ToolResult(False, f"Terminal: {exc}")
    for r in readers:
        r.join(timeout=2)
    code = proc.returncode
    out_text = "".join(out_lines)
    err_text = "".join(err_lines)
    combined = (out_text + ("\n" + err_text if err_text else "")).strip()
    ok = code == 0
    duration_ms = int((time.time() - started) * 1000)
    _emit_terminal(ctx, "terminal.completed",
                   {"command": command, "cwd": workdir_s, "exit_code": code,
                    "timed_out": False, "duration_ms": duration_ms,
                    "stdout": out_text, "stderr": err_text}, cache=False)
    return ToolResult(ok, combined[:20000] or (f"Code {code}." if not ok else "Exécuté sans sortie."),
                      data={"exit_code": code, "duration_ms": duration_ms,
                            "stdout": out_text, "stderr": err_text})


registry.add(
    id="terminal.run", name="Terminal local", category="Système",
    description="Exécute une commande shell locale (cmd.exe sous Windows). Utiliser pour git, builds, outils CLI.",
    handler=_shell, risk=SAFE_WRITE,
    risk_resolver=lambda a: classify_command(str(a.get("command", ""))),
    permissions=("execute",),
    dangerous_hint="La commande sera exécutée directement sur ta machine.",
    input_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Commande shell complète"},
            "cwd": {"type": "string", "description": "Répertoire de travail (optionnel)"},
            "timeout": {"type": "integer", "description": "Délai maximum en secondes"},
        },
        "required": ["command"],
    },
)


# ---------------------------------------------------------------------------
def _remember_dir(ctx: ToolContext, path: Path) -> None:
    """Retient le répertoire RÉELLEMENT utilisé par la tâche en cours."""
    try:
        ctx.core.active_task_context["working_dir"] = str(path if path.is_dir() else path.parent)
    except Exception:
        pass


def _emit_file(ctx: ToolContext, event_type: str, payload: dict) -> None:
    """Diffuse un fait fichier réel — émis uniquement après l'opération réussie."""
    try:
        ctx.core.events.emit(event_type, {"task_id": ctx.task_id, "ts": time.time(), **payload})
    except Exception:
        pass


def _fs_read(ctx: ToolContext) -> ToolResult:
    path, err = _check_path(ctx, _resolve_arg_path(ctx, str(ctx.arguments.get("path") or "")))
    if err:
        return ToolResult(False, err)
    if not path.is_file():
        return ToolResult(False, f"Fichier introuvable: {path}")
    if path.stat().st_size > 2_000_000:
        return ToolResult(False, "Fichier trop volumineux (> 2 Mo). Utilise grep ou head.")
    try:
        text = path.read_bytes().decode("utf-8")
    except Exception as exc:
        return ToolResult(False, f"Lecture impossible: {exc}")
    _remember_dir(ctx, path)
    _emit_file(ctx, "file.opened", {"path": str(path)})
    _emit_file(ctx, "code.file.active", {"path": str(path), "source": "fs"})
    max_lines = int(ctx.arguments.get("max_lines") or 400)
    lines = text.splitlines()
    truncated = len(lines) > max_lines
    body = "\n".join(lines[:max_lines])
    if truncated:
        body += f"\n… ({len(lines) - max_lines} lignes supplémentaires)"
    return ToolResult(True, body, data={"path": str(path), "lines": len(lines),
                                      "content": text, "source": "fs", "truncated": truncated})


registry.add(
    id="fs.read", name="Lire un fichier", category="Fichiers",
    description="Lit un fichier texte sur la machine locale.",
    handler=_fs_read, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "path": {"type": "string"}, "max_lines": {"type": "integer"}}, "required": ["path"]},
)


def _fs_write(ctx: ToolContext) -> ToolResult:
    path, err = _check_path(ctx, _resolve_arg_path(ctx, str(ctx.arguments.get("path") or "")))
    if err:
        return ToolResult(False, err)
    content = ctx.arguments.get("content")
    if content is None:
        return ToolResult(False, "Contenu manquant.")
    mode = str(ctx.arguments.get("mode") or "write")
    existed = path.exists()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and mode == "write":
            backup = path.with_suffix(path.suffix + f".jarvis-{time.strftime('%Y%m%d%H%M%S')}.bak")
            shutil.copy2(path, backup)
        with path.open("a" if mode == "append" else "w", encoding="utf-8") as fh:
            fh.write(str(content))
        _remember_dir(ctx, path)
        _emit_file(ctx, "file.changed" if existed else "file.created",
                   {"path": str(path), "mode": mode})
        if str(path).endswith((".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".md",
                               ".php", ".html", ".css", ".sh", ".yml", ".yaml", ".go", ".rs")):
            _emit_file(ctx, "code.patch.applied", {"path": str(path), "mode": mode,
                                                   "chars": len(str(content))})
        return ToolResult(True, f"Écrit dans {path} ({len(str(content))} caractères).", data={"path": str(path)})
    except Exception as exc:
        return ToolResult(False, f"Écriture impossible: {exc}")


registry.add(
    id="fs.write", name="Écrire un fichier", category="Fichiers",
    description="Écrit ou complète un fichier local. Une sauvegarde .bak est créée avant écrasement.",
    handler=_fs_write, risk=SAFE_WRITE, permissions=("write",),
    input_schema={"type": "object", "properties": {
        "path": {"type": "string"}, "content": {"type": "string"},
        "mode": {"type": "string", "enum": ["write", "append"]}}, "required": ["path", "content"]},
)


def _fs_list(ctx: ToolContext) -> ToolResult:
    raw = str(ctx.arguments.get("path") or "~")
    path, err = _check_path(ctx, _resolve_arg_path(ctx, raw))
    if err:
        return ToolResult(False, err)
    if not path.is_dir():
        return ToolResult(False, f"Dossier introuvable: {path}")
    _remember_dir(ctx, path)
    entries = []
    for item in sorted(path.iterdir())[:300]:
        try:
            size = item.stat().st_size if item.is_file() else 0
        except OSError:
            size = 0
        entries.append({"name": item.name, "type": "dir" if item.is_dir() else "file", "size": size})
    listing = "\n".join(f"{'📁' if e['type'] == 'dir' else '📄'} {e['name']}" for e in entries)
    return ToolResult(True, f"{path} ({len(entries)} éléments)\n{listing}", data={"entries": entries})


registry.add(
    id="fs.list", name="Lister un dossier", category="Fichiers",
    description="Liste le contenu d'un dossier local.",
    handler=_fs_list, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
)


def _fs_search(ctx: ToolContext) -> ToolResult:
    raw = str(ctx.arguments.get("path") or "~")
    path, err = _check_path(ctx, _resolve_arg_path(ctx, raw))
    if err:
        return ToolResult(False, err)
    pattern = str(ctx.arguments.get("pattern") or "").strip()
    if not pattern:
        return ToolResult(False, "Motif de recherche manquant.")
    kind = str(ctx.arguments.get("kind") or "content")
    results: list[str] = []
    if kind == "name":
        for item in list(path.rglob(pattern))[:200]:
            results.append(str(item))
    else:
        rx = re.compile(pattern, re.IGNORECASE)
        skip = {".git", "node_modules", ".venv", "__pycache__", "dist", "build"}
        count = 0
        for item in path.rglob("*"):
            if count >= 200:
                break
            if not item.is_file() or any(p in skip for p in item.parts):
                continue
            try:
                if item.stat().st_size > 1_000_000:
                    continue
                for n, line in enumerate(item.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if rx.search(line):
                        results.append(f"{item}:{n}: {line.strip()[:160]}")
                        count += 1
                        if count >= 200:
                            break
            except Exception:
                continue
    if not results:
        return ToolResult(True, "Aucun résultat.", data={"matches": []})
    return ToolResult(True, "\n".join(results[:200]), data={"matches": results[:200]})


registry.add(
    id="fs.search", name="Rechercher dans les fichiers", category="Fichiers",
    description="Recherche un motif dans le contenu (kind=content) ou les noms (kind=name) sous un dossier.",
    handler=_fs_search, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "path": {"type": "string"}, "pattern": {"type": "string"},
        "kind": {"type": "string", "enum": ["content", "name"]}}, "required": ["path", "pattern"]},
)


def _fs_delete(ctx: ToolContext) -> ToolResult:
    path, err = _check_path(ctx, _resolve_arg_path(ctx, str(ctx.arguments.get("path") or "")))
    if err:
        return ToolResult(False, err)
    if not path.exists():
        return ToolResult(False, "Ce chemin n'existe pas.")
    trash = Path.home() / ".jarvis-trash" / time.strftime("%Y%m%d-%H%M%S")
    try:
        trash.mkdir(parents=True, exist_ok=True)
        target = trash / path.name
        shutil.move(str(path), str(target))
        _emit_file(ctx, "file.deleted", {"path": str(path), "trash_path": str(target)})
        return ToolResult(True, f"Déplacé vers la corbeille JARVIS : {target}", risk=DESTRUCTIVE,
                          data={"trash_path": str(target)})
    except Exception as exc:
        return ToolResult(False, f"Suppression impossible: {exc}")


registry.add(
    id="fs.delete", name="Supprimer un fichier", category="Fichiers",
    description="Déplace un fichier ou dossier vers ~/.jarvis-trash (réversible).",
    handler=_fs_delete, risk=DESTRUCTIVE, permissions=("destructive",),
    dangerous_hint="Le fichier sera déplacé hors de son emplacement actuel.",
    input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
)


# ---------------------------------------------------------------------------
APP_ALIASES = {
    "cursor": "Cursor", "terminal": "Terminal", "finder": "Finder", "spotify": "Spotify",
    "chrome": "Google Chrome", "google chrome": "Google Chrome", "discord": "Discord",
    "notes": "Notes", "messages": "Messages", "mail": "Mail", "safari": "Safari",
    "vscode": "Visual Studio Code", "code": "Visual Studio Code", "docker": "Docker",
    "réglages": "System Settings", "reglages": "System Settings", "settings": "System Settings",
}


def _open_app(ctx: ToolContext) -> ToolResult:
    name = str(ctx.arguments.get("name") or "").strip()
    if not name:
        return ToolResult(False, "Nom d'application manquant.")
    if platform.system() == "Windows":
        from ..windows import open_app
        try:
            open_app(name)
            return ToolResult(True, f"{name} est ouvert.", risk=SAFE_WRITE)
        except Exception as exc:
            return ToolResult(False, f"Ouverture impossible : {exc}")
    app = APP_ALIASES.get(name.casefold(), name)
    if platform.system() != "Darwin":
        return ToolResult(False, "L'ouverture d'applications est prévue pour macOS.")
    try:
        proc = subprocess.run(["open", "-a", app], capture_output=True, text=True, timeout=15)
        if proc.returncode == 0:
            return ToolResult(True, f"{app} est ouvert.", risk=SAFE_WRITE)
        return ToolResult(False, f"Application « {app} » introuvable.")
    except Exception as exc:
        return ToolResult(False, f"Ouverture impossible: {exc}")


registry.add(
    id="app.open", name="Ouvrir une application", category="Système",
    description="Ouvre une application Windows ou macOS (Chrome, Cursor, Terminal, Explorateur…).",
    handler=_open_app, risk=SAFE_WRITE, permissions=("execute",),
    input_schema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
)


def _open_url(ctx: ToolContext) -> ToolResult:
    url = str(ctx.arguments.get("url") or "").strip()
    if not re.match(r"^https?://", url, re.I):
        return ToolResult(False, "URL invalide.")
    try:
        if platform.system() == "Darwin":
            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            import webbrowser

            webbrowser.open(url)
        return ToolResult(True, f"Ouvert : {url}", risk=SAFE_WRITE, data={"url": url})
    except Exception as exc:
        return ToolResult(False, f"Ouverture impossible: {exc}")


registry.add(
    id="browser.open", name="Ouvrir une page web", category="Navigateur",
    description="Ouvre une URL dans le navigateur par défaut de la machine.",
    handler=_open_url, risk=SAFE_WRITE, permissions=("execute",),
    input_schema={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
)


def _notify(ctx: ToolContext) -> ToolResult:
    title = str(ctx.arguments.get("title") or "JARVIS")
    message = str(ctx.arguments.get("message") or "")
    level = str(ctx.arguments.get("level") or "info")
    ctx.core.events.feed(title, level=level if level in {"info", "warn", "error", "tip"} else "info",
                         kind="notification", detail=message, source="jarvis")
    if platform.system() == "Windows" and ctx.core.settings.get("notifications", "desktop", True):
        try:
            from ..windows import notify
            notify(title, message)
        except Exception:
            return ToolResult(True, "Notification ajoutée au fil Jarvis ; notification Windows indisponible.", risk=READ_ONLY)
    if platform.system() == "Darwin" and ctx.core.settings.get("notifications", "desktop", True):
        try:
            safe_t = title.replace('"', "'")
            safe_m = message.replace('"', "'")
            subprocess.Popen(
                ["osascript", "-e", f'display notification "{safe_m}" with title "{safe_t}"'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
    return ToolResult(True, "Notification envoyée.", risk=READ_ONLY)


registry.add(
    id="notify.send", name="Notifier", category="Système",
    description="Affiche une notification du système et l'ajoute au Live Intelligence Feed.",
    handler=_notify, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "title": {"type": "string"}, "message": {"type": "string"},
        "level": {"type": "string", "enum": ["info", "warn", "error", "tip"]}}, "required": ["title"]},
)


def _git(ctx: ToolContext) -> ToolResult:
    args = str(ctx.arguments.get("args") or "status --short").strip()
    repo = str(ctx.arguments.get("repo") or ctx.core.settings.get("general", "default_project", "")).strip()
    if not repo:
        return ToolResult(False, "Aucun dépôt indiqué (repo) et aucun projet par défaut configuré.")
    path, err = _check_path(ctx, repo)
    if err:
        return ToolResult(False, err)
    if not (path / ".git").exists():
        return ToolResult(False, f"{path} n'est pas un dépôt git.")
    try:
        proc = subprocess.run(f"git {args}", shell=True, capture_output=True, text=True, timeout=180, cwd=str(path))
        out = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
        return ToolResult(proc.returncode == 0, out[:15000] or "Aucune sortie.",
                          data={"exit_code": proc.returncode})
    except Exception as exc:
        return ToolResult(False, f"git: {exc}")


registry.add(
    id="git.run", name="Git", category="Développement",
    description="Exécute une commande git dans un dépôt local (status, log, diff, pull, commit…).",
    handler=_git, risk=SAFE_WRITE,
    risk_resolver=lambda a: classify_command("git " + str(a.get("args", ""))),
    permissions=("execute",),
    input_schema={"type": "object", "properties": {
        "args": {"type": "string", "description": "Arguments git, ex: 'log --oneline -10'"},
        "repo": {"type": "string", "description": "Chemin du dépôt (défaut: projet par défaut)"}},
        "required": ["args"]},
)


def _opencode(ctx: ToolContext) -> ToolResult:
    exe = shutil.which(os.getenv("JARVIS_OPENCODE_COMMAND", "opencode")) or shutil.which("opencode")
    if not exe:
        return ToolResult(False, "OpenCode n'est pas installé ou absent du PATH.")
    task = str(ctx.arguments.get("task") or "").strip()
    if not task:
        return ToolResult(False, "Décris la tâche de développement.")
    project = str(ctx.arguments.get("project") or ctx.core.settings.get("general", "default_project", "")).strip()
    if not project:
        return ToolResult(False, "Aucun dossier projet défini (Settings → General → projet par défaut).")
    path, err = _check_path(ctx, project)
    if err:
        return ToolResult(False, err)
    try:
        proc = subprocess.run([exe, "run", "--dir", str(path), task],
                              capture_output=True, text=True, timeout=3600)
        out = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
        return ToolResult(proc.returncode == 0, out[:20000] or "Tâche terminée sans sortie.")
    except subprocess.TimeoutExpired:
        return ToolResult(False, "OpenCode a dépassé une heure.")
    except Exception as exc:
        return ToolResult(False, f"OpenCode: {exc}")


registry.add(
    id="code.opencode", name="OpenCode", category="Développement",
    description="Délègue une tâche de développement à OpenCode dans un dossier projet.",
    handler=_opencode, risk=SENSITIVE, permissions=("execute",),
    dangerous_hint="OpenCode va modifier des fichiers du projet.",
    input_schema={"type": "object", "properties": {
        "task": {"type": "string"}, "project": {"type": "string"}}, "required": ["task"]},
)
