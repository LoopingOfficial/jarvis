#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.11 ou plus récent est requis."
  echo "Installe-le avec Homebrew: brew install python@3.11"
  exit 1
fi

python3 setup_mac.py
