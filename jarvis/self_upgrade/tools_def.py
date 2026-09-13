"""Définition des outils réels du LocalCodeAgent."""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .inspector import ProjectInspector
from .git_mgr import GitManager


def _run(cmd: list[str], cwd: str | Path, timeout: float = 120.0) -> dict[str, Any]:
    try:
        res = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                             timeout=timeout, encoding="utf-8", errors="replace")
        return {"ok": res.returncode == 0, "stdout": res.stdout, "stderr": res.stderr,
                "returncode": res.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": f"timeout après {timeout}s", "returncode": -1}
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc), "returncode": -1}


@dataclass
class AgentContext:
    workspace: Path
    project_root: Path
    python: str = "python"
    candidate_started: bool = False
    candidate_port: int = 0
    candidate_pid: int = 0
    tools_used: list[str] = field(default_factory=list)
    logs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def inspector(self) -> ProjectInspector:
        return ProjectInspector(self.workspace)

    def log(self, level: str, message: str) -> None:
        self.logs.append({"ts": time.time(), "level": level, "message": message})
        print(f"[SU-AGENT][{level.upper()}] {message}", flush=True)


def _check_path(ctx: AgentContext, rel: str) -> str | None:
    rel = (rel or "").replace("\\", "/").lstrip("./")
    parts = rel.split("/")
    if ".." in parts or rel.startswith("/") or (parts and parts[0] in {
            "supervisor", "state", "backups", "releases", "upgrade-workspaces", "data"}):
        return f"Chemin protégé/interdit: {rel}"
    return None


def _abs(ctx: AgentContext, rel: str) -> Path:
    return (ctx.workspace / rel).resolve()


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------
def tool_code_search(ctx: AgentContext, args: dict[str, Any]) -> dict[str, Any]:
    pattern = str(args.get("pattern") or "").strip()
    if not pattern:
        return {"ok": False, "output": "pattern requis pour code.search."}
    include = str(args.get("include") or "*.py")
    results = ctx.inspector.search(pattern, include=include, max_results=int(args.get("max_results") or 30))
    ctx.tools_used.append("code.search")
    if not results:
        return {"ok": True, "output": "Aucun résultat.", "data": {"matches": []}}
    lines = [f'{r["file"]}:{r["line"]}  {r["text"]}' for r in results[:40]]
    return {"ok": True, "output": "\n".join(lines), "data": {"matches": len(results)}}


def tool_code_list(ctx: AgentContext, args: dict[str, Any]) -> dict[str, Any]:
    pattern = str(args.get("glob") or "**/*.py")
    files = ctx.inspector.list_files(pattern)
    ctx.tools_used.append("code.list")
    out = "\n".join(files[:100]) if files else "(vide)"
    return {"ok": True, "output": out, "data": {"count": len(files)}}


def tool_code_read(ctx: AgentContext, args: dict[str, Any]) -> dict[str, Any]:
    rel = str(args.get("file") or "")
    err = _check_path(ctx, rel)
    if err:
        return {"ok": False, "output": err}
    max_lines = int(args.get("max_lines") or 0)
    body = ctx.inspector.read_file(rel, max_lines=max_lines or 800)
    ctx.tools_used.append("code.read")
    if not body:
        return {"ok": False, "output": f"Fichier introuvable: {rel}"}
    return {"ok": True, "output": f"----- FILE: {rel} -----\n{body}"}


def tool_code_write(ctx: AgentContext, args: dict[str, Any]) -> dict[str, Any]:
    rel = str(args.get("file") or "")
    content = str(args.get("content") or "")
    err = _check_path(ctx, rel)
    if err:
        return {"ok": False, "output": err}
    target = _abs(ctx, rel)
    try:
        if not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "output": f"Erreur écriture {rel}: {exc}"}
    ctx.tools_used.append("code.write")
    return {"ok": True, "output": f"Écrit : {rel} ({len(content)} caractères)."}


