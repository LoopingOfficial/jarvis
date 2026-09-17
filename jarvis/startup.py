"""JARVIS Startup Manager — prépare Windows puis lance JARVIS en une commande.

Réutilise le Doctor (`jarvis.doctor`) : il mesure l'état réel, le startup ne
réécrit aucune sonde. Politique stricte :

  * aucun statut inventé — tout vient d'une mesure ;
  * on ne lance que ce qui manque (JARVIS, serveur LLM si un launcher existe) ;
  * jamais de destruction : pas de taskkill, pas de kill global, pas de
    suppression. Un processus tiers est signalé, jamais tué ;
  * aucune erreur Python masquée : JARVIS tourne au premier plan, stdout et
    stderr restent visibles.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .config import IS_WINDOWS, ROOT
from .doctor import (
    BLOCKED,
    ERROR,
    LLM_API_PORT,
    MAIN_API_PORT,
    OK,
    OPTIONAL,
    _http_json,
    _port_state,
    full_diagnose,
)

# Le Core charge des bibliothèques natives dont le runtime Intel Fortran
# intercepte Ctrl+C et tue le processus (« forrtl: error (200) »). On désactive
# ce handler natif : c'est JARVIS qui doit gérer son arrêt, pas le runtime.
os.environ.setdefault("FOR_DISABLE_CONSOLE_CTRL_HANDLER", "1")

DEFAULT_ENTRYPOINT = ROOT / "jarvis.py"
LOG_DIR = ROOT / "logs" / "startup"
SUPERVISOR_PORT = 8770
COMFY_PORT = 8188

# Contrôles affichés en tête, dans un ordre lisible par un humain.
_HEADLINE = (
    ("python", "Python"),
    ("repository", "Repository"),
    ("imports", "Imports"),
    ("database", "Database"),
    ("eventbus", "EventBus"),
    ("configuration", "Configuration"),
    ("secrets", "Secrets"),
    ("llm_server", "LLM Server"),
    ("llm_model", "LLM Model"),
    ("ports", "Ports"),
    ("api_server", "API Server"),
    ("tool_registry", "Tools"),
    ("mission_control", "Mission Control"),
)
# Briques externes : affichées après, un échec n'est pas bloquant en général.
_TAIL = (
    ("comfyui", "ComfyUI"),
    ("supervisor", "Supervisor"),
    ("blender", "Blender"),
    ("stt", "Dictée (STT)"),
    ("piper", "Voix Piper"),
    ("playwright", "Playwright"),
    ("discord", "Bot Discord"),
    ("gpu", "Mesure GPU"),
    ("brainrot_imports", "Brainrot"),
    ("core", "Core startup"),
    ("external_bricks", "Briques externes"),
    ("llm", "Modèle LLM"),
    ("vault", "Coffre"),
    ("clap", "Double clap"),
    ("n8n", "Connecteur n8n"),
    ("vision", "Vue (avatar)"),
)


def _safe_print(text: str) -> None:
    """Un terminal cp1252 ne doit pas faire échouer le démarrage."""
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "ascii"
        print(text.encode(encoding, "replace").decode(encoding, "replace"))


# --------------------------------------------------------------- état réel
def probe_jarvis_health(port: int = MAIN_API_PORT,
                        http_json: Callable[[str], Any] = _http_json) -> dict[str, Any] | None:
    """Confirme que le port 8765 est bien JARVIS (et pas un autre logiciel)."""
    try:
        data = http_json(f"http://127.0.0.1:{port}/api/health")
    except Exception:
        return None
    if isinstance(data, dict) and data.get("ok") and "version" in data:
        return data
    return None


def _process_label(state: dict[str, Any]) -> str:
    name = state.get("name") or "processus inconnu"
    pid = state.get("pid")
    return f"{name} (PID {pid})" if pid else name


def find_llama_launcher(root: Path = ROOT,
                        override: str | os.PathLike[str] | None = None) -> Path | None:
    """Cherche un launcher llama.cpp DÉJÀ présent dans le projet.

    Ne devine jamais un chemin de GGUF : seul un script qui invoque
    réellement « llama-server » compte. `JARVIS_LLAMA_LAUNCHER` force le choix.
    """
    if override:
        path = Path(override)
        return path if path.exists() else None
    env_override = os.getenv("JARVIS_LLAMA_LAUNCHER", "").strip()
    if env_override:
        path = Path(env_override)
        return path if path.exists() else None

    skip = {".git", ".venv", "node_modules", "mac", "data", "__pycache__", "logs"}
    markers = ("llama-server", "llama_server", "llama.cpp", "llama-cli")
    try:
        candidates = list(root.glob("*.bat")) + list(root.glob("*.ps1")) + list(root.glob("*.cmd"))
        for child in root.iterdir():
            if child.is_dir() and child.name not in skip:
                for pattern in ("*.bat", "*.ps1", "*.cmd"):
                    candidates.extend(child.glob(pattern))
    except Exception:
        return None
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace").lower()
        except Exception:
            continue
        if any(marker in text for marker in markers):
            return path
    return None


def _online_providers(report: dict[str, Any]) -> list[str]:
    """Noms des fournisseurs LLM réellement connectés, d'après le Doctor."""
    names: list[str] = []
    for check in report.get("checks", []):
        if check.get("name") != "llm_server":
            continue
        providers = (check.get("details") or {}).get("providers") or []
        for provider in providers:
            if provider.get("connected"):
                label = provider.get("name") or provider.get("type") or "provider"
                if label not in names:
                    names.append(label)
    return names


