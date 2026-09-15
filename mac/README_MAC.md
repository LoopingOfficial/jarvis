# JARVIS pour macOS

Cette distribution contient la même interface Command Center, les mêmes agents,
connecteurs, outils, workflows ComfyUI, avatar holographique et diagnostics que
la version Windows. Les données locales et les secrets ne sont pas copiés.

## Installation

Prérequis: macOS, Python 3.11+, et idéalement Homebrew pour les outils externes.

```bash
cd mac
chmod +x install_mac.sh run_jarvis.sh
./install_mac.sh
```

L'installation crée `mac/.venv`, installe `requirements.txt` et initialise la
clé du coffre dans le Trousseau macOS. Elle crée aussi `.env` depuis
`.env.example` si nécessaire.

## Lancement

```bash
./run_jarvis.sh
```

L'interface est disponible sur `http://127.0.0.1:8765/` et s'ouvre dans Chrome
si celui-ci est installé. Pour diagnostiquer les dépendances:

```bash
.venv/bin/python jarvis.py doctor
```

## Options macOS

- Le coffre utilise le Trousseau macOS via la commande `security`.
- Les applications sont ouvertes avec `open` et les notifications avec AppleScript.
- Le terminal local utilise le shell macOS, pas `cmd.exe`.
- Blender est détecté dans `/Applications` et `~/Applications`.
- Ollama, ComfyUI, Piper, FFmpeg et Playwright restent des dépendances externes
  à installer selon les fonctionnalités utilisées.

Les réglages et données produits sur le Mac restent dans `mac/data` et ne sont
pas transférés automatiquement depuis Windows.
