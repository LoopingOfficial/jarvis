"""Diagnostic d'installation — ce qui marche, ce qui manque, comment le réparer.

JARVIS dépend de briques externes (Blender, ComfyUI, Ollama, Piper, Playwright,
Whisper…). Jusqu'ici, leur absence se manifestait par un message d'erreur isolé
au moment d'appeler l'outil concerné, sans vue d'ensemble ni marche à suivre.

Ce module fait deux choses, et rien de plus :

  * `diagnose()` mesure l'état réel de chaque brique (aucune valeur supposée) ;
  * `repair()` n'installe que ce qui s'installe sans décision humaine — les
    paquets Python et les voix Piper. Tout ce qui demande un choix (quel modèle
    LLM, quel checkpoint SDXL, quelle carte) est signalé, jamais décidé ici.

Usage : `python -m jarvis.doctor` ou `python -m jarvis.doctor --repair`.
"""
from __future__ import annotations

import importlib.util
import json
import socket
import subprocess
import sys
from typing import Any, Callable

from .config import DATA_DIR

# Gravité d'un point de contrôle en échec.
BLOCKING = "blocking"      # une fonctionnalité entière est hors service
DEGRADED = "degraded"      # marche, mais amputé
OPTIONAL = "optional"      # confort


def _module(name: str) -> bool:
    """True si le module est importable, sans l'importer réellement."""
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _check(name: str, label: str, ok: bool, *, severity: str = DEGRADED,
           detail: str = "", fix: str = "", auto: bool = False) -> dict[str, Any]:
    return {"name": name, "label": label, "ok": bool(ok), "severity": severity,
            "detail": detail, "fix": fix if not ok else "",
            "auto_fixable": bool(auto and not ok)}


# ---------------------------------------------------------------- contrôles
def _check_llm(core) -> dict[str, Any]:
    """Un modèle doit être joignable, sinon JARVIS ne raisonne pas du tout."""
    configured = str(core.settings.get("ai", "default_model", "") or "").strip()
    online: list[str] = []
    try:
        for status in core.llm.status(max_age=0, blocking=True):
            if status.get("connected"):
                online.extend(status.get("models") or [])
    except Exception as exc:
        return _check("llm", "Modèle de langage", False, severity=BLOCKING,
                      detail=f"Sonde impossible : {exc}",
                      fix="Démarre Ollama (ollama serve) ou ajoute une clé API "
                          "dans Réglages → IA.")
    if online:
        return _check("llm", "Modèle de langage", True, severity=BLOCKING,
                      detail=f"{len(online)} modèle(s) disponible(s)"
                             + (f", défaut « {configured} »" if configured
                                else " — aucun modèle par défaut choisi"))
    ollama = _port_open("127.0.0.1", 11434)
    return _check("llm", "Modèle de langage", False, severity=BLOCKING,
                  detail=("Ollama répond mais n'a aucun modèle." if ollama
                          else "Aucun fournisseur joignable."),
                  fix=("ollama pull qwen2.5:7b" if ollama else
                       "Installe Ollama (ollama.com) puis « ollama pull qwen2.5:7b », "
                       "ou renseigne une clé API dans Réglages → IA."))


def _check_blender(core) -> dict[str, Any]:
    try:
        detection = core.blender.detect()
    except Exception as exc:
        return _check("blender", "Blender", False, detail=f"Détection impossible : {exc}",
                      fix="Installe Blender puis renseigne Réglages → Blender → "
                          "executable_path.")
    if detection.get("installed"):
        return _check("blender", "Blender", True,
                      detail=f"{detection.get('version', '?')} — "
                             f"{detection.get('executable_path', '')}")
    return _check("blender", "Blender", False,
                  detail="Aucune installation trouvée (PATH, Program Files, Steam).",
                  fix="Installe Blender depuis blender.org, ou renseigne le chemin "
                      "exact dans Réglages → Blender → executable_path.")


