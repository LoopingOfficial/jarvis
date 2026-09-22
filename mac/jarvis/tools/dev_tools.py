"""Outils de développement quotidien : projets, processus longue durée, tests, git.

Tout ce qui est retourné vient de faits réels : sortie de processus, codes de
sortie, diffs git. Rien n'est simulé. Les événements `project.*`, `git.*`,
`test.*`, `process.*` et `code.patch.applied` sont émis à partir des résultats
réels, pas du discours du modèle.
"""
from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from ..permissions import READ_ONLY, SAFE_WRITE, SENSITIVE, classify_command
from .base import ToolContext, ToolResult, registry


def _norms(text: str) -> str:
    import unicodedata
    text = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in text if not unicodedata.combining(c)).casefold().strip()


# ---------------------------------------------------------------------------
# ProjectResolver — outils projet
# ---------------------------------------------------------------------------
def _resolve_project(ctx: ToolContext, query: str) -> list:
    resolver = ctx.core.projects
    projects = resolver.resolve(query or "")
    return projects


def project_list(ctx: ToolContext) -> ToolResult:
    resolver = ctx.core.projects
    projects = resolver.all()
    current = resolver.current
    lines = [f"{p.id:28} {p.display_name:24} {p.stack:18} git={'✓' if p.git else '—'}  {p.path}"
             for p in projects]
    header = f"{len(projects)} projet(s) découvert(s)"
    if current:
        header += f" — actif : {current.display_name}"
    if not lines:
        return ToolResult(True, "Aucun projet détecté dans les dossiers autorisés.", data=[])
    try:
        ctx.core.events.emit("project.list", {"count": len(projects)})
    except Exception:
        pass
    return ToolResult(True, header + "\n" + "\n".join(lines),
                      data=[p.summary() for p in projects])


registry.add(
    id="project.list", name="Projets disponibles", category="Projets",
    description="Liste les projets réels détectés dans les dossiers autorisés (repository git ou manifeste présent).",
    handler=project_list, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {}, "required": []},
)


def project_select(ctx: ToolContext) -> ToolResult:
    query = str(ctx.arguments.get("name") or ctx.arguments.get("query") or "").strip()
    if not query:
        return ToolResult(False, "Indique le nom ou une partie du nom du projet.")
    resolver = ctx.core.projects
    # « mon bot Discord » ne nomme aucun dossier : on passe par les preuves
    # réelles (dépendance discord.js/py, commands/, point d'entrée, jeton
    # attendu) au lieu d'un rapprochement de noms qui ne peut pas aboutir.
    if resolver.looks_like_discord_bot(query):
        proj, candidates = resolver.resolve_discord_bot()
        if proj is not None:
            resolver.select(proj)
            resolver.remember_bot(proj)
            evidence = next((c for c in candidates
                             if c.project.path == proj.path), None)
            detail = ("\n- " + "\n- ".join(evidence.facts)) if evidence else ""
            return ToolResult(True,
                              f"Bot Discord identifié : {proj.display_name} ({proj.path})"
                              + (f"\nPreuves :{detail}" if detail else ""),
                              data=(evidence.summary() if evidence else proj.summary()))
        if candidates:
            try:
                ctx.core.events.emit("project.ambiguous", {
                    "query": query, "candidates": [c.summary() for c in candidates]})
            except Exception:
                pass
            lines = [f"{c.project.id} → {c.project.path} (score {c.score} : "
                     f"{'; '.join(c.facts[:3])})" for c in candidates]
            return ToolResult(False,
                              "Plusieurs projets sont réellement des bots Discord. "
                              "Lequel est le tien ?\n" + "\n".join(lines),
                              data=[c.summary() for c in candidates])
        return ToolResult(False,
                          "Aucun bot Discord trouvé dans les dossiers autorisés : "
                          "aucun projet ne déclare de bibliothèque Discord.")
    matches = _resolve_project(ctx, query)
    if not matches:
        return ToolResult(False,
                          f"Aucun projet trouvé pour « {query} ». Utilise project.list pour voir les projets détectés.")
    if len(matches) > 1:
        try:
            ctx.core.events.emit("project.ambiguous", {
                "query": query, "candidates": [p.summary() for p in matches],
            })
        except Exception:
            pass
        lines = [f"{p.id} → {p.display_name} ({p.stack}) {p.path}" for p in matches]
        return ToolResult(False,
                          f"Plusieurs projets correspondent à « {query} ». Précise lequel :\n" + "\n".join(lines),
                          data=[p.summary() for p in matches])
    proj = matches[0]
    ctx.core.projects.select(proj)
    return ToolResult(True, f"Projet sélectionné : {proj.display_name} ({proj.path})",
                      data=proj.summary())


