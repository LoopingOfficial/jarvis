"""Synthèse vocale GPU — moteur Coqui XTTS-v2 (voix françaises neuronales).

Rôle : offrir une voix bien plus naturelle que Piper quand un GPU NVIDIA avec
assez de VRAM libre est présent, sans dépendre du réseau à l'usage (Edge TTS).

Règles d'architecture (mêmes garanties que `tts.py`/`stt.py`) :
  * AUCUN téléchargement implicite : le modèle (~1,8 Go) n'est récupéré que
    via `install()`, jamais au démarrage ni à la première synthèse ;
  * `available()` ne ment jamais : GPU absent, VRAM insuffisante ou modèle
    non installé → False, et la cascade serveur retombe sur Piper puis Edge ;
  * le moteur ne parle pas tout seul : il produit des octets WAV, le client
    joue.
"""
from __future__ import annotations

import os
import struct
import threading
import time
from typing import Any

from .config import DATA_DIR
from .gpu_manager import GpuResourceManager

MODEL_ID = "tts_models/multilingual/multi-dataset/xtts_v2"
MODELS_DIR = DATA_DIR / "models" / "xtts"
# Chemin où le paquet TTS dépose le modèle une fois téléchargé : sert à
# détecter une installation déjà faite sans repasser par le réseau.
_MODEL_MARKER = MODELS_DIR / "tts" / "tts_models--multilingual--multi-dataset--xtts_v2"

# En dessous, le modèle (poids + activations) risque l'OOM CUDA même en fp16.
MIN_FREE_MB = 3000
SAMPLE_RATE = 24000
LANGUAGE = "fr"


def _wav_bytes(pcm_i16: bytes, sample_rate: int, channels: int = 1, bits: int = 16) -> bytes:
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    data_size = len(pcm_i16)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE",
        b"fmt ", 16, 1, channels, sample_rate, byte_rate, block_align, bits,
        b"data", data_size,
    )
    return header + pcm_i16


def _float_to_pcm16(samples) -> bytes:
    import numpy as np

    arr = np.asarray(samples, dtype=np.float32)
    peak = float(np.max(np.abs(arr))) if arr.size else 0.0
    if peak > 1e-6:
        arr = arr * min(1.0, 0.92 / peak)
    return (arr * 32767.0).astype(np.int16).tobytes()


class XttsTTS:
    """Moteur XTTS-v2. Instance unique portée par CORE.tts_xtts."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._model: Any = None
        self._load_error = ""
        self._started = time.time()

    # ------------------------------------------------------------------ GPU
    @staticmethod
    def _gpu() -> dict[str, Any] | None:
        vram = GpuResourceManager.vram()
        if vram and vram.get("vendor") == "nvidia" and vram.get("free_mb", 0) >= MIN_FREE_MB:
            return vram
        return None

    @staticmethod
    def _package_available() -> bool:
        try:
            import TTS  # noqa: F401
            import torch  # noqa: F401
            return True
        except Exception:
            return False

    def is_installed(self) -> bool:
        return _MODEL_MARKER.is_dir() and any(_MODEL_MARKER.iterdir())

    def available(self) -> bool:
        return bool(self._package_available() and self._gpu() and self.is_installed())

    # ------------------------------------------------------------------ état
    def engine_status(self) -> dict[str, Any]:
        gpu = self._gpu()
        package_ok = self._package_available()
        installed = self.is_installed()
        hint = ""
        if not package_ok:
            hint = "Installe le moteur : pip install -r requirements-audio.txt (section XTTS)."
        elif not gpu:
            hint = "GPU NVIDIA insuffisant ou absent (>= 3 Go de VRAM libre requis)."
        elif not installed:
            hint = "Modèle non téléchargé : POST /api/tts/xtts/install (~1,8 Go)."
        return {
            "available": self.available(),
            "name": "xtts-v2" if self.available() else None,
            "package_available": package_ok,
            "installed": installed,
            "gpu": gpu,
            "default_voice": self.default_voice(),
            "started_at": self._started,
            "last_error": self._load_error,
            "hint": hint,
        }

    def summary(self) -> str:
        if self.available():
            return f"XTTS-v2 GPU ({(self._gpu() or {}).get('free_mb', 0)} Mo libres)"
        return "XTTS-v2 indisponible : " + (self.engine_status()["hint"] or "voir /api/tts/status")

    # --------------------------------------------------------------- voix
    def voices(self) -> list[dict[str, Any]]:
        """Locuteurs intégrés au modèle (pas de clonage requis)."""
        if self._model is None:
            return []
        names = list(getattr(self._model, "speakers", None) or [])
        default = self.default_voice()
        return [{"id": name, "label": name, "installed": True, "provider": "xtts",
                 "language": LANGUAGE, "default": name == default} for name in names]

    def default_voice(self) -> str:
        if self._model is not None:
            names = list(getattr(self._model, "speakers", None) or [])
            if names:
                return names[0]
        return ""

    # ------------------------------------------------------ installation
    def install(self) -> dict[str, Any]:
        """Télécharge et charge le modèle. Explicite : jamais appelé tout seul.

        Accepter la licence non-commerciale Coqui est un préalable du paquet
        `TTS` lui-même ; l'appel explicite à cette méthode EST ce consentement.
        """
        if not self._package_available():
            raise RuntimeError(
                "Paquet manquant : pip install -r requirements-audio.txt (section XTTS)")
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("TTS_HOME", str(MODELS_DIR))
        os.environ["COQUI_TOS_AGREED"] = "1"
        self._load(force=True)
        return {"installed": self.is_installed(), "voices": [v["id"] for v in self.voices()]}

    # --------------------------------------------------------------- moteur
    def _load(self, force: bool = False):
        with self._lock:
            if self._model is not None and not force:
                return self._model
            if not self._package_available():
                return None
            gpu = self._gpu()
            if gpu is None and not force:
                # Pas de GPU exploitable : on ne charge pas un modèle qui
                # tournerait en CPU (inutilisable en pratique, trop lent).
                return None
            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            os.environ.setdefault("TTS_HOME", str(MODELS_DIR))
            os.environ.setdefault("COQUI_TOS_AGREED", "1" if force else "0")
            try:
                from TTS.api import TTS

                model = TTS(MODEL_ID)
                model = model.to("cuda" if gpu else "cpu")
            except Exception as exc:
                self._load_error = str(exc)
                return None
            self._load_error = ""
            self._model = model
            return self._model

    def warmup(self) -> bool:
        if not self.is_installed():
            return False
        return self._load() is not None

    # ----------------------------------------------------------- synthèse
    def synthesize(self, text: str, voice_id: str = "", rate: float = 1.0,
                   speaker: str = "") -> bytes | None:
        """Synthétise `text` → WAV (bytes). None si le moteur est indisponible."""
        text = (text or "").strip()
        if not text or not self.available():
            return None
        model = self._load()
        if model is None:
            return None
        speaker_name = (speaker or voice_id or "").strip() or self.default_voice()
        if not speaker_name:
            return None
        text = text[:4000]
        speed = max(0.5, min(1.6, float(rate) if rate else 1.0))
        try:
            with self._lock:
                samples = model.tts(text=text, speaker=speaker_name, language=LANGUAGE,
                                    speed=speed)
        except Exception as exc:
            self._load_error = str(exc)
            return None
        if not samples:
            return None
        pcm = _float_to_pcm16(samples)
        return _wav_bytes(pcm, SAMPLE_RATE)