def tool_code_patch(ctx: AgentContext, args: dict[str, Any]) -> dict[str, Any]:
    """Modification ciblée : remplacement exact old→new (ou diff unifié simple)."""
    rel = str(args.get("file") or "")
    err = _check_path(ctx, rel)
    if err:
        return {"ok": False, "output": err}
    target = _abs(ctx, rel)
    if not target.exists():
        return {"ok": False, "output": f"Fichier introuvable: {rel} (utilise code.write pour créer)."}
    orig = target.read_text(encoding="utf-8", errors="replace")
    old = str(args.get("old") or "")
    new = str(args.get("new") or "")
    if old and old in orig:
        if old == new:
            return {"ok": True, "output": f"old == new : rien à changer dans {rel}."}
        updated = orig.replace(old, new, 1)
        target.write_text(updated, encoding="utf-8")
        ctx.tools_used.append("code.patch")
        return {"ok": True, "output": f"Patch appliqué à {rel} (remplacement simple)."}
    if old and old not in orig:
        return {"ok": False,
                "output": "old introuvable dans le fichier. Relis le fichier et reprends le texte EXACT."}
    return {"ok": False, "output": "code.patch exige les paramètres old et new (texte exact)."}


def tool_git_status(ctx: AgentContext, args: dict[str, Any]) -> dict[str, Any]:
    g = GitManager(ctx.workspace)
    ctx.tools_used.append("git.status")
    return {"ok": True, "output": g.status() or "(aucune modification)"}


def tool_git_diff(ctx: AgentContext, args: dict[str, Any]) -> dict[str, Any]:
    g = GitManager(ctx.workspace)
    ctx.tools_used.append("git.diff")
    d = g.diff("HEAD")
    return {"ok": True, "output": d[:6000] or "(aucun diff)"}


def tool_git_commit(ctx: AgentContext, args: dict[str, Any]) -> dict[str, Any]:
    message = str(args.get("message") or "upgrade")
    g = GitManager(ctx.workspace)
    ctx.tools_used.append("git.commit")
    ok = g.commit(message)
    return {"ok": ok, "output": "Commit créé." if ok else "Échec du commit (rien à commit ?)."}


def tool_test_run(ctx: AgentContext, args: dict[str, Any]) -> dict[str, Any]:
    target = str(args.get("tests") or "discover -s tests")
    if not target.startswith("discover"):
        target = "discover -s tests " + target
    cmd = [ctx.python, "-m", "unittest", *target.split()]
    ctx.tools_used.append("test.run")
    res = _run(cmd, ctx.workspace, timeout=float(args.get("timeout") or 180))
    tail = "\n".join((res["stdout"] + res["stderr"]).splitlines()[-60:])
    return {"ok": res["ok"], "output": tail or "(aucune sortie)",
            "data": {"returncode": res["returncode"]}}


def tool_terminal_run(ctx: AgentContext, args: dict[str, Any]) -> dict[str, Any]:
    cmd = str(args.get("command") or "").strip()
    if not cmd:
        return {"ok": False, "output": "commande vide"}
    forbidden = ("rm -rf", "rmdir /s", "format ", "del /", "git push", "git reset --hard", "git checkout -f")
    if any(f in cmd.lower() for f in forbidden):
        return {"ok": False, "output": "commande refusée (destructive)."}
    cwd = str(args.get("cwd") or ctx.workspace)
    ctx.tools_used.append("terminal.run_safe")
    res = _run([cmd], cwd, timeout=float(args.get("timeout") or 120)) if _windows() else \
        _run(cmd.split(), cwd, timeout=float(args.get("timeout") or 120))
    tail = "\n".join((res["stdout"] + res["stderr"]).splitlines()[-80:])
    return {"ok": res["ok"], "output": tail or "(aucune sortie)",
            "data": {"returncode": res["returncode"]}}


def _windows() -> bool:
    import platform
    return platform.system() == "Windows"