registry.add(
    id="project.select", name="Sélectionner un projet", category="Projets",
    description="Résout et sélectionne le projet correspondant au nom donné (le projet courant est utilisé par les autres outils dev).",
    handler=project_select, risk=READ_ONLY,
    input_schema={"type": "object",
                  "properties": {"name": {"type": "string", "description": "Nom ou fragment du nom du projet"}},
                  "required": ["name"]},
)


def project_context(ctx: ToolContext) -> ToolResult:
    query = str(ctx.arguments.get("name") or ctx.arguments.get("query") or "").strip()
    proj = None
    if query:
        matches = _resolve_project(ctx, query)
        if len(matches) == 1:
            proj = matches[0]
        elif len(matches) > 1:
            lines = [f"{p.id} → {p.display_name} ({p.stack}) {p.path}" for p in matches]
            return ToolResult(False, "Plusieurs projets correspondent. Précise :\n" + "\n".join(lines),
                              data=[p.summary() for p in matches])
    if proj is None:
        proj = ctx.core.projects.current
    if proj is None:
        return ToolResult(False,
                          "Aucun projet en contexte. Utilise project.select avec un nom de projet, ou project.context name=« … ».")
    text = ctx.core.projects.context(proj)
    if ctx.core.projects.current is None or ctx.core.projects.current.id != proj.id:
        ctx.core.projects.select(proj)
    try:
        ctx.core.events.emit("project.context", {"project": proj.summary(), "text": text[:6000]})
    except Exception:
        pass
    return ToolResult(True, text, data=proj.summary())


registry.add(
    id="project.context", name="Contexte du projet", category="Projets",
    description="Construit le contexte d'un projet (structure, stack, scripts, tests connus, README). Utilise-le au début d'une tâche dev.",
    handler=project_context, risk=READ_ONLY,
    input_schema={"type": "object",
                  "properties": {"name": {"type": "string", "description": "Nom du projet (optionnel si déjà sélectionné)"}},
                  "required": []},
)


def _cycle_root(ctx: ToolContext) -> Path | None:
    query = str(ctx.arguments.get("repo") or ctx.arguments.get("project") or "").strip()
    proj = None
    source = "none"
    if query:
        matches = _resolve_project(ctx, query)
        if matches:
            proj = matches[0]
            source = "query"
    if proj is None:
        proj = ctx.core.projects.current
        if proj is not None:
            source = "current"
    if proj is None:
        last = str(ctx.core.settings.get("general", "last_project", "")).strip()
        if last:
            try:
                path = Path(last).expanduser().resolve()
                known = {p.path: p for p in ctx.core.projects.all()}
                proj = known.get(str(path))
                source = "last_project"
            except Exception:
                proj = None
    if proj is None:
        return None
    try:
        ctx.core.events.emit("dev.debug", {"root": str(proj.path), "query": query, "source": source})
    except Exception:
        pass
    return Path(proj.path)


def _slug(path: Path) -> str:
    return _norms(path.name).replace(" ", "-")


