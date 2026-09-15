#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
  echo "Environnement absent. Lance d'abord ./install_mac.sh"
  exit 1
fi

exec .venv/bin/python jarvis.py "$@"
