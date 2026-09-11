"""Synthèse vocale locale — moteur Piper (voix françaises uniquement).

Rôle : remplacer la Web Speech API du navigateur par une synthèse réellement
locale, générée côté serveur et diffusée au client sous forme de WAV.

Vrai budget fonctionnel :
  * catalogue des voix françaises officielles de `rhasspy/piper-voices` ;
  * découverte des voix installées sous DATA_DIR/voices ;
  * installation/import d'une voix (`.onnx` + `.onnx.json`) depuis HuggingFace ;
  * synthèse d'un texte → WAV PCM 16 bits mono (le format est reconstitué ici,
    Piper livre seulement des échantillons bruts).

Règles d'architecture :
  * le moteur ne parle pas tout seul : il produit des octets, le client joue ;
  * AUCUN téléchargement au démarrage : seules les voix déjà présentes sont
    utilisées, l'installation est toujours explicite (endpoint /api/tts/install
    ou helper CLI ci-dessous) ;
  * une voix est chargée une seule fois puis mise en cache (chargement ~1,5 s).
"""
from __future__ import annotations

import io
import json
import shutil
import struct
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import DATA_DIR

VOICES_DIR = DATA_DIR / "voices" / "fr"

# Voix françaises officielles du dépôt rhasspy/piper-voices, dans l'ordre de
# préférence pour JARVIS (qualité d'abord). `hf` = dossier dans le dépôt HF.
FRENCH_VOICES: dict[str, dict[str, str]] = {
    "fr_FR-tom-medium": {"hf": "tom/medium", "label": "Tom (medium)"},
    "fr_FR-upmc-medium": {"hf": "upmc/medium", "label": "Université de Montpellier (medium)"},
    "fr_FR-mls-medium": {"hf": "mls/medium", "label": "MLS (medium)"},
    "fr_FR-gilles-low": {"hf": "gilles/low", "label": "Gilles (low)"},
    "fr_FR-siwis-low": {"hf": "siwis/low", "label": "Siwis (low)"},
}

HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/"

# ---------------------------------------------------------------------------
# Mastering de sortie : Piper normalise chaque phrase à plein niveau (peak =
# 1.0), ce qui produit une voix agressive/saturée. On redonne de la marge,
# on écrête doucement les crêtes et on fond les fins/prochain débuts de
# phrases pour éliminer les clics de raboutage.
# ---------------------------------------------------------------------------
# Crête cible après mastering (≈ -1.4 dBFS : garde la voix claire sans risque
# de saturation sur des enceintes/écouteurs sensibles).
MASTER_PEAK = 0.85
# Seuil (en valeur absolue) à partir duquel on compresse doucement les crêtes.
LIMITER_THRESHOLD = 0.60
# Racourcissement d'une phrase : durée (s) du fondu de sortie à la fin de
# chaque phrase, avant le début de la suivante.
CHUNK_FADE_S = 0.012
CHUNK_FADE_IN_S = 0.006
DEFAULT_SAMPLE_RATE = 22050

_wav_header_cache_lock = threading.Lock()


def _master_chunks(chunks, sample_rate: int) -> "bytes | None":
    """Concatène les phrases Piper, applique limiteur + fades, rend du PCM
    int16 mono prêt à être encapsulé en WAV."""
    try:
        import numpy as np
    except Exception:
        # Sans numpy (moteur seul), repli : concaténation brute des int16.
        parts = [getattr(c, "audio_int16_bytes", b"") for c in chunks]
        return b"".join(parts) if parts else None
    arrays = [np.asarray(c.audio_float_array, dtype=np.float32) for c in chunks]
    if not arrays:
        return None
    fade_n = int(round(sample_rate * CHUNK_FADE_S))
    fade_in_n = int(round(sample_rate * CHUNK_FADE_IN_S))
    out = None
    for arr in arrays:
        if arr.size == 0:
            continue
        a = arr
        if fade_n > 4 and len(a) > fade_n * 4:
            ramp = np.linspace(1.0, 0.0, fade_n, dtype=np.float32)
            a = a.copy()
            a[-fade_n:] *= ramp
        if fade_in_n > 4 and len(a) > fade_in_n * 4:
            ramp = np.linspace(0.0, 1.0, fade_in_n, dtype=np.float32)
            a = np.concatenate([a[:fade_in_n] * ramp, a[fade_in_n:]])
        out = a if out is None else np.concatenate([out, a])
    if out is None or out.size == 0:
        return None

    # 1) Marge : la phrase n'atteint jamais 100 % du niveau maximum.
    peak = float(np.max(np.abs(out)))
    if peak > 1e-6 and peak > MASTER_PEAK:
        out = out * (float(MASTER_PEAK) / peak)

    # 2) Limiteur doux : comprime les crêtes au‑dessus du seuil (ratio 3:1)
    #    pour supprimer la raideur des « esses » et des plosives.
    over = np.abs(out) - LIMITER_THRESHOLD
    np.maximum(over, 0.0, out=over)
    out = np.where(
        np.abs(out) > LIMITER_THRESHOLD,
        np.sign(out) * (LIMITER_THRESHOLD + over / 3.0),
        out,
    )

    # 3) Conversion int16 avec clipping de sécurité.
    ints = np.clip(out * 32767.0, -32768.0, 32767.0).astype(np.int16)
    return ints.tobytes()