# ---------------------------------------------------------------------------
# ProcessManager — processus longue durée en arrière-plan
# ---------------------------------------------------------------------------
class ProcessManager:
    """StarStop des processus serveurs / longues tâches avec buffer de logs."""

    def __init__(self, core: Any) -> None:  # noqa: F821
        self.core = core
        self._entries: dict[str, "ProcessEntry"] = {}
        self._lock = threading.RLock()

    def start(self, command: str, cwd: str | None = None, name: str | None = None) -> dict:
        name = (name or "").strip() or Path(command.split()[0]).name or "process"
        if name in self._entries:
            raise RuntimeError(f"Un processus « {name} » est déjà suivi. Utilise process.stop d'abord.")
        env = dict(os.environ)
        proc = subprocess.Popen(
            command, shell=True, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env, start_new_session=True, bufsize=1,
        )
        entry = ProcessEntry(name=name, command=command, cwd=cwd, proc=proc)
        with self._lock:
            self._entries[name] = entry
        self._pump(entry, proc)
        try:
            self.core.events.emit("process.started", {
                "name": name, "pid": proc.pid, "command": command, "cwd": cwd or ""})
        except Exception:
            pass
        return {"name": name, "pid": proc.pid}

    def _pump(self, entry: "ProcessEntry", proc: subprocess.Popen) -> None:
        def read_loop() -> None:
            try:
                for line in proc.stdout:
                    line = line.rstrip("\n")
                    entry.buffer.append(line)
                    if len(entry.buffer) > 5000:
                        del entry.buffer[: len(entry.buffer) - 5000]
                    try:
                        self.core.events.emit("process.output",
                                              {"name": entry.name, "line": line[:4000]}, cache=False)
                    except Exception:
                        pass
                    with entry.cv:
                        entry.cv.notify_all()
            except Exception:
                pass
            finally:
                entry.exit_code = proc.wait()
                try:
                    self.core.events.emit("process.stopped", {
                        "name": entry.name, "pid": proc.pid, "exit_code": entry.exit_code})
                except Exception:
                    pass
                entry.alive = False

        entry.thread = threading.Thread(target=read_loop, daemon=True,
                                        name=f"proc-{entry.name}")
        entry.thread.start()

    def status(self, name: str | None = None) -> list[dict]:
        with self._lock:
            entries = [self._entries[n] for n in self._entries] if not name else [
                e for n, e in self._entries.items() if n == name]
        out = []
        for e in sorted(entries, key=lambda x: x.name):
            running = e.alive and e.proc.poll() is None
            out.append({"name": e.name, "pid": e.proc.pid, "command": e.command,
                        "running": running, "exit_code": e.exit_code,
                        "cwd": e.cwd or "", "lines": len(e.buffer)})
        return out

    def logs(self, name: str, tail: int = 100) -> list[str]:
        with self._lock:
            e = self._entries.get(name)
        if not e:
            raise KeyError(f"Processus inconnu : {name}")
        return list(e.buffer[-tail:])

    def stop(self, name: str, kill: bool = False) -> dict:
        with self._lock:
            e = self._entries.get(name)
        if not e:
            raise KeyError(f"Processus inconnu : {name}")
        if e.proc.poll() is None:
            try:
                os.killpg(e.proc.pid, 9 if kill else 15)  # SIGKILL / SIGTERM
            except Exception:
                pass
        e.exit_code = e.proc.wait(timeout=30)
        e.alive = False
        return {"name": name, "pid": e.proc.pid, "exit_code": e.exit_code}

    def restart(self, name: str) -> dict:
        with self._lock:
            entry = self._entries.get(name)
        if entry is None:
            raise KeyError(f"Processus inconnu : {name}")
        command, cwd = entry.command, entry.cwd
        self.stop(name)
        with self._lock:
            self._entries.pop(name, None)
        return self.start(command, cwd=cwd, name=name)



class ProcessEntry:
    def __init__(self, name: str, command: str, cwd: str | None, proc: subprocess.Popen) -> None:
        self.name = name
        self.command = command
        self.cwd = cwd
        self.proc = proc
        self.buffer: list[str] = []
        self.exit_code: int | None = None
        self.alive = True
        self.thread: threading.Thread | None = None
        self.cv = threading.Condition()


_pm: ProcessManager | None = None


def _manager(ctx: ToolContext) -> ProcessManager:
    global _pm
    if _pm is None:
        _pm = getattr(ctx.core, "process_manager", None) or ProcessManager(ctx.core)
    return _pm


