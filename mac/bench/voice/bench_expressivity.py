"""Mesure l'effet des paramètres d'expressivité Piper sur le rendu.

Le reproche fait à la voix est d'être « trop synthétique ». Ce qui rend une
voix robotique est mesurable : une hauteur trop plate (peu de variation de F0)
et un rythme trop régulier (durées de syllabes toutes identiques). Piper expose
`noise_scale` (variation de timbre) et `noise_w_scale` (variation de durée des
phonèmes) — que JARVIS n'utilisait pas. Ce banc quantifie leur effet réel.

    python bench/voice/bench_expressivity.py
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jarvis.config import DATA_DIR  # noqa: E402

PHRASE = ("Bonsoir Jérôme. Tous les systèmes sont opérationnels. "
          "Que puis-je faire pour vous ?")
VOICE = "fr_FR-tom-medium"
OUT_DIR = ROOT / "bench" / "voice" / "samples_expressivity"

#: (label, length_scale, noise_scale, noise_w_scale)
#: Les valeurs par défaut de Piper sont 1.0 / 0.667 / 0.8.
PROFILES = [
    ("defaut_piper", 1.0, 0.667, 0.8),
    ("jarvis_actuel", 1.0, 0.667, 0.8),
    ("pose", 1.06, 0.667, 0.9),
    ("naturel", 0.98, 0.72, 1.0),
    ("expressif", 0.95, 0.80, 1.15),
    ("tres_expressif", 0.92, 0.90, 1.35),
]


def f0_track(x: np.ndarray, sr: int) -> np.ndarray:
    """Suite des F0 (Hz) par autocorrélation, sur les trames voisées."""
    frame = int(sr * 0.04)
    hop = int(sr * 0.01)
    lo, hi = int(sr / 320), int(sr / 60)          # 60–320 Hz : voix masculine
    out = []
    for i in range(0, len(x) - frame, hop):
        seg = x[i:i + frame]
        if np.sqrt(np.mean(seg**2)) < 0.02:
            continue
        seg = seg - seg.mean()
        corr = np.correlate(seg, seg, "full")[frame - 1:]
        if corr[0] <= 0:
            continue
        window = corr[lo:hi]
        if not len(window):
            continue
        peak = int(np.argmax(window)) + lo
        # Rejette les trames non périodiques : ce n'est pas de la voix tenue.
        if corr[peak] / corr[0] < 0.3:
            continue
        out.append(sr / peak)
    return np.array(out)


def syllable_rhythm(x: np.ndarray, sr: int) -> float:
    """Écart-type des intervalles entre pics d'énergie (régularité du débit).

    Une valeur basse = rythme mécanique ; une valeur haute = phrasé varié.
    """
    hop = int(sr * 0.01)
    env = np.array([np.sqrt(np.mean(x[i:i + hop * 2]**2))
                    for i in range(0, len(x) - hop * 2, hop)])
    if len(env) < 5:
        return 0.0
    thr = np.percentile(env, 60)
    peaks = [i for i in range(1, len(env) - 1)
             if env[i] > thr and env[i] >= env[i - 1] and env[i] > env[i + 1]]
    if len(peaks) < 3:
        return 0.0
    gaps = np.diff(peaks) * 0.01
    return float(np.std(gaps))


def run_profile(voice, label, length_scale, noise_scale, noise_w_scale) -> dict:
    from piper.config import SynthesisConfig

    from jarvis.tts import _master_chunks, _wav_bytes

    config = SynthesisConfig(length_scale=length_scale, noise_scale=noise_scale,
                             noise_w_scale=noise_w_scale)
    t0 = time.perf_counter()
    chunks = list(voice.synthesize(PHRASE, syn_config=config))
    gen_ms = (time.perf_counter() - t0) * 1000
    sr = int(chunks[0].sample_rate)
    payload = _wav_bytes(_master_chunks(chunks, sr), sr)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{label}.wav").write_bytes(payload)

    x = np.frombuffer(payload[44:], dtype=np.int16).astype(np.float64) / 32768
    f0 = f0_track(x, sr)
    duration = len(x) / sr
    return {
        "profile": label,
        "length_scale": length_scale,
        "noise_scale": noise_scale,
        "noise_w_scale": noise_w_scale,
        "duration_s": round(duration, 2),
        "gen_ms": round(gen_ms, 0),
        "f0_median_hz": round(float(np.median(f0)), 1) if len(f0) else 0.0,
        # Variation de hauteur : c'est elle qui distingue une voix vivante
        # d'une voix plate. Exprimée en demi-tons pour être perceptuelle.
        "f0_spread_semitones": round(
            float(12 * np.log2(np.percentile(f0, 90) / np.percentile(f0, 10))), 2
        ) if len(f0) > 10 else 0.0,
        "rhythm_std_s": round(syllable_rhythm(x, sr), 4),
        "sample": f"bench/voice/samples_expressivity/{label}.wav",
    }


def main() -> int:
    from piper import PiperVoice

    voice_dir = DATA_DIR / "voices" / "fr" / VOICE
    voice = PiperVoice.load(str(voice_dir / f"{VOICE}.onnx"),
                            str(voice_dir / f"{VOICE}.onnx.json"))

    results = [run_profile(voice, *p) for p in PROFILES]
    (ROOT / "bench" / "voice" / "expressivity.json").write_text(
        json.dumps({"voice": VOICE, "phrase": PHRASE, "results": results},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    head = (f"{'Profil':<18}{'durée':>8}{'F0 méd.':>10}{'étendue F0':>13}"
            f"{'rythme σ':>11}{'génération':>12}")
    print(head)
    print("-" * len(head))
    for r in results:
        print(f"{r['profile']:<18}{r['duration_s']:>7.2f}s{r['f0_median_hz']:>9.1f}Hz"
              f"{r['f0_spread_semitones']:>10.2f} dt{r['rhythm_std_s']:>11.4f}"
              f"{r['gen_ms']:>10.0f}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
