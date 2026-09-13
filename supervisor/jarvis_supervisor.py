"""JarvisSupervisor — processus externe stable de gestion des releases JARVIS.

Rôle (strictement externe au package `jarvis`) :
- connaître la release active
- démarrer / arrêter / redémarrer JARVIS
- installer une candidate validée (merge Git + restart + /health)
- conserver la release précédente (tag Git + backups) et assurer le rollback
- exposer un petit serveur HTTP local (127.0.0.1:8770) pour les demandes de
  promotion / rollback / statut.

Aucun module de `jarvis` n'importe ce fichier. SelfUpgradeService ne peut jamais
modifier le contenue du dossier `supervisor/` (chemins protégés).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SU_VERSION = "JARVIS_SUPERVISOR_V1"
API_PORT = int(os.getenv("JARVIS_SUPERVISOR_PORT", "8770"))
MAIN_PORT = int(os.getenv("JARVIS_PORT", "8765"))

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
BACKUP_DIR = ROOT / "backups"
RELEASES_DIR = ROOT / "releases"
UPGRADE_WORKSPACES = ROOT / "upgrade-workspaces"
STATE_FILE = STATE_DIR / "supervisor.json"
MANIFEST = RELEASES_DIR / "manifest.json"


def _venv_python() -> str:
    win = ROOT / ".venv" / "Scripts" / "python.exe"
    if win.exists():
        return str(win)
    unix = ROOT / ".venv" / "bin" / "python3"
    if unix.exists():
        return str(unix)
    unix = ROOT / ".venv" / "bin" / "python"
    if unix.exists():
        return str(unix)
    return sys.executable


# -- utilitaires système ---------------------------------------------------
def _run(cmd: list[str], cwd: Path | None = None, timeout: float = 120.0) -> subprocess.CompletedProcess:
    kwargs = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(cmd, cwd=str(cwd or ROOT), capture_output=True,
                          text=True, timeout=timeout, encoding="utf-8", errors="replace",
                          **kwargs)


def find_pid_on_port(port: int) -> int:
    if sys.platform.startswith("win"):
        try:
            out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True,
                                 text=True, timeout=15, encoding="utf-8", errors="replace").stdout
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING":
                    local = parts[1]
                    if local.endswith(f":{port}"):
                        return int(parts[4])
        except Exception:
            return 0
    try:
        import psutil
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and conn.status == "LISTEN":
                return int(conn.pid or 0)
    except Exception:
        pass
    return 0


def kill_pid(pid: int) -> None:
    if not pid:
        return
    if sys.platform.startswith("win"):
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=15)
            return
        except Exception:
            pass
    try:
        os.kill(pid, 9)
    except Exception:
        pass


def health(port: int, timeout: float = 8.0) -> dict:
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return {"ok": resp.status == 200, "status": resp.status,
                    "body": resp.read().decode("utf-8", errors="replace")[:500]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _git(*args: str, timeout: float = 120.0) -> subprocess.CompletedProcess:
    return _run(["git"] + list(args), timeout=timeout)


# -- état persistant -------------------------------------------------------
def load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"api_port": API_PORT, "main_port": MAIN_PORT, "main_pid": 0,
            "active_release": None, "installing": False, "installing_upgrade_id": "",
            "last_installed_upgrade_id": "", "last_failed_upgrade_id": "",
            "last_rollback_status": "", "upgrades": {}}


def save_state(state: dict) -> None:
    state["updated_at"] = time.time()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def record_releases(release: dict) -> None:
    RELEASES_DIR.mkdir(parents=True, exist_ok=True)
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else []
    except Exception:
        manifest = []
    manifest.append(release)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


# -- gestion JARVIS --------------------------------------------------------
def start_main() -> dict:
    pid = find_pid_on_port(MAIN_PORT)
    if pid:
        return {"ok": True, "already_running": True, "pid": pid}
    env = dict(os.environ)
    env.update({"JARVIS_PORT": str(MAIN_PORT), "JARVIS_HOST": "127.0.0.1",
                "JARVIS_LAUNCH_UI": os.getenv("JARVIS_LAUNCH_UI", "1")})
    try:
        proc = subprocess.Popen([_venv_python(), "jarvis.py"], cwd=str(ROOT), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    state = load_state()
    state["main_pid"] = proc.pid
    save_state(state)
    ok = wait_healthy(MAIN_PORT, attempts=45)
    return {"ok": ok, "pid": proc.pid, "health": health(MAIN_PORT)}


def stop_main() -> dict:
    pid = find_pid_on_port(MAIN_PORT)
    if not pid:
        state = load_state()
        pid = int(state.get("main_pid") or 0)
    if not pid:
        return {"ok": True, "already_stopped": True}
    kill_pid(pid)
    for _ in range(20):
        if not find_pid_on_port(MAIN_PORT):
            break
        time.sleep(0.5)
    return {"ok": not find_pid_on_port(MAIN_PORT), "pid": pid}


def wait_healthy(port: int, attempts: int = 30, gap: float = 2.0) -> bool:
    for _ in range(attempts):
        if health(port).get("ok"):
            return True
        time.sleep(gap)
    return bool(health(port).get("ok"))


# -- promotion / rollback --------------------------------------------------
def _install(upgrade_id: str, branch: str) -> dict:
    state = load_state()
    state["installing"] = True
    state["installing_upgrade_id"] = upgrade_id
    save_state(state)
    result = {"upgrade_id": upgrade_id, "status": "installing", "ok": False}
    try:
        if _git("rev-parse", "--verify", f"refs/heads/{branch}").returncode != 0:
            raise RuntimeError(f"branche inconnue: {branch}")
        if _git("merge-base", "main", branch).returncode != 0:
            raise RuntimeError("branche sans base commune avec main")
        pre = f"SU/pre-{upgrade_id}"
        _git("tag", "-f", pre, "main")  # conserve la release précédente
        stop = stop_main()
        if not stop.get("ok"):
            raise RuntimeError("échec arrêt JARVIS avant installation")
        co = _git("checkout", "main")
        if co.returncode != 0:
            _git("reset", "--hard", pre)
            raise RuntimeError("échec checkout main : " + co.stderr[-400:])
        merged = _git("merge", "--ff-only", branch)
        if merged.returncode != 0:
            _git("reset", "--hard", pre)
            raise RuntimeError("merge impossible, restauration de main ("
                               + (merged.stderr or merged.stdout)[-300:] + ")")
        snapshots = _snapshot_workspace(upgrade_id, branch)
        started = start_main()
        if not started.get("ok"):
            self_res = _rollback_to(pre)
            raise RuntimeError("JARVIS relancé mais /health en échec — rollback " + str(self_res))
        result["ok"] = True
        result["status"] = "installed"
        result["pid"] = started.get("pid")
        state["active_release"] = pre
        state["last_installed_upgrade_id"] = upgrade_id
        state["last_failed_upgrade_id"] = ""
        record_releases({"upgrade_id": upgrade_id, "tag": pre, "branch": branch,
                         "installed_at": time.time(), "snapshots": snapshots})
    except Exception as exc:
        result["ok"] = False
        result["status"] = "failed"
        result["error"] = str(exc)[:1000]
        state["last_failed_upgrade_id"] = upgrade_id
    finally:
        state["installing"] = False
        state["installing_upgrade_id"] = ""
        save_state(state)
    return result


def _snapshot_workspace(upgrade_id: str, branch: str) -> list[str]:
    """Copie les fichiers modifiés du workspace avant installation du nouveau HEAD."""
    try:
        out = _git("diff", "--name-status", f"main...{branch}").stdout
    except Exception:
        out = ""
    changed = [ln.split("\t")[-1] for ln in out.splitlines() if "\t" in ln]
    dest = BACKUP_DIR / "pre-upgrade" / upgrade_id
    snapshots = []
    for rel in changed:
        rel = rel.replace("\\", "/")
        src = ROOT / rel
        if src.exists() and not any(p in rel for p in ("supervisor/", "state/", "backups/",
                                                       "releases/", "upgrade-workspaces/")):
            try:
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
                snapshots.append(rel)
            except Exception:
                pass
    return snapshots


def _rollback_to(tag: str) -> dict:
    stop_main()
    _git("reset", "--hard", tag)
    started = start_main()
    return {"ok": bool(started.get("ok")), "tag": tag}


def promote(upgrade_id: str, branch: str) -> dict:
    if load_state().get("installing"):
        return {"ok": False, "error": "installation en cours, patiente."}
    thread = threading.Thread(target=_install, args=(upgrade_id, branch), daemon=True)
    thread.start()
    return {"ok": True, "accepted": True, "upgrade_id": upgrade_id}


def rollback(upgrade_id: str) -> dict:
    tag = f"SU/pre-{upgrade_id}"
    if _git("rev-parse", "--verify", f"refs/tags/{tag}").returncode != 0:
        return {"ok": False, "error": f"aucune release précédente ({tag})"}
    result = _rollback_to(tag)
    state = load_state()
    state["active_release"] = tag
    state["last_rollback_status"] = "rolled_back"
    save_state(state)
    return {"ok": result.get("ok"), "tag": tag}


# -- serveur HTTP ----------------------------------------------------------
class SupervisorHandler(BaseHTTPRequestHandler):
    server_version = f"JarvisSupervisor/{SU_VERSION}"

    def log_message(self, fmt, *args):  # silencieux
        pass

    def _send(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):  # noqa: N802
        if self.path == "/api/supervisor/ping":
            return self._send({"ok": True, "version": SU_VERSION, "main_port": MAIN_PORT})
        if self.path == "/api/supervisor/status":
            state = load_state()
            st = health(MAIN_PORT)
            state["main_running"] = st.get("ok")
            state["main_health"] = st
            return self._send({"ok": True, "version": SU_VERSION, **state})
        if self.path == "/api/supervisor/releases":
            try:
                manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else []
            except Exception:
                manifest = []
            return self._send({"ok": True, "releases": manifest})
        return self._send({"ok": False, "error": "endpoint inconnu"}, 404)

    def do_POST(self):  # noqa: N802
        data = self._read()
        if self.path == "/api/supervisor/promote":
            upgrade_id = str(data.get("upgrade_id") or "")
            branch = str(data.get("branch") or "")
            if not upgrade_id or not branch:
                return self._send({"ok": False, "error": "upgrade_id et branch requis"}, 400)
            return self._send(promote(upgrade_id, branch))
        if self.path == "/api/supervisor/rollback":
            upgrade_id = str(data.get("upgrade_id") or "")
            if not upgrade_id:
                return self._send({"ok": False, "error": "upgrade_id requis"}, 400)
            return self._send(rollback(upgrade_id))
        if self.path == "/api/supervisor/stop_main":
            return self._send(stop_main())
        if self.path == "/api/supervisor/start_main":
            return self._send(start_main())
        return self._send({"ok": False, "error": "endpoint inconnu"}, 404)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="JarvisSupervisor — gestion des releases JARVIS")
    parser.add_argument("--port", type=int, default=API_PORT)
    parser.add_argument("--install", nargs=2, metavar=("UPGRADE_ID", "BRANCH"),
                        help="Installer une candidate validée en ligne de commande")
    parser.add_argument("--rollback", metavar="UPGRADE_ID", help="Rollback CLI")
    parser.add_argument("--start", action="store_true", help="Démarrer JARVIS")
    parser.add_argument("--stop", action="store_true", help="Arrêter JARVIS")
    args = parser.parse_args()

    if args.install:
        result = _install(args.install[0], args.install[1])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if args.rollback:
        result = rollback(args.rollback)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if args.start:
        print(json.dumps(start_main(), ensure_ascii=False, indent=2))
        return 0
    if args.stop:
        print(json.dumps(stop_main(), ensure_ascii=False, indent=2))
        return 0

    server = ThreadingHTTPServer(("127.0.0.1", args.port), SupervisorHandler)
    server.daemon_threads = True
    print(f"JarvisSupervisor {SU_VERSION} → http://127.0.0.1:{args.port} (JARVIS port {MAIN_PORT})",
          flush=True)
    print(f"State : {STATE_FILE}\nReleases : {MANIFEST}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())