def _require_dir(ctx: ToolContext) -> tuple[Path | None, str]:
    root = _cycle_root(ctx)
    if root is None:
        return None, "Aucun projet en contexte. Sélectionne d'abord un projet (project.select) ou passe l'argument cwd/project."
    return root, ""


# ---------------------------------------------------------------------------
# process.*
# ---------------------------------------------------------------------------
def process_start(ctx: ToolContext) -> ToolResult:
    command = str(ctx.arguments.get("command") or "").strip()
    if not command:
        return ToolResult(False, "Commande manquante.")
    cwd = str(ctx.arguments.get("cwd") or "").strip()
    name = str(ctx.arguments.get("name") or "").strip()
    workdir = None
    if cwd:
        from .system_tools import _check_path, _resolve_arg_path
        workdir, err = _check_path(ctx, _resolve_arg_path(ctx, cwd))
        if err:
            return ToolResult(False, err)
    try:
        info = _manager(ctx).start(command, cwd=str(workdir) if workdir else None, name=name)
        return ToolResult(True, f"Processus « {info['name']} » lancé (pid {info['pid']}).",
                          data=info, risk=SAFE_WRITE)
    except Exception as exc:
        return ToolResult(False, f"process.start : {exc}")


registry.add(
    id="process.start", name="Lancer un processus", category="Développement",
    description="Lance une commande en arrière-plan (serveur, build long…) et suit ses logs en continu. Ne bloque pas.",
    handler=process_start, risk=SAFE_WRITE, permissions=("execute",),
    risk_resolver=lambda a: classify_command("start " + str(a.get("command", ""))),
    input_schema={"type": "object", "properties": {
        "command": {"type": "string"}, "cwd": {"type": "string", "description": "Répertoire de travail (optionnel)"},
        "name": {"type": "string", "description": "Nom d'usage du processus (optionnel)"}},
        "required": ["command"]},
)


def process_status(ctx: ToolContext) -> ToolResult:
    name = str(ctx.arguments.get("name") or "").strip()
    try:
        rows = _manager(ctx).status(name or None)
        if not rows:
            return ToolResult(True, "Aucun processus suivi." if not name else f"Processus « {name} » inconnu.",
                              data=[])
        lines = [f"{r['name']:16} pid={r['pid']:<6} {'●' if r['running'] else '✗ term.',} exit={r['exit_code']}  {r['command']}"
                 for r in rows]
        return ToolResult(True, "\n".join(lines) or "Aucun processus suivi.", data=rows)
    except Exception as exc:
        return ToolResult(False, f"process.status : {exc}")


registry.add(
    id="process.status", name="État des processus", category="Développement",
    description="État (en cours / terminé, code de sortie) des processus suivis en arrière-plan.",
    handler=process_status, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {"name": {"type": "string"}}, "required": []},
)


def process_logs(ctx: ToolContext) -> ToolResult:
    name = str(ctx.arguments.get("name") or "").strip()
    tail = int(ctx.arguments.get("tail") or 200)
    if not name:
        return ToolResult(False, "Nom du processus requis.")
    try:
        lines = _manager(ctx).logs(name, tail)
    except KeyError as exc:
        return ToolResult(False, str(exc))
    except Exception as exc:
        return ToolResult(False, f"process.logs : {exc}")
    return ToolResult(True, "\n".join(lines[-tail:]) or "(processus démarré, aucun log encore)",
                      data={"name": name, "tail": tail, "lines": len(lines)})


registry.add(
    id="process.logs", name="Logs d'un processus", category="Développement",
    description="Dernières lignes du log d'un processus suivi en arrière-plan.",
    handler=process_logs, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "name": {"type": "string"}, "tail": {"type": "integer", "default": 200}}, "required": ["name"]},
)