def tool_app_start_candidate(ctx: AgentContext, args: dict[str, Any]) -> dict[str, Any]:
    port = int(args.get("port") or (ctx.candidate_port or 8791))
    ctx.candidate_port = port
    if ctx.candidate_started:
        return {"ok": True, "output": f"Candidate déjà démarrée sur le port {port}."}
    import subprocess as sp
    env = {"JARVIS_PORT": str(port), "JARVIS_HOST": "127.0.0.1",
           "JARVIS_LAUNCH_UI": "0", "JARVIS_DATA_DIR": str(ctx.workspace / "data_candidate")}
    proc = sp.Popen(
        [ctx.python, "jarvis.py"],
        cwd=str(ctx.workspace), env=env,
        stdout=sp.PIPE, stderr=sp.STDOUT, text=True,
        creationflags=getattr(sp, "CREATE_NO_WINDOW", 0),
    )
    ctx.candidate_started = True
    ctx.candidate_pid = proc.pid
    ctx.tools_used.append("app.start_candidate")
    return {"ok": True, "output": f"Candidate lancée (pid {proc.pid}) sur http://127.0.0.1:{port}/"}


def tool_app_stop_candidate(ctx: AgentContext, args: dict[str, Any]) -> dict[str, Any]:
    if not ctx.candidate_started:
        return {"ok": True, "output": "Aucune candidate active."}
    try:
        subprocess.run(["taskkill", "/PID", str(ctx.candidate_pid), "/T", "/F"],
                       capture_output=True, timeout=10)
    except Exception:
        pass
    ctx.candidate_started = False
    ctx.tools_used.append("app.stop_candidate")
    return {"ok": True, "output": f"Candidate arrêtée (pid {ctx.candidate_pid})."}


def tool_app_healthcheck(ctx: AgentContext, args: dict[str, Any]) -> dict[str, Any]:
    port = int(args.get("port") or ctx.candidate_port or 8791)
    endpoint = str(args.get("endpoint") or "/api/health")
    ctx.tools_used.append("app.healthcheck")
    import urllib.request
    url = f"http://127.0.0.1:{port}{endpoint}"
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            body = resp.read().decode("utf-8", errors="replace")[:1000]
            return {"ok": resp.status == 200, "output": f"GET {url} → {resp.status}\n{body}",
                    "data": {"status": resp.status}}
    except Exception as exc:
        return {"ok": False, "output": f"Healthcheck {url} : {exc}"}


def tool_browser_open(ctx: AgentContext, args: dict[str, Any]) -> dict[str, Any]:
    import webbrowser
    url = str(args.get("url") or "")
    if not url:
        return {"ok": False, "output": "url requise."}
    ctx.tools_used.append("browser.open")
    webbrowser.open(url)
    return {"ok": True, "output": f"Ouvert dans le navigateur : {url}"}


def tool_browser_screenshot(ctx: AgentContext, args: dict[str, Any]) -> dict[str, Any]:
    ctx.tools_used.append("browser.screenshot")
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:
        return {"ok": False,
                "output": "Capture impossible : playwright non installé. Commande : "
                          ".venv\\Scripts\\python.exe -m pip install playwright && playwright install chromium"}
    url = str(args.get("url") or f"http://127.0.0.1:{ctx.candidate_port or 8791}/")
    out = ctx.workspace / "candidate_screenshot.png"
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(url, timeout=20000)
            page.wait_for_load_state("networkidle")
            page.screenshot(path=str(out))
            browser.close()
        return {"ok": True, "output": f"Capture : {out}"}
    except Exception as exc:
        return {"ok": False, "output": f"Capture impossible : {exc}"}