def _check_comfyui(core) -> dict[str, Any]:
    from .comfyui_detect import detect_comfy_image_engines

    base = str(core.settings.get("image", "comfy_url", "") or "http://127.0.0.1:8188")
    try:
        result = detect_comfy_image_engines(base)
    except Exception as exc:
        return _check("comfyui", "ComfyUI", False, detail=str(exc),
                      fix="Lance ComfyUI sur 127.0.0.1:8188.")
    if not result.get("reachable"):
        return _check("comfyui", "ComfyUI", False, severity=BLOCKING,
                      detail=f"Injoignable sur {base} — toute génération d'image échouera.",
                      fix="Démarre ComfyUI (run_nvidia_gpu.bat) et vérifie le port 8188.")
    engines = result.get("engines") or []
    if not engines:
        return _check("comfyui", "ComfyUI", False,
                      detail="En ligne, mais aucun checkpoint exploitable détecté.",
                      fix="Place un checkpoint SDXL dans ComfyUI/models/checkpoints.")
    return _check("comfyui", "ComfyUI", True,
                  detail=f"{len(engines)} moteur(s) : "
                         + ", ".join(str(e.get("id", "?")) for e in engines[:4]))


def _check_piper(core) -> dict[str, Any]:
    engine = core.tts.engine_status()
    # Le repli Edge ne rend pas le contrôle vert : il dépanne la voix, mais il
    # n'est pas local. Il est seulement signalé pour éviter de croire JARVIS muet.
    fallback = (" Repli edge-tts actif (voix non locale)."
                if core.tts_fallback.available() else "")
    if not engine.get("available"):
        return _check("piper", "Voix locale (Piper)", False,
                      detail="Moteur Piper absent." + fallback,
                      fix="pip install piper-tts", auto=True)
    if not core.tts.installed():
        return _check("piper", "Voix locale (Piper)", False,
                      detail="Moteur présent, aucune voix française installée." + fallback,
                      fix="python -m jarvis.tts install", auto=True)
    return _check("piper", "Voix locale (Piper)", True, detail=core.tts.summary())


def _check_stt(core) -> dict[str, Any]:
    status = core.stt.status()
    provider = str(core.settings.get("voice", "stt_provider", "browser") or "browser")
    if status.get("available"):
        cached = "" if status.get("model_cached") else " (poids non encore téléchargés)"
        return _check("stt", "Dictée serveur", True,
                      detail=f"faster-whisper, modèle « {status.get('model')} »{cached}")
    return _check("stt", "Dictée serveur", False,
                  severity=BLOCKING if provider == "local" else OPTIONAL,
                  detail="Sans elle, la dictée dépend de la Web Speech API "
                         "(absente de Firefox, distante dans Chrome).",
                  fix="pip install faster-whisper", auto=True)


def _check_playwright(core) -> dict[str, Any]:
    if not _module("playwright"):
        return _check("playwright", "Automatisation navigateur", False,
                      detail="Pilotage web, rendu PDF des factures et du CRM indisponibles.",
                      fix="pip install playwright && python -m playwright install chromium",
                      auto=True)
    # Le paquet ne suffit pas : le navigateur doit aussi avoir été téléchargé.
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "--dry-run", "chromium"],
            capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        missing = "not installed" in (proc.stdout + proc.stderr).lower()
    except Exception:
        missing = False
    if missing:
        return _check("playwright", "Automatisation navigateur", False,
                      detail="Paquet présent, navigateur Chromium non téléchargé.",
                      fix="python -m playwright install chromium", auto=True)
    return _check("playwright", "Automatisation navigateur", True,
                  detail="playwright + chromium")


def _check_discord(core) -> dict[str, Any]:
    if _module("discord"):
        return _check("discord", "Bot Discord", True, detail="discord.py présent")
    return _check("discord", "Bot Discord", False, severity=OPTIONAL,
                  detail="Bot et notifications blog→Discord inactifs.",
                  fix="pip install discord.py", auto=True)


def _check_vault(core) -> dict[str, Any]:
    if not _module("cryptography"):
        return _check("vault", "Coffre à secrets", False, severity=BLOCKING,
                      detail="Sans « cryptography », les secrets chiffrés ne se déchiffrent pas.",
                      fix="pip install cryptography", auto=True)
    try:
        from .secrets import MasterKey

        source = MasterKey().source
    except Exception as exc:
        return _check("vault", "Coffre à secrets", False, severity=BLOCKING,
                      detail=f"Clé maîtresse illisible : {exc}",
                      fix="Vérifie JARVIS_MASTER_KEY dans .env, ou le gestionnaire "
                          "d'identifiants Windows.")
    return _check("vault", "Coffre à secrets", True, detail=f"clé maîtresse : {source}")