def process_stop(ctx: ToolContext) -> ToolResult:
    name = str(ctx.arguments.get("name") or "").strip()
    kill = bool(ctx.arguments.get("force", False))
    if not name:
        return ToolResult(False, "Nom du processus requis.")
    try:
        info = _manager(ctx).stop(name, kill=kill)
        return ToolResult(True,
                          f"Processus « {name} » arrêté (exit {info['exit_code']}).",
                          data=info, risk=SENSITIVE)
    except KeyError as exc:
        return ToolResult(False, str(exc))
    except Exception as exc:
        return ToolResult(False, f"process.stop : {exc}")


registry.add(
    id="process.stop", name="Arrêter un processus", category="Développement",
    description="Arrête un processus suivi (SIGTERM, ou SIGKILL si force=true).",
    handler=process_stop, risk=SENSITIVE, permissions=("execute",),
    dangerous_hint="Va arrêter un processus en cours d'exécution.",
    input_schema={"type": "object", "properties": {
        "name": {"type": "string"}, "force": {"type": "boolean", "description": "kill -9"} },
        "required": ["name"]},
)


def process_restart(ctx: ToolContext) -> ToolResult:
    name = str(ctx.arguments.get("name") or "").strip()
    if not name:
        return ToolResult(False, "Nom du processus requis.")
    try:
        info = _manager(ctx).restart(name)
        return ToolResult(True, f"Processus « {name} » relancé (pid {info['pid']}).",
                          data=info, risk=SENSITIVE)
    except KeyError as exc:
        return ToolResult(False, str(exc))
    except Exception as exc:
        return ToolResult(False, f"process.restart : {exc}")


registry.add(
    id="process.restart", name="Redémarrer un processus", category="Développement",
    description="Arrête puis relance un processus suivi.",
    handler=process_restart, risk=SENSITIVE, permissions=("execute",),
    dangerous_hint="Va arrêter et relancer un processus.",
    input_schema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
)


# ---------------------------------------------------------------------------
# test.run — exécution réelle de tests / lint
# ---------------------------------------------------------------------------
def _emit(ctx: ToolContext, event_type: str, payload: dict, *, cache: bool = True) -> None:
    try:
        ctx.core.events.emit(event_type, {"task_id": ctx.task_id, **payload}, cache=cache)
    except Exception:
        pass


def test_run(ctx: ToolContext) -> ToolResult:
    command = str(ctx.arguments.get("command") or "").strip()
    if not command:
        return ToolResult(False, "Commande manquante.")
    root, _ = _require_dir(ctx)
    cwd = str(ctx.arguments.get("cwd") or "").strip()
    workdir = None
    if cwd:
        from .system_tools import _check_path, _resolve_arg_path
        workdir, err2 = _check_path(ctx, _resolve_arg_path(ctx, cwd))
        if err2:
            return ToolResult(False, err2)
    elif root is not None:
        workdir = root
    from dataclasses import replace
    from .system_tools import _shell
    arguments = {**ctx.arguments, "cwd": str(workdir) if workdir else ""}
    _emit(ctx, "test.started", {"command": command, "cwd": arguments["cwd"]})
    result = _shell(replace(ctx, arguments=arguments))
    _emit(ctx, "test.passed" if result.ok else "test.failed",
          {"command": command, "exit_code": (result.data or {}).get("exit_code"),
           "reason": "" if result.ok else result.output})
    return result


registry.add(
    id="test.run", name="Lancer les tests / lint", category="Développement",
    description="Exécute réellement une commande de test ou de lint dans le projet sélectionné et reporte le code de sortie réel.",
    handler=test_run, risk=SAFE_WRITE, permissions=("execute",),
    risk_resolver=lambda a: classify_command(" " + str(a.get("command", ""))),
    input_schema={"type": "object", "properties": {
        "command": {"type": "string", "description": "Commande complète, ex: npm test"}, 
        "cwd": {"type": "string", "description": "Répertoire de travail (défaut: projet sélectionné)"}},
        "required": ["command"]},
)


# ---------------------------------------------------------------------------
# git.status / git.diff / git.log — wrappers factuels
# ---------------------------------------------------------------------------
def _git_run(command: str, cwd: str, timeout: int = 120) -> tuple[int, str]:
    try:
        proc = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        out = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
        return proc.returncode, out
    except subprocess.TimeoutExpired:
        return -1, "git : délai dépassé"
    except Exception as exc:
        return -1, f"git : {exc}"


