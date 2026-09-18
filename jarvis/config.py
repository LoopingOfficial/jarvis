"""Chemins, valeurs par défaut et store de réglages (persisté en SQLite)."""
from __future__ import annotations

import copy
import json
import os
import platform
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("JARVIS_DATA_DIR", str(ROOT / "data"))).expanduser()
DB_PATH = DATA_DIR / "jarvis.db"
UI_DIR = ROOT / "ui"
LOG_DIR = DATA_DIR / "logs"
BACKUP_DIR = DATA_DIR / "backups"
USER_CONFIG_DIR = Path(os.getenv("JARVIS_CONFIG_DIR",
    str(Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "Jarvis")
    if platform.system() == "Windows" else "~/.config/jarvis")).expanduser()
LEGACY_CONNECTIONS = DATA_DIR / "connections.json"

IS_DARWIN = platform.system() == "Darwin"
IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"


def ensure_dirs() -> None:
    for d in (DATA_DIR, LOG_DIR, BACKUP_DIR):
        d.mkdir(parents=True, exist_ok=True)
    try:
        USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(USER_CONFIG_DIR, 0o700)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Réglages par défaut. Chaque section est modifiable depuis Settings.
# Les valeurs sensibles (clés API, mots de passe) ne vivent JAMAIS ici :
# elles sont dans le Secret Vault, référencées par connector_id.
# ---------------------------------------------------------------------------
DEFAULT_SETTINGS: dict[str, dict[str, Any]] = {
    "general": {
        "assistant_name": os.getenv("JARVIS_NAME", "JARVIS"),
        "user_name": os.getenv("JARVIS_USER_NAME", "Jérôme"),
        "language": "fr-FR",
        "timezone": "Europe/Paris",
        "default_project": os.getenv("JARVIS_DEFAULT_PROJECT", ""),
        "operator_title": "Commander",
        "location": "",
        "launch_ui_on_start": True,
    },
    "image": {
        "backend": "auto",              # ComfyUI is the image backend
        "default_mode": "auto",         # auto | fast | quality
        "fast_enabled": True,
        "quality_enabled": True,
        "fast_endpoint": "http://127.0.0.1:8188",
        "quality_endpoint": "http://127.0.0.1:8188",
        "fast_model": "z_image_turbo_bf16.safetensors",
        "quality_checkpoint": "",
        "quality_vae": "",
        "quality_txt2img_workflow": "workflows/comfyui/sdxl_quality_txt2img.json",
        "quality_img2img_workflow": "workflows/comfyui/sdxl_quality_img2img.json",
        "quality_preset": "quality_standard",
        "quality_sampler": "dpmpp_2m",
        "quality_scheduler": "karras",
        "quality_steps": 32,
        "quality_cfg": 7.0,
        "quality_width": 1024,
        "quality_height": 1024,
        "quality_timeout_s": 900,
        "default_quality": "BALANCED",  # FAST | BALANCED | HIGH
        "preferred_style": "cinematic",
        "default_width": 1024,
        "default_height": 1024,
        "default_resolution": "auto",
        "steps": 0,                     # 0 = valeur du connecteur
        "cfg": 0,
        "sampler": "",
        "scheduler": "",
        "auto_upscale": False,
        "auto_quality_check": True,
        "refine": False,
        "max_retries": 2,
        "preview_interval": 1.5,
        "preferred_model": "",
        "prefer_tools_on_creative_intent": True,
        "allow_web_fallback_on_image_intent": False,
        "auto_detect_comfy": True,      # ComfyUI local (127.0.0.1:8188) détecté sans connecteur
    },
    "blender": {
        "executable_path": "",          # vide = détection automatique
        "preferred_version": "",        # ex. "4.2" ; vide = version la plus récente
        "show_blender_ui": False,       # JARVIS travaille en --background par défaut
        "factory_startup": True,
        "render_engine": "eevee",       # eevee (aperçu) | cycles (final)
        "render_samples": 64,
        "use_gpu": True,
        "output_format": "glb",
        "default_timeout_s": 600,
        "render_timeout_s": 1200,
        "allow_run_script": True,       # blender.run_script (sandbox workspace)
        "optimize_target": "web",       # web | jarvis_avatar | none
        "auto_preview": True,
        "preview_size": 640,
    },
    "avatar": {
        "enabled": True,
        "view": "CALL",                 # CALL | FULL_BODY
        "locomotion": True,             # autorise les déplacements réels
        "walk_to_brain": True,          # se rapprocher du Brain lors d'un rappel
        "return_home": True,
        "spawn_animation": True,        # entrée en scène au démarrage
        "gesture_min_gap_s": 2.5,
        "lip_sync": True,
        "quality": "balanced",          # low | balanced | high | ultra
    },
    "appearance": {
        "theme": "midnight",
        "accent": "#22d3ee",
        "animations": True,
        "sphere": True,
        "quality": "balanced",
        "compact_sidebar": False,
        "clock_24h": True,
    },
    "voice": {
        # Fournisseur TTS : "browser" (Web Speech API) ou "macos_say"
        "tts_provider": "browser",
        "voice": "",
        # Locuteur d'une voix multi-locuteurs (ex. « pierre » pour upmc).
        # Vide = locuteur par défaut du modèle.
        "speaker": "",
        "speech_rate": 1.0,
        "pitch": 0.9,
        "volume": 1.0,
        # Expressivité 0..1 : pilote noise_scale / noise_w_scale de Piper.
        # Mesuré sur fr_FR-tom-medium, l'effet sur l'étendue de F0 et la
        # régularité du rythme reste dans le bruit de mesure — le réglage est
        # exposé parce que le moteur le supporte, pas comme gain démontré.
        "expressivity": 0.0,
        # Ne jamais prononcer les emojis : le moteur lirait leur nom Unicode.
        "speak_emojis": False,
        # Mode d'écoute : push_to_talk | always_listening | wake_word | conversation
        "mode": "wake_word",
        "wake_word": "jarvis",
        "wake_word_aliases": ["jarvice", "djarvis", "jarvi", "jarvis."],
        "wake_word_sensitivity": 0.6,
        "wake_ack": "Oui ?",
        "wake_ack_enabled": True,
        "conversation_window_s": 45,
        "silence_timeout_s": 6,
        "interruptible_speech": True,
        "stop_words": ["stop", "arrête", "arrete", "tais-toi", "silence", "annule"],
        # Greeting : UNIQUEMENT au démarrage d'une vraie session utilisateur.
        # Fréquence verrouillée à "once_per_session" : aucune autre valeur n'est acceptée.
        "greeting_enabled": True,
        "greeting_text": "Bonjour {user}.",
        "greeting_frequency": "once_per_session",
        "session_idle_reset_hours": 8,
        "speak_responses": True,
        "speak_notifications": False,
        "clap_enabled": True,
        "stt_language": "fr-FR",
    },
    "ai": {
        # Références "connector_id:model". Vide → auto (premier provider connecté).
        "default_model": "",
        "fast_model": "",
        "reasoning_model": "",
        "coding_model": "",
        "blender_model": "",
        "fallback_model": "",
        "temperature": 0.3,
        "max_tool_iterations": 12,
        "max_context_messages": 24,
        "auto_fallback": True,
        "embedding_model": "",
        "system_prompt_extra": "",
    },
    "memory": {
        "auto_extract": True,
        "max_context_memories": 12,
        "semantic_search": True,
        "min_importance_for_context": 2,
    },
    "learning": {
        "enabled": True, "idle_after_s": 600, "resource_mode": "balanced",
        "allowed_hours": "00:00-23:59", "max_duration_s": 1200,
        "max_pages": 4, "max_searches": 3, "max_new_knowledge": 3,
        "web_enabled": True, "local_docs_enabled": True, "daily_report": False,
    },
    "files": {"default_scope": "auto"},
    "security": {
        "confirm_sensitive": True,
        "confirm_destructive": True,
        "confirmation_timeout_s": 600,
        "allowed_shell": True,
        "shell_timeout_s": 120,
        "filesystem_roots": ["~"],
        "audit_retention_days": 90,
        "mask_secrets_in_logs": True,
    },
    "automation": {
        "scheduler_enabled": True,
        "scheduler_tick_s": 15,
        "n8n_connector_id": "",
        "max_concurrent_tasks": 3,
        "task_retention_days": 30,
    },
    "background": {
        "enabled": True,
        "max_background_tasks": 4,
        "task_retention_days": 30,
    },
    "notifications": {
        "desktop": True,
        "feed": True,
        "speak_important": False,
        "levels": ["info", "warn", "error", "live", "tip"],
    },
    "files": {
        # Pièces jointes envoyées à JARVIS depuis la command bar.
        "max_upload_size_mb": 25,
        "max_image_size_mb": 15,
        "max_attachments_per_message": 6,
        "attachment_ttl_hours": 24,
        "text_context_chars": 30000,
        "pdf_page_context": 25,
    },
    "developer": {
        "debug": False,
        "log_level": "INFO",
        "event_history": 300,
        "expose_api_docs": True,
    },
    "editor": {
        "minimap": True,
        "wordWrap": False,
        "fontSize": 13,
        "fontFamily": "Cascadia Code",
    },
    # Agent éditorial du blog (JARVIS_BLOG_PUBLISHER_V1).
    # `auto_publish` reste faux par défaut : la publication publique est une
    # décision, jamais un réglage qu'on active par inadvertance.
    "blog": {
        "discord_channel_id": "",
        "auto_editorial": False,
        "auto_mode": "AUTO_DRAFTS",       # AUTO_DRAFTS | AUTO_REVIEW | FULL_AUTO
        "auto_research": True,
        "auto_drafts": True,
        "auto_publish": False,
        "auto_discord": False,
        "ssh_connector_id": "ssh",
        "default_category": "actualites",
    },
    "self_upgrade": {
        "enabled": True,
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11434",
        "orchestrator_model": "jarvis-astra",
        "coder_model": "qwen2.5-coder:7b-instruct-q4_K_M",
        "candidate_port": 8791,
        "supervisor_url": "http://127.0.0.1:8770",
        "main_port": 8765,
        "python": "",
        "max_attempts": 2,
    },
}