def _check_gpu(core) -> dict[str, Any]:
    from .gpu_manager import GpuResourceManager

    vram = GpuResourceManager.vram()
    if not vram:
        return _check("gpu", "Mesure VRAM", False, severity=OPTIONAL,
                      detail="Aucune sonde n'a répondu : l'arbitrage VRAM entre Ollama "
                             "et Blender est désactivé (aucune décision inventée).",
                      fix="NVIDIA : installe les pilotes (nvidia-smi). AMD : rocm-smi. "
                          "Sinon, les compteurs de performance Windows doivent être actifs.")
    return _check("gpu", "Mesure VRAM", True,
                  detail=f"{vram.get('vendor', '?')} — {vram['free_mb']} / "
                         f"{vram['total_mb']} Mo libres (source : {vram.get('source', '?')})")


def _check_supervisor(core) -> dict[str, Any]:
    from .self_upgrade.service import DEFAULT_SUPERVISOR_URL
    from .self_upgrade.supervisor_client import SupervisorClient

    url = DEFAULT_SUPERVISOR_URL
    try:
        url = str(core.self_upgrade.config().get("supervisor_url", url) or url)
    except Exception:
        pass
    if SupervisorClient(url).ping():
        return _check("supervisor", "Supervisor (auto-mise à jour)", True,
                      detail=f"en ligne — {url}")
    return _check("supervisor", "Supervisor (auto-mise à jour)", False,
                  detail=f"Injoignable sur {url} : une mise à jour ne pourrait pas "
                         "être annulée.",
                  fix="Lance supervisor\\run_supervisor.bat (service séparé, par conception).")


def _check_clap(core) -> dict[str, Any]:
    enabled = bool(core.settings.get("voice", "clap_enabled", False))
    missing = [name for name in ("sounddevice", "numpy") if not _module(name)]
    if missing:
        return _check("clap", "Réveil au double clap", False, severity=OPTIONAL,
                      detail="Dépendances audio absentes : " + ", ".join(missing),
                      fix="pip install -r requirements-audio.txt", auto=True)
    return _check("clap", "Réveil au double clap", True,
                  detail="dépendances présentes"
                         + ("" if enabled else ", désactivé dans les réglages"))


def _check_n8n(core) -> dict[str, Any]:
    try:
        rows = [c for c in core.connectors.list() if c.get("type") == "n8n"]
    except Exception as exc:
        return _check("n8n", "Connecteur n8n", False, severity=OPTIONAL,
                      detail=f"Liste des connecteurs illisible : {exc}",
                      fix="Vérifie la base de données locale (data/jarvis.db) "
                          "et Réglages → Connecteurs.")
    if not rows:
        return _check("n8n", "Connecteur n8n", False, severity=OPTIONAL,
                      detail="Aucune instance n8n déclarée : aucune automatisation "
                             "externe branchée.",
                      fix="Réglages → Connecteurs → n8n : renseigne l'URL de l'instance.")
    return _check("n8n", "Connecteur n8n", True, detail=f"{len(rows)} instance(s) déclarée(s)")


def _check_vision(core) -> dict[str, Any]:
    """L'évaluation du rendu avatar exige un modèle multimodal réellement branché."""
    models: list[str] = []
    try:
        for status in core.llm.status(max_age=30):
            if status.get("connected"):
                models.extend(status.get("models") or [])
    except Exception:
        models = []
    hints = ("llava", "vision", "-vl", "minicpm", "moondream", "gemma3", "pixtral")
    found = [m for m in models if any(h in str(m).lower() for h in hints)]
    if found:
        return _check("vision", "Évaluation visuelle (avatar)", True,
                      detail="modèle(s) vision : " + ", ".join(found[:3]))
    return _check("vision", "Évaluation visuelle (avatar)", False, severity=OPTIONAL,
                  detail="Sans modèle de vision, le rendu avatar ne peut pas être noté "
                         "automatiquement.",
                  fix="ollama pull llava:13b (ou un autre modèle multimodal).")


CHECKS: tuple[tuple[str, Callable[[Any], dict[str, Any]]], ...] = (
    ("llm", _check_llm),
    ("vault", _check_vault),
    ("blender", _check_blender),
    ("comfyui", _check_comfyui),
    ("piper", _check_piper),
    ("stt", _check_stt),
    ("playwright", _check_playwright),
    ("gpu", _check_gpu),
    ("supervisor", _check_supervisor),
    ("discord", _check_discord),
    ("clap", _check_clap),
    ("n8n", _check_n8n),
    ("vision", _check_vision),
)


