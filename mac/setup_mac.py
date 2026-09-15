#!/usr/bin/env python3
"""Installateur macOS pour JARVIS dans un environnement Python isolé."""
from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "bin" / "python"


def run(*args: str) -> None:
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("Cet installateur est destiné à macOS.")
    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11 ou plus récent est requis.")

    os.chdir(ROOT)
    if not PYTHON.exists():
        print("Création de l'environnement Python…", flush=True)
        venv.EnvBuilder(with_pip=True).create(ROOT / ".venv")

    run(PYTHON, "-m", "pip", "install", "--upgrade", "pip")
    run(PYTHON, "-m", "pip", "install", "-r", "requirements.txt")

    env_file = ROOT / ".env"
    if not env_file.exists():
        env_file.write_bytes((ROOT / ".env.example").read_bytes())

    run(PYTHON, "-c", "import cryptography, psutil, paramiko, dotenv; "
        "from jarvis.secrets import MasterKey; "
        "key = MasterKey(); print('Coffre :', key.source); "
        "from jarvis.server import create_server; print('Serveur : import OK')")
    print("Installation macOS vérifiée. Lance ./run_jarvis.sh.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Installation impossible : {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
