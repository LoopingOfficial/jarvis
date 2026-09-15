# JARVIS pour macOS

Version macOS complète de JARVIS Command Center. Elle inclut la même interface,
les agents, les connecteurs, les outils locaux, les workflows ComfyUI, l’avatar
holographique et les diagnostics que la version Windows.

## Installation

Pré-requis: macOS et Python 3.11 ou plus récent.

```bash
chmod +x install_mac.sh run_jarvis.sh
./install_mac.sh
```

## Lancement

```bash
./run_jarvis.sh
```

L’interface est disponible sur `http://127.0.0.1:8765/`. Le navigateur Chrome
est utilisé en fenêtre d’application lorsqu’il est installé.

Pour le diagnostic:

```bash
.venv/bin/python jarvis.py doctor
```

Le coffre utilise le Trousseau macOS, les applications sont lancées avec
`open`, les notifications avec AppleScript et le terminal local utilise le
shell macOS. Ollama, ComfyUI, Blender, Piper, FFmpeg et Playwright restent des
dépendances externes installées selon les fonctions utilisées.

Les fichiers `.env`, `data`, `state` et `.venv` sont locaux à cette distribution
et ne sont pas copiés depuis Windows.

## Voix

Les voix Piper françaises locales sont incluses dans `data/voices/fr`. JARVIS
les utilise en priorité. Le repli réseau `edge-tts` est également intégré dans
`jarvis/tts_edge.py` avec la voix masculine `fr-FR-HenriNeural` par défaut.

Pour l'activer avec la synthèse MP3 asynchrone:

```bash
.venv/bin/python -m pip install -r requirements-audio.txt
```

Le texte envoyé à `edge-tts` transite par les serveurs Microsoft; Piper reste
le moteur recommandé lorsque la voix locale est disponible.