def git_status(ctx: ToolContext) -> ToolResult:
    root, err = _require_dir(ctx)
    if err:
        return ToolResult(False, err + "\nSinon passe l'argument repo ou cwd.")
    if not (root / ".git").exists():
        return ToolResult(False, f"{root} n'est pas un dépôt git (aucune modification à comparer).")
    code, raw = _git_run("git status --short --branch", str(root))
    if code != 0:
        return ToolResult(False, raw)
    files = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("##"):
            continue
        parts = line.split(None, 1)
        files.append({"status": parts[0] if parts else "??", "path": parts[1] if len(parts) > 1 else ""})
    branch = next((line[3:] for line in raw.splitlines() if line.startswith("##")), "")
    clean = not files
    _emit(ctx, "git.status", {"repo": str(root), "branch": branch, "clean": clean,
                              "files": files, "raw": raw[:6000]})
    if clean:
        return ToolResult(True, f"Git ({branch} à {root}) : working tree propre.",
                          data={"branch": branch, "clean": True, "files": []})
    return ToolResult(True,
                      f"Git ({branch} à {root}) : {len(files)} fichier(s) modifié(s) :\n" + raw,
                      data={"branch": branch, "clean": False, "files": files})


registry.add(
    id="git.status", name="État Git du projet", category="Développement",
    description="Statut git réel du projet sélectionné (fichiers modifiés, non suivis, branche).",
    handler=git_status, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "repo": {"type": "string", "description": "Nom du projet ou chemin (défaut: projet sélectionné)"}},
        "required": []},
)


def git_diff(ctx: ToolContext) -> ToolResult:
    root, err = _require_dir(ctx)
    if err:
        return ToolResult(False, err + "\nSinon passe l'argument repo ou cwd.")
    if not (root / ".git").exists():
        return ToolResult(False, f"{root} n'est pas un dépôt git.")
    stat = bool(ctx.arguments.get("stat", False))
    staged = bool(ctx.arguments.get("staged", False))
    args = f"diff --stat" if stat else ("diff --cached" if staged else "diff")
    code, raw = _git_run(f"git {args}", str(root), timeout=180)
    if code != 0:
        return ToolResult(False, raw)
    _emit(ctx, "git.diff", {"repo": str(root), "stat": stat, "raw": raw[:12000],
                            "empty": not raw})
    if not raw:
        return ToolResult(True, "Aucune modification en attente dans le répertoire de travail.", data={})
    return ToolResult(True, raw[:15000] or "Aucune sortie.", data={"stat": stat, "raw": raw[:15000]})


registry.add(
    id="git.diff", name="Diff Git du projet", category="Développement",
    description="Diff git réel du projet sélectionné (modifications en attente, ou stat si stat=true).",
    handler=git_diff, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "repo": {"type": "string"}, "stat": {"type": "boolean", "description": "réduire au résumé (--stat)"},
        "staged": {"type": "boolean", "description": "diff de l'index (--cached)"}},
        "required": []},
)


def git_log(ctx: ToolContext) -> ToolResult:
    root, err = _require_dir(ctx)
    if err:
        return ToolResult(False, err + "\nSinon passe l'argument repo ou cwd.")
    if not (root / ".git").exists():
        return ToolResult(False, f"{root} n'est pas un dépôt git.")
    n = int(ctx.arguments.get("n") or 10)
    code, raw = _git_run(f"git log --oneline -{n}", str(root))
    if code != 0:
        return ToolResult(False, raw)
    _emit(ctx, "git.log", {"repo": str(root), "raw": raw[:6000]})
    return ToolResult(True, raw[:12000] or "Aucun commit.", data={"n": n})


registry.add(
    id="git.log", name="Journal Git du projet", category="Développement",
    description="Derniers commits réels du dépôt (git log --oneline).",
    handler=git_log, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "repo": {"type": "string"}, "n": {"type": "integer", "default": 10}}, "required": []},
)