# Contrôles sans accès réseau ni sous-processus : utilisables dans la bannière
# de démarrage, où l'on ne peut pas se permettre d'attendre.
FAST_CHECKS = ("vault", "stt", "discord", "clap", "gpu")


def diagnose(core, only: list[str] | None = None) -> dict[str, Any]:
    """État réel de chaque brique. Ne modifie rien, ne lève jamais."""
    wanted = set(only or [])
    results = []
    for name, probe in CHECKS:
        if wanted and name not in wanted:
            continue
        try:
            results.append(probe(core))
        except Exception as exc:  # une sonde cassée ne doit pas masquer les autres
            results.append(_check(name, name, False, detail=f"Sonde en échec : {exc}"))
    failing = [r for r in results if not r["ok"]]
    return {
        "checks": results,
        "ok": not failing,
        "blocking": [r["name"] for r in failing if r["severity"] == BLOCKING],
        "auto_fixable": [r["name"] for r in failing if r["auto_fixable"]],
        "summary": f"{len(results) - len(failing)}/{len(results)} briques opérationnelles",
        "data_dir": str(DATA_DIR),
    }


# ------------------------------------------------------------- réparations
# Une réparation = une commande non interactive, sans choix humain à faire.
REPAIRS: dict[str, list[list[str]]] = {
    "piper": [[sys.executable, "-m", "pip", "install", "piper-tts"],
              [sys.executable, "-m", "jarvis.tts", "install"]],
    "stt": [[sys.executable, "-m", "pip", "install", "faster-whisper"]],
    "playwright": [[sys.executable, "-m", "pip", "install", "playwright"],
                   [sys.executable, "-m", "playwright", "install", "chromium"]],
    "discord": [[sys.executable, "-m", "pip", "install", "discord.py"]],
    "vault": [[sys.executable, "-m", "pip", "install", "cryptography"]],
    "clap": [[sys.executable, "-m", "pip", "install", "-r", "requirements-audio.txt"]],
}


def repair(core, names: list[str] | None = None) -> dict[str, Any]:
    """Installe ce qui s'installe seul. Retourne le détail de chaque commande."""
    report = diagnose(core)
    targets = [n for n in (names or report["auto_fixable"]) if n in REPAIRS]
    steps: list[dict[str, Any]] = []
    for name in targets:
        for command in REPAIRS[name]:
            try:
                proc = subprocess.run(
                    command, capture_output=True, text=True, timeout=900,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-3:]
                steps.append({"check": name, "command": " ".join(command[1:]),
                              "ok": proc.returncode == 0, "output": "\n".join(tail)})
                if proc.returncode != 0:
                    break  # inutile d'enchaîner sur une étape dont la précédente a échoué
            except Exception as exc:
                steps.append({"check": name, "command": " ".join(command[1:]),
                              "ok": False, "output": str(exc)})
                break
    return {"steps": steps, "attempted": targets,
            "after": diagnose(core, only=targets) if targets else report}


# --------------------------------------------------------------------- CLI
def _render(report: dict[str, Any]) -> str:
    lines = [report["summary"], ""]
    for check in report["checks"]:
        mark = "OK    " if check["ok"] else "MANQUE"
        lines.append(f"[{mark}] {check['label']} — {check['detail']}")
        if check["fix"]:
            lines.append(f"         → {check['fix']}")
    return "\n".join(lines)


def _print(text: str) -> None:
    """Sortie console tolérante : un terminal cp1252 ne doit pas faire planter."""
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "ascii"
        print(text.encode(encoding, "replace").decode(encoding, "replace"))


def cli() -> None:
    args = sys.argv[1:]
    as_json = "--json" in args
    names = [a for a in args if not a.startswith("-")] or None

    from .core import JarvisCore

    core = JarvisCore()
    if "--repair" in args:
        result = repair(core, names)
        _print(json.dumps(result, ensure_ascii=False, indent=2) if as_json
               else _render(result["after"]))
        sys.exit(0 if all(step["ok"] for step in result["steps"]) else 1)
    report = diagnose(core, names)
    _print(json.dumps(report, ensure_ascii=False, indent=2) if as_json else _render(report))
    sys.exit(0 if not report["blocking"] else 1)


if __name__ == "__main__":
    cli()