def llm_state(report: dict[str, Any], port_8080: dict[str, Any],
              launcher: Path | None) -> dict[str, Any]:
    """État LLM réel : serveur local, repli provider, launcher, ou hors ligne."""
    providers = _online_providers(report)
    if port_8080.get("state") == "listening":
        return {"state": "running", "providers": providers,
                "message": "LLM SERVER ALREADY RUNNING (127.0.0.1:8080)"}
    if providers:
        return {"state": "fallback", "providers": providers,
                "message": "LLM SERVER OFFLINE (127.0.0.1:8080)\n"
                           f"LLM STATUS : AVAILABLE VIA {', '.join(providers).upper()}"}
    if launcher is not None:
        return {"state": "launchable", "providers": [], "launcher": str(launcher),
                "message": "LLM SERVER OFFLINE (127.0.0.1:8080)\n"
                           f"LAUNCHER TROUVÉ : {launcher}"}
    return {"state": "offline", "providers": [],
            "message": "LLM SERVER OFFLINE (127.0.0.1:8080)\nMANUAL START REQUIRED"}


# --------------------------------------------------------------- affichage
def _mark(check: dict[str, Any]) -> str:
    status = check.get("status")
    if status == OK:
        return "OK"
    if status == ERROR:
        return "BLOCKED" if check.get("blocking") else "DEGRADED"
    if check.get("severity") == OPTIONAL:
        return "OPTIONAL"
    return "DEGRADED"


def _line(label: str, check: dict[str, Any]) -> str:
    """Aligne le statut sans jamais coller une étiquette trop longue."""
    pad = label if len(label) > 22 else f"{label:<22}"
    return f"{pad}{_mark(check)}"


def render_preflight(report: dict[str, Any]) -> str:
    """Bloc « JARVIS STARTUP » : une ligne par contrôle, jamais un faux statut."""
    by_name = {c["name"]: c for c in report.get("checks", [])}
    shown: set[str] = set()
    lines = ["JARVIS STARTUP", "-" * 33, ""]
    for name, label in _HEADLINE:
        check = by_name.get(name)
        if check is None:
            continue
        shown.add(name)
        lines.append(_line(label, check))
    tail = [(label, by_name[name]) for name, label in _TAIL if name in by_name]
    rest = [c for n, c in by_name.items() if n not in shown
            and n not in {n for n, _ in _TAIL}]
    if tail or rest:
        lines.append("")
    for label, check in tail:
        lines.append(_line(label, check))
    for check in rest:
        lines.append(_line(str(check.get("label", check["name"]))[:22], check))
    health = report.get("health")
    verdict = "BLOCKED" if health == BLOCKED else "READY"
    lines += ["", f"Preflight: {verdict}"]
    return "\n".join(lines)


