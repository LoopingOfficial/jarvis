#!/usr/bin/env python3
"""Point d'entrée JARVIS — serveur local + interface Command Center."""
from __future__ import annotations

import os
import signal
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:  # python-dotenv est optionnel
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

from jarvis import __version__            # noqa: E402
from jarvis.core import JarvisCore        # noqa: E402
from jarvis.server import create_server   # noqa: E402


def _quick_diagnosis(core) -> str:
    """Une ligne d'état des dépendances locales, sans sonde réseau au démarrage."""
    try:
        from jarvis.doctor import FAST_CHECKS, diagnose

        report = diagnose(core, list(FAST_CHECKS))
    except Exception:
        return "python jarvis.py doctor"
    missing = [c["label"] for c in report["checks"] if not c["ok"]]
    if not missing:
        return "dépendances locales OK · python jarvis.py doctor pour le détail"
    return f"{', '.join(missing)} — corrige avec : python jarvis.py doctor --repair"


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    # `jarvis.py doctor [--repair] [--json]` : diagnostic d'installation, sans
    # démarrer le serveur ni l'interface.
    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        from jarvis.doctor import cli as doctor_cli

        sys.argv = [sys.argv[0], *sys.argv[2:]]
        doctor_cli()
        return 0
    host = os.getenv("JARVIS_HOST", "127.0.0.1")
    port = int(os.getenv("JARVIS_PORT", "8765"))

    core = JarvisCore()
    try:
        server = create_server(core, host, port)
    except OSError as exc:
        print(f"Impossible de démarrer JARVIS sur {host}:{port} — {exc}", file=sys.stderr)
        print("Un autre JARVIS tourne peut-être déjà, ou change JARVIS_PORT dans .env.", file=sys.stderr)
        return 1

    core.start_background()
    stopping = threading.Event()

    def shutdown(*_args) -> None:
        if stopping.is_set():
            return
        stopping.set()
        print("\nArrêt de JARVIS…", flush=True)
        core.shutdown()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    status = core.status()
    connected = [p for p in status["llm"] if p["connected"]]
    print("\n" + "═" * 66)
    print(f"  JARVIS Command Center  v{__version__}")
    print(f"  Interface   : http://{host}:{port}/")
    print(f"  Coffre      : {core.vault.backend}")
    print(f"  Outils      : {status['tools']['total']}  ·  Agents : {len(status['agents'])}")
    print(f"  Connecteurs : {status['connectors']['total']}")
    print(f"  Modèles     : {', '.join(p['name'] for p in connected) if connected else 'aucun (Settings → AI Providers)'}")
    print(f"  Mémoire     : {status['memory']['total']} souvenirs")
    print(f"  Diagnostic  : {_quick_diagnosis(core)}")
    print("  Ctrl+C pour arrêter")
    print("═" * 66 + "\n")

    threading.Timer(1.0, lambda: core.launch_ui(port)).start()
    try:
        server.serve_forever(poll_interval=0.3)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
