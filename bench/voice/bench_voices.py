"""Benchmark réel des voix françaises masculines locales.

Phrase identique pour tous les candidats, mesures prises sur cette machine :
latence du premier échantillon audio, durée totale de génération, facteur
temps-réel, CPU/RAM, et production du WAV comparatif.

Aucune voix n'est téléchargée ici : seules les voix déjà installées sous
DATA_DIR/voices sont mesurées. L'installation reste explicite.

    python bench/voice/bench_voices.py
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
import wave
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jarvis.config import DATA_DIR  # noqa: E402

PHRASE = ("Bonsoir Jérôme. Tous les systèmes sont opérationnels. "
          "Que puis-je faire pour vous ?")

OUT_DIR = ROOT / "bench" / "voice" / "samples"

#: Candidats masculins francophones. `speaker` est le nom tel qu'il apparaît
#: dans speaker_id_map ; il est résolu en identifiant numérique avant l'appel.
CANDIDATES = [
    {"id": "fr_FR-tom-medium", "speaker": "", "label": "Piper — Tom (medium, 44,1 kHz)"},
    {"id": "fr_FR-upmc-medium", "speaker": "pierre", "label": "Piper — UPMC Pierre (medium)"},
    {"id": "fr_FR-mls-medium", "speaker": "1840", "label": "Piper — MLS locuteur 1840 (medium)"},
    {"id": "fr_FR-mls-medium", "speaker": "123", "label": "Piper — MLS locuteur 123 (medium)"},
    {"id": "fr_FR-gilles-low", "speaker": "", "label": "Piper — Gilles (low, 16 kHz)"},
]

RUNS = 3


def _rss_mb() -> float:
    try:
        import ctypes
        import ctypes.wintypes as wt

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        ctypes.windll.psapi.GetProcessMemoryInfo(
            ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counters),
            counters.cb)
        return counters.WorkingSetSize / (1024 * 1024)
    except Exception:
        return 0.0


def _cpu_seconds() -> float:
    times = os.times()
    return times.user + times.system


def resolve_speaker(voice_dir: Path, vid: str, speaker: str):
    """Nom de locuteur -> identifiant numérique attendu par Piper."""
    if not speaker:
        return None
    config = voice_dir / vid / f"{vid}.onnx.json"
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except Exception:
        return None
    mapping = data.get("speaker_id_map") or {}
    if speaker in mapping:
        return int(mapping[speaker])
    try:
        return int(speaker)
    except (TypeError, ValueError):
        return None


def wav_duration_s(payload: bytes) -> float:
    import io
    with wave.open(io.BytesIO(payload), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate() or 1)


def measure(candidate: dict) -> dict:
    """Mesure une voix : chargement, premier échantillon, génération complète."""
    from piper import PiperVoice

    voice_dir = DATA_DIR / "voices" / "fr"
    vid = candidate["id"]
    model = voice_dir / vid / f"{vid}.onnx"
    config = voice_dir / vid / f"{vid}.onnx.json"
    if not model.exists():
        return {**candidate, "ok": False, "error": "VOICE_NOT_INSTALLED"}

    speaker_id = resolve_speaker(voice_dir, vid, candidate["speaker"])
    if candidate["speaker"] and speaker_id is None:
        return {**candidate, "ok": False, "error": "SPEAKER_UNKNOWN"}

    rss_before = _rss_mb()
    t_load = time.perf_counter()
    voice = PiperVoice.load(str(model), str(config) if config.exists() else None)
    load_ms = (time.perf_counter() - t_load) * 1000
    rss_after_load = _rss_mb()

    first_chunk_ms: list[float] = []
    total_ms: list[float] = []
    cpu_ms: list[float] = []
    audio_s = 0.0
    payload = b""

    for run in range(RUNS):
        cpu0 = _cpu_seconds()
        t0 = time.perf_counter()
        first = None
        chunks = []
        # `speaker_id` se passe par SynthesisConfig, PAS en argument nommé de
        # synthesize() : c'est ce qui rendait les voix multi-locuteurs
        # inatteignables (retombée silencieuse sur le locuteur 0).
        from piper.config import SynthesisConfig
        syn_config = SynthesisConfig(speaker_id=speaker_id) if speaker_id is not None else None
        for chunk in voice.synthesize(PHRASE, syn_config=syn_config):
            if first is None:
                first = (time.perf_counter() - t0) * 1000
            chunks.append(chunk)
        elapsed = (time.perf_counter() - t0) * 1000
        cpu_ms.append((_cpu_seconds() - cpu0) * 1000)
        first_chunk_ms.append(first if first is not None else elapsed)
        total_ms.append(elapsed)
        if run == RUNS - 1:
            from jarvis.tts import _master_chunks, _wav_bytes
            sample_rate = int(chunks[0].sample_rate)
            pcm = _master_chunks(chunks, sample_rate)
            payload = _wav_bytes(pcm, sample_rate)
            audio_s = wav_duration_s(payload)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    name = vid + (f"__{candidate['speaker']}" if candidate["speaker"] else "")
    out = OUT_DIR / f"{name}.wav"
    out.write_bytes(payload)

    median_total = statistics.median(total_ms)
    return {
        **candidate,
        "ok": True,
        "speaker_id": speaker_id,
        "sample_rate": int(json.loads(config.read_text(encoding="utf-8"))
                           .get("audio", {}).get("sample_rate", 0)),
        "load_ms": round(load_ms, 1),
        "first_audio_ms": round(statistics.median(first_chunk_ms), 1),
        "gen_ms": round(median_total, 1),
        "cpu_ms": round(statistics.median(cpu_ms), 1),
        "audio_s": round(audio_s, 2),
        # Facteur temps-réel : > 1 signifie « génère plus vite que ça ne se joue ».
        "rtf": round(audio_s / (median_total / 1000), 2) if median_total else 0,
        "rss_load_mb": round(rss_after_load - rss_before, 1),
        "wav_kb": round(len(payload) / 1024, 1),
        "sample": str(out.relative_to(ROOT)).replace("\\", "/"),
    }


def main() -> int:
    results = []
    for candidate in CANDIDATES:
        try:
            results.append(measure(candidate))
        except Exception as exc:
            results.append({**candidate, "ok": False, "error": f"{type(exc).__name__}: {exc}"})

    report = ROOT / "bench" / "voice" / "results.json"
    report.write_text(json.dumps({"phrase": PHRASE, "runs": RUNS,
                                  "results": results}, ensure_ascii=False, indent=2),
                      encoding="utf-8")

    header = f"{'Voix':<38}{'1er audio':>11}{'Génération':>12}{'RTF':>7}{'CPU':>9}{'RAM':>9}{'SR':>8}"
    print(header)
    print("-" * len(header))
    for r in results:
        if not r.get("ok"):
            print(f"{r['label']:<38}{'ÉCHEC — ' + r.get('error', ''):>50}")
            continue
        print(f"{r['label']:<38}{r['first_audio_ms']:>9.0f}ms{r['gen_ms']:>10.0f}ms"
              f"{r['rtf']:>7.2f}{r['cpu_ms']:>7.0f}ms{r['rss_load_mb']:>7.1f}Mo"
              f"{r['sample_rate']:>8}")
    print(f"\nÉchantillons : {OUT_DIR.relative_to(ROOT)}")
    print(f"Rapport      : {report.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
