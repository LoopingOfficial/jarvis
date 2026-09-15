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
    # Le dernier champ du bloc « fmt » est bitsPerSample, exprimé en BITS.
    # On y écrivait `sample_width` (2 octets) : l'en-tête annonçait donc de
    # l'audio 2 bits pour du PCM 16 bits, et tout décodeur respectant l'en-tête
    # lisait un flux invalide.
    bits_per_sample = sample_width * 8
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE",
        b"fmt ", 16, 1, channels, sample_rate, byte_rate, block_align, bits_per_sample,
        b"data", data_size,
    )
    return header + pcm_data


class PiperTTS:
    """Moteur de synthèse Piper. Instance unique portée par CORE.tts."""

    def __init__(self, voice_dir: str | Path | None = None) -> None:
        self._dir = Path(voice_dir or VOICES_DIR)
        self._lock = threading.RLock()
        self._loaded: dict[str, Any] = {}
        self._alignment_support: dict[str, bool] = {}
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
            "speaker_id_map": dict(data.get("speaker_id_map") or {}),
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
    def resolve_speaker_id(self, vid: str, speaker: str) -> int | None:
        """Nom de locuteur → identifiant numérique attendu par Piper.

        Une voix multi-locuteurs (`fr_FR-upmc-medium` : jessica/pierre,
        `fr_FR-mls-medium` : 125 locuteurs) n'est adressable que par cet
        identifiant. Un nom inconnu renvoie None : le modèle parle alors avec
        son locuteur par défaut plutôt que d'échouer.
        """
        if not speaker:
            return None
        info = self._voice_info(vid) or {}
        mapping = info.get("speaker_id_map") or {}
        if speaker in mapping:
            return int(mapping[speaker])
        try:
            return int(speaker)
        except (TypeError, ValueError):
            return None

    def _synthesis_config(self, vid: str, rate: float, speaker: str,
                          expressivity: float | None = None):
        """Construit la SynthesisConfig, ou None si le moteur est trop ancien.

        `speaker_id` se passe ICI et nulle part ailleurs : il n'est pas un
        argument nommé de `synthesize()`. L'y passer levait un TypeError et
        faisait silencieusement retomber toute voix multi-locuteurs sur son
        locuteur 0 — les voix masculines étaient donc inatteignables.
        """
        if not self._piper_has_synthesis_config():
            return None
        try:
            from piper.config import SynthesisConfig
            length_scale = max(0.5, min(2.0, 1.0 / max(0.4, float(rate))))
            kwargs: dict[str, Any] = {"length_scale": length_scale}
            speaker_id = self.resolve_speaker_id(vid, speaker)
            if speaker_id is not None:
                kwargs["speaker_id"] = speaker_id
            if expressivity is not None:
                # 0 = neutre, 1 = maximum. Mesuré sur fr_FR-tom-medium : l'effet
                # sur l'étendue de F0 et la régularité du rythme reste dans le
                # bruit de mesure (cf. bench/voice/expressivity.json). Le réglage
                # est exposé parce que le moteur le supporte, pas parce qu'il
                # constitue un gain de naturel démontré.
                level = max(0.0, min(1.0, float(expressivity)))
                kwargs["noise_scale"] = 0.667 + 0.233 * level
                kwargs["noise_w_scale"] = 0.8 + 0.55 * level
            return SynthesisConfig(**kwargs)
        except Exception:
            return None

    def synthesize(self, text: str, voice_id: str = "", rate: float = 1.0,
                   speaker: str = "", expressivity: float | None = None) -> bytes | None:
        """Synthétise `text` → WAV (bytes). None si la voix est indisponible."""
        text = (text or "").strip()
        if not text:
            return None
        vid = (voice_id or "").strip() or self.default_voice()
        model = self._dir / vid / f"{vid}.onnx"
        if not model.exists():
            return None

        voice = self._load_voice(vid, model)
        if voice is None:
            return None

        # Tronque les très longues réponses (protection mémoire/CPU).
        text = text[:4000]
        syn_cfg = self._synthesis_config(vid, rate, speaker, expressivity)
        try:
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

    def supports_alignments(self, voice_id: str = "") -> bool:
        """Vrai si CETTE voix produit réellement des durées de phonèmes.

        Tester la signature de `synthesize()` ne suffit pas : `include_alignments`
        existe dans l'API alors que la plupart des modèles Piper n'exportent pas
        la sortie de durée nécessaire (`phoneme_id_samples` reste None). Seule
        une synthèse courte réellement exécutée répond à la question. Le
        résultat est mis en cache par voix.
        """
        vid = (voice_id or "").strip() or self.default_voice()
        with self._lock:
            cached = self._alignment_support.get(vid)
        if cached is not None:
            return cached
        supported = False
        try:
            import inspect

            from piper import PiperVoice
            if "include_alignments" in inspect.signature(
                    PiperVoice.synthesize).parameters:
                model = self._dir / vid / f"{vid}.onnx"
                voice = self._load_voice(vid, model) if model.exists() else None
                if voice is not None:
                    probe = list(voice.synthesize("Bonjour.", include_alignments=True))
                    supported = any(
                        getattr(c, "phoneme_id_samples", None) for c in probe)
        except Exception:
            supported = False
        with self._lock:
            self._alignment_support[vid] = supported
        return supported

    def synthesize_with_alignments(
        self, text: str, voice_id: str = "", rate: float = 1.0,
        speaker: str = "", expressivity: float | None = None,
    ) -> dict[str, Any] | None:
        """WAV + suite de phonèmes horodatés.

        C'est la donnée dont l'avatar a besoin pour des visèmes réellement
        synchronisés : sans elle, la bouche ne peut qu'approximer l'amplitude
        du signal. Retourne None si la voix ou le moteur ne le permettent pas ;
        l'appelant retombe alors sur `synthesize()`.
        """
        text = (text or "").strip()
        if not text or not self.supports_alignments(voice_id):
            return None
        vid = (voice_id or "").strip() or self.default_voice()
        model = self._dir / vid / f"{vid}.onnx"
        if not model.exists():
            return None
        voice = self._load_voice(vid, model)
        if voice is None:
            return None

        syn_cfg = self._synthesis_config(vid, rate, speaker, expressivity)
        try:
            chunks = list(voice.synthesize(text[:4000], syn_config=syn_cfg,
                                           include_alignments=True))
        except Exception:
            return None
        if not chunks:
            return None

        sample_rate = int(chunks[0].sample_rate) or DEFAULT_SAMPLE_RATE
        pcm = _master_chunks(chunks, sample_rate)
        if not pcm:
            return None

        # Les alignements sont relatifs à chaque phrase : on les recale sur la
        # timeline globale au fur et à mesure du cumul des durées.
        phonemes: list[dict[str, Any]] = []
        offset = 0.0
        for chunk in chunks:
            for item in (getattr(chunk, "alignments", None) or []):
                start = float(getattr(item, "start_seconds", 0.0) or 0.0)
                duration = float(getattr(item, "duration_seconds", 0.0) or 0.0)
                phonemes.append({
                    "phoneme": str(getattr(item, "phoneme", "") or ""),
                    "start": round(offset + start, 4),
                    "end": round(offset + start + duration, 4),
                })
            samples = len(getattr(chunk, "audio_int16_bytes", b"")) / 2
            offset += samples / float(sample_rate or 1)

        return {"wav": _wav_bytes(pcm, sample_rate), "sample_rate": sample_rate,
                "phonemes": phonemes, "duration": round(offset, 4)}

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