def _wav_bytes(pcm_data: bytes, sample_rate: int, channels: int = 1,
               sample_width: int = 2) -> bytes:
    """Encapsule du PCM brut en fichier WAV (RIFF)."""
    data_size = len(pcm_data)
    block_align = channels * sample_width
    byte_rate = sample_rate * block_align
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE",
        b"fmt ", 16, 1, channels, sample_rate, byte_rate, block_align, sample_width,
        b"data", data_size,
    )
    return header + pcm_data


class PiperTTS:
    """Moteur de synthèse Piper. Instance unique portée par CORE.tts."""

    def __init__(self, voice_dir: str | Path | None = None) -> None:
        self._dir = Path(voice_dir or VOICES_DIR)
        self._lock = threading.RLock()
        self._loaded: dict[str, Any] = {}
        self._started = time.time()

    # ------------------------------------------------------------------ état
    def engine_status(self) -> dict[str, Any]:
        ok = self._piper_available()
        return {
            "available": ok,
            "name": "piper-tts" if ok else None,
            "voices_dir": str(self._dir),
            "default_voice": self.default_voice(),
            "started_at": self._started,
        }

    @staticmethod
    def _piper_available() -> bool:
        try:
            import piper  # noqa: F401
            return True
        except Exception:
            return False

    # ------------------------------------------------------------- voix
    def catalog(self) -> list[dict[str, Any]]:
        """Les voix françaises officielles, avec leur état local."""
        out = []
        for vid, meta in FRENCH_VOICES.items():
            info = self._voice_info(vid) or {"sample_rate": 22050, "language": "fr_FR"}
            out.append({
                "id": vid,
                "label": meta["label"],
                "quality": vid.split("-")[-1],
                "installed": (self._dir / vid / f"{vid}.onnx").exists(),
                "language": info.get("language", "fr_FR"),
                "sample_rate": info.get("sample_rate", 22050),
                "speakers": info.get("speakers", []),
            })
        return out

    def voices(self) -> list[dict[str, Any]]:
        return self.catalog()

    def installed(self) -> list[dict[str, Any]]:
        return [v for v in self.catalog() if v["installed"]]

    def _voice_info(self, vid: str) -> dict[str, Any] | None:
        json_path = self._dir / vid / f"{vid}.onnx.json"
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return {
            "sample_rate": int((data.get("audio") or {}).get("sample_rate", 22050)),
            "language": str(((data.get("language") or {}).get("code")) or "fr_FR"),
            "speakers": list((data.get("speaker_id_map") or {}).keys()),
        }

    def default_voice(self) -> str:
        for vid in FRENCH_VOICES:
            if (self._dir / vid / f"{vid}.onnx").exists():
                return vid
        return next(iter(FRENCH_VOICES))  # tom-medium (à installer)

    # ------------------------------------------------------ installation
    def install(self, voice_id: str) -> dict[str, Any]:
        """Télécharge une voix française depuis HuggingFace + l'importe."""
        vid = (voice_id or "").strip()
        if vid not in FRENCH_VOICES:
            raise ValueError(f"Voix inconnue: {vid}")
        dest = self._dir / vid
        dest.mkdir(parents=True, exist_ok=True)
        for ext in (".onnx", ".onnx.json"):
            url = f"{HF_BASE}{FRENCH_VOICES[vid]['hf']}/{vid}{ext}"
            target = dest / f"{vid}{ext}"
            urllib.request.urlretrieve(url, target)  # noqa: S310 — URL fixe du catalogue
        with self._lock:
            self._loaded.pop(vid, None)  # invalide le cache si déjà chargé
        return {"id": vid, "installed": True, "path": str(dest)}

    def install_all_french(self) -> list[dict[str, Any]]:
        result = []
        for vid in FRENCH_VOICES:
            if (self._dir / vid / f"{vid}.onnx").exists():
                result.append({"id": vid, "installed": True, "skipped": True})
                continue
            result.append(self.install(vid))
        return result

    # ----------------------------------------------------------- synthèse
    def synthesize(self, text: str, voice_id: str = "", rate: float = 1.0,
                   speaker: str = "") -> bytes | None:
        """Synthétise `text` → WAV (bytes). None si la voix est indisponible."""
        text = (text or "").strip()
        if not text:
            return None
        vid = (voice_id or "").strip() or self.default_voice()
        model = self._dir / vid / f"{vid}.onnx"
        if not model.exists():
            return None
        import piper  # import tardif : l'absence du moteur ne bloque pas le boot

        voice = self._load_voice(vid, model)
        if voice is None:
            return None

        # Tronque les très longues réponses (protection mémoire/CPU).
        text = text[:4000]
        syn_cfg = None
        if self._piper_has_synthesis_config():
            try:
                from piper.config import SynthesisConfig
                length_scale = max(0.5, min(2.0, 1.0 / max(0.4, float(rate))))
                syn_cfg = SynthesisConfig(length_scale=length_scale)
            except Exception:
                syn_cfg = None
        try:
            chunks = list(voice.synthesize(text, syn_config=syn_cfg,
                                           speaker_id=None if not speaker else speaker))
        except TypeError:
            chunks = list(voice.synthesize(text, syn_config=syn_cfg))
        except Exception:
            return None
        if not chunks:
            return None
        sample_rate = int(chunks[0].sample_rate) or DEFAULT_SAMPLE_RATE
        pcm = _master_chunks(chunks, sample_rate)
        if not pcm:
            return None
        return _wav_bytes(pcm, sample_rate)

    @staticmethod
    def _piper_has_synthesis_config() -> bool:
        try:
            from piper.config import SynthesisConfig  # noqa: F401
            return True
        except Exception:
            return False

    def _load_voice(self, vid: str, model: Path):
        with self._lock:
            voice = self._loaded.get(vid)
            if voice is not None:
                return voice
        from piper import PiperVoice
        config = self._dir / vid / f"{vid}.onnx.json"
        try:
            voice = PiperVoice.load(str(model), str(config) if config.exists() else None)
        except Exception:
            return None
        with self._lock:
            self._loaded[vid] = voice
        return voice

    def warmup(self, voice_id: str = "") -> bool:
        """Charge une voix en mémoire (démarrage moins lent qu'à la première
        réponse). Retourne True si la voix est prête."""
        vid = (voice_id or "").strip() or self.default_voice()
        model = self._dir / vid / f"{vid}.onnx"
        if not model.exists():
            return False
        return self._load_voice(vid, model) is not None

    # ------------------------------------------------------------- divers
    def summary(self) -> str:
        total_mb = 0
        count = 0
        for p in self._dir.glob("fr_FR-*/*.onnx"):
            total_mb += p.stat().st_size / (1024 * 1024)
            count += 1
        return f"{count} voix française(s), {total_mb:.1f} Mo" if count else "aucune voix installée"


def cli() -> None:
    """python -m jarvis.tts            → état + voix disponibles
       python -m jarvis.tts install    → installe les 5 voix françaises"""
    import sys

    engine = PiperTTS()
    def show() -> None:
        print(f"moteur piper: {'OK' if engine.engine_status()['available'] else 'ABSENT'}")
        print(f"dossier voix : {engine._dir}")
        print(f"défaut       : {engine.default_voice()}")
        for v in engine.catalog():
            state = "installee" if v["installed"] else "manquante"
            print(f"  - {v['id']:<22} {state}")
    if len(sys.argv) > 1 and sys.argv[1] == "install":
        for res in engine.install_all_french():
            print(f"installee: {res['id']}" if not res.get("skipped") else f"deja presente: {res['id']}")
    else:
        show()


if __name__ == "__main__":
    cli()