def project_discord_bot(ctx: ToolContext) -> ToolResult:
    """Trouve le bot Discord de l'utilisateur à partir de preuves réelles."""
    resolver = ctx.core.projects
    refresh = bool(ctx.arguments.get("refresh"))
    proj, candidates = resolver.resolve_discord_bot(refresh=refresh)
    if proj is not None:
        resolver.select(proj)
        resolver.remember_bot(proj)
        evidence = next((c for c in candidates if c.project.path == proj.path), None)
        lines = [f"Bot Discord : {proj.display_name}", f"Chemin : {proj.path}"]
        if evidence:
            if evidence.framework:
                lines.append(f"Framework : {evidence.framework}")
            if evidence.entrypoint:
                lines.append(f"Point d'entrée : {evidence.entrypoint}")
            if evidence.start_command:
                lines.append(f"Démarrage : {evidence.start_command}")
            lines.append("Preuves :\n- " + "\n- ".join(evidence.facts))
        return ToolResult(True, "\n".join(lines),
                          data=(evidence.summary() if evidence else proj.summary()))
    if candidates:
        lines = [f"{c.project.display_name} — {c.project.path} (score {c.score})"
                 for c in candidates]
        return ToolResult(False,
                          "Plusieurs bots Discord réels ont été trouvés. Demande à "
                          "l'utilisateur lequel est le sien :\n" + "\n".join(lines),
                          data=[c.summary() for c in candidates])
    return ToolResult(False, "Aucun bot Discord détecté dans les dossiers autorisés.")


registry.add(
    id="project.discord_bot", name="Trouver le bot Discord", category="Projets",
    description=(
        "Identifie le projet bot Discord de l'utilisateur SANS qu'il fournisse de chemin. "
        "La détection repose sur des preuves lues sur disque : bibliothèque Discord "
        "déclarée, dossiers commands/events, point d'entrée qui se connecte, jeton "
        "attendu en configuration. Sélectionne le projet et le mémorise. Utilise cet "
        "outil dès qu'une demande parle de « mon bot Discord » sans nommer de dossier."
    ),
    handler=project_discord_bot, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "refresh": {"type": "boolean", "description": "Rescanner les dossiers avant de chercher."}},
        "required": []},
)


def process_find(ctx: ToolContext) -> ToolResult:
    """Processus RÉELS du système qui tournent pour ce projet.

    `process.status` ne connaît que ce que JARVIS a lancé lui-même. Avant de
    démarrer un bot, il faut savoir si une instance tourne DÉJÀ — peu importe
    qui l'a lancée — sinon on met deux bots sur le même jeton et le test
    devient ininterprétable.
    """
    try:
        import psutil
    except Exception:
        return ToolResult(False, "psutil indisponible : impossible d'inspecter les processus système.")
    target = str(ctx.arguments.get("path") or "").strip()
    if not target:
        current = getattr(ctx.core.projects, "current", None)
        target = current.path if current else ""
    if not target:
        return ToolResult(False, "Indique le chemin du projet (ou sélectionne-le d'abord).")
    from pathlib import Path as _P
    try:
        root = _P(target).expanduser().resolve()
    except Exception as exc:
        return ToolResult(False, f"Chemin invalide : {exc}")
    needle = str(root)
    found: list[dict] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time", "username"]):
        try:
            info = proc.info
            cmdline = " ".join(info.get("cmdline") or [])
            try:
                cwd = proc.cwd()
            except Exception:
                cwd = ""
            # Le chemin du projet peut apparaître dans n'importe quelle ligne
            # de commande (un shell, un éditeur, cet outil lui-même). On ne
            # retient qu'un vrai processus applicatif : soit son répertoire de
            # travail est DANS le projet, soit c'est un exécutable connu lancé
            # sur un fichier du projet.
            in_cwd = bool(cwd) and (cwd == needle or cwd.startswith(needle + "/"))
            runtime = (info.get("name") or "").lower() in {
                "node", "node22", "node20", "bun", "deno", "python", "python3",
                "python3.11", "python3.12", "pm2", "pm2-runtime", "nodemon"}
            in_cmd = runtime and needle in cmdline
            if not in_cwd and not in_cmd:
                continue
            if info["pid"] == __import__("os").getpid():
                continue
            found.append({"pid": info["pid"], "name": info.get("name") or "",
                          "cmdline": cmdline[:300], "cwd": cwd,
                          "user": info.get("username") or "",
                          "started_at": info.get("create_time") or 0})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if not found:
        return ToolResult(True, f"Aucun processus en cours pour {root}.", data=[])
    import time as _t
    lines = []
    for p in found:
        age = int((_t.time() - p["started_at"]) / 60) if p["started_at"] else 0
        lines.append(f"pid={p['pid']} {p['name']} depuis {age} min\n  cwd : {p['cwd']}\n  cmd : {p['cmdline']}")
    return ToolResult(True, f"{len(found)} processus en cours pour {root} :\n" + "\n".join(lines),
                      data=found)