# --------------------------------------------------------------- préflight
def _run_quiet(run_doctor: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Le Core imprime sa bannière sur stdout : elle ne doit pas polluer l'écran."""
    import contextlib
    import io

    with contextlib.redirect_stdout(io.StringIO()):
        return run_doctor()


def preflight(run_doctor: Callable[[], dict[str, Any]] = full_diagnose) -> dict[str, Any]:
    report = _run_quiet(run_doctor)
    health = report.get("health")
    blocked = health == BLOCKED
    return {"report": report, "health": health, "blocked": blocked, "ready": not blocked}


# ------------------------------------------------------------------- logs
def write_log(record: dict[str, Any], log_dir: Path = LOG_DIR) -> Path:
    """Un log léger, sans aucun secret (noms et PID uniquement)."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"startup_{datetime.now():%Y-%m-%d}.log"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


# ------------------------------------------------------------- démarrage
def _launch_detached(command: list[str], cwd: Path = ROOT) -> Any:
    """Lance un exécutable externe (launcher LLM) détaché, sans jamais le tuer."""
    flags = 0
    if IS_WINDOWS:
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) \
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen(command, cwd=str(cwd), creationflags=flags)


def _run_foreground(entrypoint: Path) -> int:
    """Lance JARVIS au premier plan, dans la même console.

    Le manager a déjà mis SIGINT en SIG_IGN (voir `cli`) : le Ctrl+C qui arrête
    JARVIS ne doit pas être interprété comme un échec de démarrage.
    """
    return int(subprocess.call([sys.executable, str(entrypoint)], cwd=str(ROOT)))


def _launcher_command(path: Path) -> list[str]:
    suffix = path.suffix.lower()
    if suffix == ".ps1":
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)]
    if suffix in (".bat", ".cmd"):
        return ["cmd", "/c", str(path)]
    return [str(path)]


def start(*, run_doctor: Callable[[], dict[str, Any]] = full_diagnose,
          port_state: Callable[[int], dict[str, Any]] = _port_state,
          probe_health: Callable[[int], dict[str, Any] | None] = probe_jarvis_health,
          spawn: Callable[[list[str]], Any] = _launch_detached,
          runner: Callable[[Path], int] | None = None,
          entrypoint: Path = DEFAULT_ENTRYPOINT,
          launcher: Path | None = None,
          no_launch: bool = False,
          log_dir: Path = LOG_DIR,
          emit: Callable[[str], None] = _safe_print) -> int:
    """Prépare l'environnement puis lance JARVIS au premier plan. Renvoie 0/1."""
    report = _run_quiet(run_doctor)
    emit(render_preflight(report))
    emit("")

    ports: dict[str, Any] = {MAIN_API_PORT: port_state(MAIN_API_PORT),
                             LLM_API_PORT: port_state(LLM_API_PORT),
                             SUPERVISOR_PORT: port_state(SUPERVISOR_PORT),
                             COMFY_PORT: port_state(COMFY_PORT)}
    jarvis_port = ports[MAIN_API_PORT]

    # JARVIS tourne déjà ? On ne lance jamais une deuxième instance.
    if jarvis_port.get("state") == "listening":
        health = probe_health(MAIN_API_PORT)
        if health is not None:
            emit(f"JARVIS ALREADY RUNNING — {_process_label(jarvis_port)} "
                 f"· v{health.get('version', '?')} · http://127.0.0.1:{MAIN_API_PORT}/")
            write_log({"ts": datetime.now().isoformat(timespec="seconds"),
                       "event": "already_running", "health": report.get("health"),
                       "ports": {str(k): v.get("state") for k, v in ports.items()},
                       "result": "ok"}, log_dir)
            return 0
        # Port occupé par un AUTRE programme : on signale, on ne tue jamais.
        emit(f"PORT {MAIN_API_PORT} OCCUPÉ PAR {_process_label(jarvis_port)} — "
             f"ce n'est pas JARVIS.\nJARVIS START FAILED : conflit de port.")
        emit("Utilise JARVIS_DOCTOR.bat pour le diagnostic détaillé.")
        write_log({"ts": datetime.now().isoformat(timespec="seconds"),
                   "event": "port_conflict", "port": MAIN_API_PORT,
                   "owner": _process_label(jarvis_port), "result": "failed"}, log_dir)
        return 1

    # Serveur LLM : réutiliser ce qui existe, sinon ne pas en démarrer un second.
    found_launcher = launcher if launcher is not None else find_llama_launcher()
    llm = llm_state(report, ports[LLM_API_PORT], found_launcher)
    emit(llm["message"])
    emit("")

    if llm["state"] == "launchable" and not no_launch and found_launcher is not None:
        try:
            spawn(_launcher_command(found_launcher))
            emit("Serveur LLM lancé (détaché). JARVIS patientera le temps du chargement.")
        except Exception as exc:  # le lancement LLM ne doit jamais masquer l'erreur
            emit(f"Lancement du serveur LLM impossible : {type(exc).__name__}: {exc}")

    if report.get("health") == BLOCKED:
        emit("JARVIS START FAILED : dépendance bloquante hors service.")
        emit("Bloquant : " + ", ".join(report.get("blocking", [])))
        emit("Utilise JARVIS_DOCTOR.bat pour le diagnostic détaillé.")
        write_log({"ts": datetime.now().isoformat(timespec="seconds"),
                   "event": "blocked", "health": BLOCKED,
                   "blocking": report.get("blocking", []),
                   "llm": llm["state"], "result": "failed"}, log_dir)
        return 1

    if not entrypoint.exists():
        emit(f"JARVIS START FAILED : point d'entrée introuvable ({entrypoint}).")
        emit("Utilise JARVIS_DOCTOR.bat pour le diagnostic détaillé.")
        write_log({"ts": datetime.now().isoformat(timespec="seconds"),
                   "event": "missing_entrypoint", "entrypoint": str(entrypoint),
                   "result": "failed"}, log_dir)
        return 1

    if no_launch:
        emit(f"[--no-launch] environ prêt, JARVIS ne sera pas lancé ({entrypoint}).")
        return 0

    emit(f"Démarrage de JARVIS ({entrypoint.name})…")
    emit("")
    write_log({"ts": datetime.now().isoformat(timespec="seconds"),
               "event": "launch", "entrypoint": str(entrypoint),
               "health": report.get("health"), "llm": llm["state"],
               "degraded": report.get("degraded", []),
               "ports": {str(k): v.get("state") for k, v in ports.items()},
               "result": "started"}, log_dir)
    run = runner or _run_foreground
    started = time.time()
    try:
        code = int(run(entrypoint))
    except Exception as exc:  # jamais masquer une erreur Python
        emit(f"JARVIS START FAILED : {type(exc).__name__}: {exc}")
        emit("Utilise JARVIS_DOCTOR.bat pour le diagnostic détaillé.")
        write_log({"ts": datetime.now().isoformat(timespec="seconds"),
                   "event": "launch_exception", "error": f"{type(exc).__name__}: {exc}",
                   "llm": llm["state"], "result": "failed"}, log_dir)
        return 1

    log_path = write_log({"ts": datetime.now().isoformat(timespec="seconds"),
                          "event": "jarvis_exit", "code": code,
                          "uptime_s": round(time.time() - started, 1),
                          "health": report.get("health"), "llm": llm["state"],
                          "ports": {str(k): v.get("state") for k, v in ports.items()},
                          "result": "ok" if code == 0 else "failed"}, log_dir)
    if code != 0:
        emit("")
        emit(f"JARVIS START FAILED : JARVIS s'est arrêté avec le code {code}.")
        emit(f"Log : {log_path}")
        emit("Utilise JARVIS_DOCTOR.bat pour le diagnostic détaillé.")
        return code if code > 0 else 1
    emit("JARVIS arrêté proprement.")
    return 0


# --------------------------------------------------------------- arrêt propre
def _send_ctrl_c(pid: int) -> bool:
    """Envoie un vrai Ctrl+C à la console de JARVIS (jamais un kill forcé).

    Le PID doit avoir été confirmé comme JARVIS au préalable. On rejoint un
    instant sa console (AttachConsole), on génère CTRL_C_EVENT, puis on s'en
    détache : JARVIS reçoit le signal qu'il gère déjà (core.shutdown)."""
    if not IS_WINDOWS:
        try:
            os.kill(pid, signal.SIGINT)
            return True
        except Exception:
            return False
    import ctypes

    kernel32 = ctypes.windll.kernel32
    # Ce processus reçoit le Ctrl+C qu'il génère (il rejoint la console cible) :
    # un handler NULL le lui fait ignorer, le temps de confirmer l'arrêt.
    kernel32.SetConsoleCtrlHandler(None, True)
    kernel32.FreeConsole()
    if not kernel32.AttachConsole(pid):
        return False
    try:
        # Windows ne sait pas cibler CTRL_C sur un groupe : la console entière
        # reçoit le signal — c'est celui que JARVIS gère déjà.
        return bool(kernel32.GenerateConsoleCtrlEvent(0, 0))
    finally:
        kernel32.FreeConsole()


def stop(*, port_state: Callable[[int], dict[str, Any]] = _port_state,
         probe_health: Callable[[int], dict[str, Any] | None] = probe_jarvis_health,
         sender: Callable[[int], bool] = _send_ctrl_c,
         wait_s: float = 8.0,
         emit: Callable[[str], None] = _safe_print) -> int:
    """Arrête JARVIS proprement en visant uniquement SON processus confirmé."""
    state = port_state(MAIN_API_PORT)
    if state.get("state") != "listening":
        emit("JARVIS N'EST PAS EN COURS D'EXÉCUTION.")
        return 0
    if probe_health(MAIN_API_PORT) is None:
        emit(f"PORT {MAIN_API_PORT} OCCUPÉ PAR {_process_label(state)} — "
             "ce n'est pas JARVIS.\nAucun arrêt : rien ne sera tué.")
        return 1
    pid = state.get("pid")
    if not pid:
        emit("PID de JARVIS introuvable : arrêt annulé (aucun kill forcé).")
        return 1
    emit(f"Arrêt propre de JARVIS — {_process_label(state)}…")
    # Ce processus reçoit le Ctrl+C qu'il génère (il rejoint la console cible) :
    # il l'ignore, sinon il mourrait avant de confirmer l'arrêt.
    previous: Any = None
    try:
        previous = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except Exception:
        previous = None
    try:
        if not sender(int(pid)):
            emit("Envoi de Ctrl+C impossible : ferme la fenêtre de JARVIS ou fais Ctrl+C "
                 "(aucun kill forcé).")
            return 1
        deadline = time.time() + max(0.0, wait_s)
        while time.time() < deadline:
            if port_state(MAIN_API_PORT).get("state") != "listening":
                emit("JARVIS arrêté proprement.")
                return 0
            time.sleep(0.5)
    finally:
        if previous is not None:
            try:
                signal.signal(signal.SIGINT, previous)
            except Exception:
                pass
    emit("JARVIS n'a pas répondu à l'arrêt à temps : ferme sa fenêtre ou fais Ctrl+C "
         "(aucun kill forcé).")
    return 1


# --------------------------------------------------------------------- CLI
def cli(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    as_json = "--json" in args
    check_only = "--check" in args
    no_launch = "--no-launch" in args
    if "--stop" in args:
        return stop()

    if as_json:
        import io
        import contextlib

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            report = full_diagnose()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report.get("health") == BLOCKED else 0

    if check_only:
        result = preflight()
        _safe_print(render_preflight(result["report"]))
        return 1 if result["blocked"] else 0

    # Le Ctrl+C qui arrête JARVIS arrive aussi à ce processus (même console) :
    # on l'ignore pour laisser JARVIS gérer son arrêt et écrire le log.
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except Exception:
        pass
    return start(no_launch=no_launch)


def main(argv: list[str] | None = None) -> int:
    try:
        return cli(argv)
    except KeyboardInterrupt:
        _safe_print("\nInterrompu.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
