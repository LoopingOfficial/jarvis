"""Windows installer: isolated Python environment, required dependencies, checks."""
from pathlib import Path
import os
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent


def main():
    if sys.platform != 'win32':
        raise SystemExit('Cet installateur est destiné à Windows.')
    if sys.version_info < (3, 11):
        raise SystemExit('Python 3.11 ou plus récent est requis.')
    os.chdir(ROOT)
    python = ROOT / '.venv' / 'Scripts' / 'python.exe'
    if not python.exists():
        print("Création de l'environnement Python…", flush=True)
        venv.EnvBuilder(with_pip=True).create(ROOT / '.venv')
    subprocess.run([str(python), '-m', 'pip', 'install', '--upgrade', 'pip'], check=True)
    subprocess.run([str(python), '-m', 'pip', 'install', '-r', 'requirements.txt'], check=True)
    env = ROOT / '.env'
    if not env.exists():
        env.write_bytes((ROOT / '.env.example').read_bytes())
    subprocess.run([str(python), '-c',
        'import cryptography, psutil, paramiko, dotenv; '
        'from jarvis.secrets import MasterKey; '
        'key = MasterKey(); print("Coffre :", key.source); '
        'from jarvis.server import create_server; print("Serveur : import OK")'], check=True)
    print('Installation vérifiée. Lance run_jarvis.bat.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Installation impossible : {exc}', file=sys.stderr)
        sys.exit(1)