registry.add(
    id="process.find", name="Processus en cours d'un projet", category="Développement",
    description=(
        "Cherche dans les processus RÉELS du système ceux qui tournent pour un projet "
        "(par chemin de travail ou ligne de commande), même s'ils n'ont pas été lancés "
        "par JARVIS. Renvoie pid, commande, répertoire et ancienneté. À utiliser AVANT "
        "process.start pour ne pas démarrer une seconde instance du même bot."
    ),
    handler=process_find, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "path": {"type": "string", "description": "Chemin du projet (défaut : projet sélectionné)."}},
        "required": []},
)


def project_audit(ctx: ToolContext) -> ToolResult:
    """Audit RÉEL et complet d'un projet, en une seule opération.

    L'enchaînement est déterministe : git, manifeste, arborescence, commandes,
    point d'entrée, scripts déclarés, processus en cours, état Discord. Chaque
    ligne du rapport vient d'une opération réellement exécutée ici.
    """
    from ..project_audit import ProjectAuditPipeline

    resolver = ctx.core.projects
    query = str(ctx.arguments.get("name") or ctx.arguments.get("query") or "").strip()
    proj = None
    if query and not resolver.looks_like_discord_bot(query):
        matches = _resolve_project(ctx, query)
        if len(matches) > 1:
            lines = [f"{p.display_name} — {p.path}" for p in matches]
            return ToolResult(False, "Plusieurs projets correspondent. Précise lequel :\n"
                              + "\n".join(lines), data=[p.summary() for p in matches])
        proj = matches[0] if matches else None
    if proj is None:
        resolved, candidates = resolver.resolve_discord_bot()
        if resolved is None and candidates:
            lines = [f"{c.project.display_name} — {c.project.path} (score {c.score})"
                     for c in candidates]
            return ToolResult(False, "Plusieurs bots Discord réels existent. Demande à "
                              "l'utilisateur lequel auditer :\n" + "\n".join(lines),
                              data=[c.summary() for c in candidates])
        proj = resolved or resolver.current
    if proj is None:
        return ToolResult(False, "Aucun projet à auditer : nomme-le ou sélectionne-le d'abord.")
    resolver.select(proj)
    if resolver.looks_like_discord_bot(query or "bot discord"):
        resolver.remember_bot(proj)
    report = ProjectAuditPipeline(ctx.core).run(proj, task_id=ctx.task_id)
    return ToolResult(True, report["summary"], data=report)


registry.add(
    id="project.audit", name="Auditer un projet", category="Projets",
    description=(
        "Audit COMPLET et réel d'un projet en une seule opération : dépôt git et "
        "branche, manifeste et dépendances, arborescence, commandes/événements, point "
        "d'entrée, scripts de test déclarés, processus réellement en cours et état du "
        "connecteur Discord. Sans argument, audite le bot Discord de l'utilisateur. "
        "Utilise cet outil pour toute demande « analyse / dans quel état est mon projet »."
    ),
    handler=project_audit, risk=READ_ONLY,
    input_schema={"type": "object", "properties": {
        "name": {"type": "string", "description": "Nom du projet (défaut : le bot Discord)."}},
        "required": []},
)