def tool_upgrade_request_promotion(ctx: AgentContext, args: dict[str, Any]) -> dict[str, Any]:
    ctx.tools_used.append("upgrade.request_promotion")
    ctx.request_promotion = True
    reason = str(args.get("reason") or "validation terminée")
    return {"ok": True, "output": f"Promotion demandée ({reason}). Le Supervisor décidera."}


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "code.search",
            "description": "Recherche un motif dans les sources du workspace (workspace JARVIS cloné).",
            "parameters": {"type": "object", "properties": {
                "pattern": {"type": "string"},
                "include": {"type": "string", "description": "glob, défaut **.py"},
                "max_results": {"type": "integer"}}, "required": ["pattern"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "code.list",
            "description": "Liste les fichiers du workspace selon un glob (ex: **/*.py, ui/js/*.js).",
            "parameters": {"type": "object", "properties": {
                "glob": {"type": "string"}}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "code.read",
            "description": "Lit un fichier du workspace (chemin relatif).",
            "parameters": {"type": "object", "properties": {
                "file": {"type": "string"},
                "max_lines": {"type": "integer"}}, "required": ["file"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "code.write",
            "description": "Écrit (ou crée) un fichier dans le workspace avec son contenu complet.",
            "parameters": {"type": "object", "properties": {
                "file": {"type": "string"},
                "content": {"type": "string"}}, "required": ["file", "content"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "code.patch",
            "description": "Applique une modification ciblée : fournis old (texte exact) et new (remplacement), "
                         "ou un diff unifié.",
            "parameters": {"type": "object", "properties": {
                "file": {"type": "string"},
                "patch": {"type": "string", "description": "diff unifié optional"},
                "old": {"type": "string"},
                "new": {"type": "string"}}, "required": ["file"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git.status",
            "description": "État Git du workspace (fichiers modifiés).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git.diff",
            "description": "Diff Git des modifications du workspace.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git.commit",
            "description": "Commite les modifications du workspace avec un message.",
            "parameters": {"type": "object", "properties": {
                "message": {"type": "string"}}, "required": ["message"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "test.run",
            "description": "Exécute les tests unittest du workspace. target vide ou 'discover -s tests'.",
            "parameters": {"type": "object", "properties": {
                "tests": {"type": "string"},
                "timeout": {"type": "integer"}}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal.run_safe",
            "description": "Exécute une commande sûre dans le workspace (pas de commande destructive).",
            "parameters": {"type": "object", "properties": {
                "command": {"type": "string"},
                "cwd": {"type": "string"},
                "timeout": {"type": "integer"}}, "required": ["command"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "app.start_candidate",
            "description": "Démarre la candidate (cette copie de JARVIS) sur un port dédié.",
            "parameters": {"type": "object", "properties": {
                "port": {"type": "integer"}}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "app.stop_candidate",
            "description": "Arrête la candidate.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "app.healthcheck",
            "description": "Vérifie la santé HTTP de la candidate (par défaut /api/health).",
            "parameters": {"type": "object", "properties": {
                "port": {"type": "integer"},
                "endpoint": {"type": "string"}}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser.open",
            "description": "Ouvre une URL dans le navigateur.",
            "parameters": {"type": "object", "properties": {
                "url": {"type": "string"}}, "required": ["url"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser.screenshot",
            "description": "Capture d'écran de la candidate (nécessite playwright).",
            "parameters": {"type": "object", "properties": {
                "url": {"type": "string"}}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "upgrade.request_promotion",
            "description": "Demande la mise en production de cette candidate une fois tout validé.",
            "parameters": {"type": "object", "properties": {
                "reason": {"type": "string"}}, "required": []},
        },
    },
]

HANDLERS: dict[str, Callable[[AgentContext, dict[str, Any]], dict[str, Any]]] = {
    "code.search": tool_code_search,
    "code.list": tool_code_list,
    "code.read": tool_code_read,
    "code.write": tool_code_write,
    "code.patch": tool_code_patch,
    "git.status": tool_git_status,
    "git.diff": tool_git_diff,
    "git.commit": tool_git_commit,
    "test.run": tool_test_run,
    "terminal.run_safe": tool_terminal_run,
    "app.start_candidate": tool_app_start_candidate,
    "app.stop_candidate": tool_app_stop_candidate,
    "app.healthcheck": tool_app_healthcheck,
    "browser.open": tool_browser_open,
    "browser.screenshot": tool_browser_screenshot,
    "upgrade.request_promotion": tool_upgrade_request_promotion,
}