# Les clés ci-dessous sont verrouillées : Settings ne peut pas les modifier
# (anti-régression : le greeting ne doit jamais dépendre d'un timer).
LOCKED_SETTINGS = {("voice", "greeting_frequency"): "once_per_session"}

_lock = threading.RLock()


class SettingsStore:
    """Réglages persistés dans la table `settings` (une ligne par section)."""

    def __init__(self, db) -> None:
        self._db = db
        self._cache: dict[str, dict[str, Any]] | None = None

    def _load(self) -> dict[str, dict[str, Any]]:
        with _lock:
            if self._cache is not None:
                return self._cache
            data = copy.deepcopy(DEFAULT_SETTINGS)
            for row in self._db.query("SELECT key, value FROM settings"):
                try:
                    stored = json.loads(row["value"])
                except Exception:
                    continue
                if row["key"] in data and isinstance(stored, dict):
                    data[row["key"]].update(stored)
            for (section, key), value in LOCKED_SETTINGS.items():
                data[section][key] = value
            self._cache = data
            return data

    def all(self) -> dict[str, dict[str, Any]]:
        return copy.deepcopy(self._load())

    def section(self, name: str) -> dict[str, Any]:
        return copy.deepcopy(self._load().get(name, {}))

    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self._load().get(section, {}).get(key, default)

    def update(self, section: str, values: dict[str, Any]) -> dict[str, Any]:
        if section not in DEFAULT_SETTINGS:
            raise ValueError(f"Section de réglages inconnue: {section}")
        with _lock:
            current = self._load()[section]
            allowed = {k: v for k, v in values.items() if k in DEFAULT_SETTINGS[section]}
            for (sec, key), locked in LOCKED_SETTINGS.items():
                if sec == section and key in allowed:
                    allowed[key] = locked
            current.update(allowed)
            self._db.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (section, json.dumps(current, ensure_ascii=False), time.time()),
            )
            return copy.deepcopy(current)

    def invalidate(self) -> None:
        with _lock:
            self._cache = None


def public_env_summary() -> dict[str, Any]:
    return {
        "root": str(ROOT),
        "data_dir": str(DATA_DIR),
        "db": str(DB_PATH),
        "platform": platform.platform(),
        "python": platform.python_version(),
    }
