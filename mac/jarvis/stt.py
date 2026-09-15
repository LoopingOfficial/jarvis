"""Reconnaissance vocale côté serveur — repli quand le navigateur ne peut pas.

Pourquoi ce module
------------------
Jusqu'ici la dictée reposait uniquement sur la Web Speech API du navigateur :
elle n'existe pas dans Firefox, elle dépend d'un service distant dans Chrome, et
elle disparaît dès que l'onglet perd le focus. Ce module transcrit localement,
sur la machine, avec `faster-whisper` (CTranslate2) quand il est installé.

Rien n'est simulé : si `faster-whisper` est absent ou si le modèle n'a pas été
téléchargé, `available()` est False et `transcribe()` renvoie une erreur
explicite — jamais un texte inventé.
"""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from .config import DATA_DIR

MODELS_DIR = DATA_DIR / "stt"

# Compromis mesuré sur CPU : `small` transcrit du français propre en restant
# sous la seconde par phrase courte ; `base` est deux fois plus rapide mais
# confond les homophones usuels.
DEFAULT_MODEL = "small"
SUPPORTED_MODELS = ("tiny", "base", "small", "medium", "large-v3")

_lock = threading.Lock()
_loaded: dict[str, Any] = {}
_devices_used: dict[str, str] = {}


def backend_available() -> bool:
    """True si le paquet `faster-whisper` est importable."""
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        return False
    return True


def model_cached(model: str = "") -> bool:
    """True si les poids du modèle sont déjà sur le disque (pas de réseau)."""
    name = (model or DEFAULT_MODEL).strip()
    root = MODELS_DIR
    if not root.exists():
        return False
    marker = f"faster-whisper-{name}"
    for entry in root.rglob("*"):
        if entry.is_file() and entry.name.endswith(".bin") and marker in str(entry).replace("\\", "/"):
            return True
    return False


class SpeechRecognizer:
    """Transcription locale. Une instance par cœur applicatif."""

    def __init__(self, settings=None) -> None:
        self._settings = settings

    # ------------------------------------------------------------- réglages
    def _model_name(self) -> str:
        if self._settings is None:
            return DEFAULT_MODEL
        name = str(self._settings.get("voice", "stt_model", "") or "").strip()
        return name if name in SUPPORTED_MODELS else DEFAULT_MODEL

    def _language(self) -> str:
        """Code Whisper (« fr ») déduit du tag BCP-47 des réglages (« fr-FR »)."""
        if self._settings is None:
            return "fr"
        tag = str(self._settings.get("voice", "stt_language", "fr-FR") or "").strip()
        return (tag.split("-")[0].lower() or "fr")

    def _devices(self) -> list[tuple[str, str]]:
        """Cibles (device, compute_type) à essayer, de la plus rapide à la plus sûre.

        Une carte NVIDIA présente ne suffit pas : CTranslate2 exige cuBLAS et
        cuDNN, absents d'une installation Python nue. On ne le sait qu'en
        essayant réellement, d'où la validation dans `_load`.
        """
        from .gpu_manager import GpuResourceManager

        vram = GpuResourceManager.vram()
        cpu = ("cpu", "int8")
        if vram and vram.get("vendor") == "nvidia" and vram.get("free_mb", 0) >= 1500:
            return [("cuda", "float16"), cpu]
        return [cpu]

    # -------------------------------------------------------------- statut
    def available(self) -> bool:
        return backend_available()

    def status(self) -> dict[str, Any]:
        model = self._model_name()
        installed = backend_available()
        return {
            "available": installed,
            "backend": "faster-whisper",
            "model": model,
            "model_cached": model_cached(model) if installed else False,
            "models_dir": str(MODELS_DIR),
            "language": self._language(),
            "loaded": sorted(_loaded),
            "device": _devices_used.get(model, ""),
            "hint": ("" if installed else
                     "Installe le moteur local : pip install faster-whisper"),
        }

    # --------------------------------------------------------------- moteur
    def _load(self):
        model = self._model_name()
        with _lock:
            if model in _loaded:
                return _loaded[model]
            from faster_whisper import WhisperModel

            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            last: Exception | None = None
            for device, compute_type in self._devices():
                try:
                    engine = WhisperModel(model, device=device, compute_type=compute_type,
                                          download_root=str(MODELS_DIR))
                    self._validate(engine)
                except Exception as exc:
                    # cuBLAS/cuDNN manquants : on retombe sur le CPU plutôt que
                    # de laisser exploser la première phrase de l'utilisateur.
                    last = exc
                    continue
                _loaded[model] = engine
                _devices_used[model] = device
                return engine
            raise RuntimeError(
                "Moteur de dictée inutilisable sur cette machine : "
                f"{last}")

    @staticmethod
    def _validate(engine) -> None:
        """Encode un bref silence : seul moyen de prouver que le device marche."""
        import numpy

        silence = numpy.zeros(16000 // 2, dtype=numpy.float32)
        for _ in engine.transcribe(silence, language="fr", beam_size=1)[0]:
            break

    def warmup(self) -> bool:
        """Charge le modèle à l'avance. False si le moteur est absent."""
        if not backend_available():
            return False
        try:
            self._load()
        except Exception:
            return False
        return True

    def transcribe(self, audio: bytes, *, language: str = "",
                   suffix: str = ".webm") -> dict[str, Any]:
        """Transcrit un enregistrement. `audio` = octets du fichier tel quel.

        Retourne {'text', 'language', 'duration', 'segments'} ou lève RuntimeError
        avec un message exploitable.
        """
        if not audio:
            raise RuntimeError("Aucun audio reçu.")
        if not backend_available():
            raise RuntimeError(
                "Reconnaissance vocale serveur indisponible : "
                "installe le moteur local (pip install faster-whisper).")
        engine = self._load()
        handle, path = tempfile.mkstemp(suffix=suffix or ".webm")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(audio)
            segments, info = engine.transcribe(
                path, language=(language or self._language()) or None,
                vad_filter=True, beam_size=5)
            parts = []
            for segment in segments:
                text = (segment.text or "").strip()
                if text:
                    parts.append({"start": round(float(segment.start), 2),
                                  "end": round(float(segment.end), 2),
                                  "text": text})
        finally:
            try:
                Path(path).unlink()
            except Exception:
                pass
        return {
            "text": " ".join(part["text"] for part in parts).strip(),
            "language": getattr(info, "language", "") or "",
            "duration": round(float(getattr(info, "duration", 0.0) or 0.0), 2),
            "segments": parts,
            "model": self._model_name(),
        }
