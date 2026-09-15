from __future__ import annotations

import os
import threading
import time
from typing import Callable

import numpy as np
import sounddevice as sd

SAMPLE_RATE = int(os.getenv("JARVIS_SAMPLE_RATE", "44100"))
BLOCK_MS = int(os.getenv("JARVIS_BLOCK_MS", "40"))
CHANNELS = 1
SPIKE_RATIO = float(os.getenv("JARVIS_SPIKE_RATIO", "7.0"))
COOLDOWN_S = float(os.getenv("JARVIS_CLAP_COOLDOWN", "2.0"))
MIN_DOUBLE_GAP_S = float(os.getenv("JARVIS_MIN_DOUBLE_GAP", "0.05"))
MAX_DOUBLE_GAP_S = float(os.getenv("JARVIS_MAX_DOUBLE_GAP", "0.35"))
RETRIGGER_RATIO = 0.55
NOISE_FLOOR_ALPHA = 0.992
MIN_RMS = float(os.getenv("JARVIS_MIN_RMS", "0.012"))
QUIET_GATE_MULT = 2.2
INPUT_PROBE_S = 0.45
INPUT_SILENT_RMS = 0.001


def rms_mono(block: np.ndarray) -> float:
    x = block.astype(np.float64)
    if x.ndim > 1:
        x = np.mean(x, axis=1)
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(x**2)))


def input_devices() -> list[tuple[int, dict]]:
    return [(i, d) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] >= 1]


def probe(idx: int, blocksize: int) -> float | None:
    try:
        with sd.InputStream(device=idx, samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=blocksize) as stream:
            peak = 0.0
            deadline = time.monotonic() + INPUT_PROBE_S
            while time.monotonic() < deadline:
                data, _ = stream.read(blocksize)
                peak = max(peak, rms_mono(data))
            return peak
    except Exception:
        return None


def choose_input(blocksize: int) -> tuple[int, str]:
    override = os.getenv("JARVIS_INPUT_DEVICE", "").strip()
    devices = input_devices()
    if override:
        if override.isdigit():
            idx = int(override)
            return idx, sd.query_devices(idx)["name"]
        for idx, dev in devices:
            if override.casefold() in dev["name"].casefold():
                return idx, dev["name"]
        raise RuntimeError(f"Aucun micro ne correspond à JARVIS_INPUT_DEVICE={override!r}")

    default = sd.default.device[0]
    if default is not None and default >= 0:
        peak = probe(default, blocksize)
        if peak is not None and peak >= INPUT_SILENT_RMS:
            return default, sd.query_devices(default)["name"]

    best: tuple[int, str, float] | None = None
    for idx, dev in devices:
        peak = probe(idx, blocksize)
        if peak is not None and (best is None or peak > best[2]):
            best = (idx, dev["name"], peak)
    if best:
        return best[0], best[1]
    if default is not None and default >= 0:
        return default, sd.query_devices(default)["name"]
    raise RuntimeError("Aucun microphone disponible")


class ClapListener(threading.Thread):
    def __init__(self, on_double_clap: Callable[[float], None], on_status: Callable[[str, str], None]):
        super().__init__(daemon=True, name="jarvis-clap-listener")
        self.on_double_clap = on_double_clap
        self.on_status = on_status
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        blocksize = max(1, int(SAMPLE_RATE * BLOCK_MS / 1000))
        try:
            idx, name = choose_input(blocksize)
        except Exception as exc:
            self.on_status(f"erreur: {exc}", "")
            return

        noise_floor = 1e-4
        last_double = 0.0
        first_clap: float | None = None
        spike_armed = True
        try:
            with sd.InputStream(device=idx, samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=blocksize) as stream:
                self.on_status("écoute", name)
                while not self.stop_event.is_set():
                    data, _ = stream.read(blocksize)
                    level = rms_mono(data)
                    if level < noise_floor * QUIET_GATE_MULT:
                        noise_floor = NOISE_FLOOR_ALPHA * noise_floor + (1 - NOISE_FLOOR_ALPHA) * level
                        noise_floor = max(noise_floor, 1e-7)
                    threshold = max(noise_floor * SPIKE_RATIO, MIN_RMS)
                    now = time.monotonic()
                    if level < threshold * RETRIGGER_RATIO:
                        spike_armed = True
                    if spike_armed and level >= threshold and (now - last_double) >= COOLDOWN_S:
                        spike_armed = False
                        if first_clap is None:
                            first_clap = now
                        else:
                            gap = now - first_clap
                            if gap < MIN_DOUBLE_GAP_S:
                                continue
                            if gap <= MAX_DOUBLE_GAP_S:
                                first_clap = None
                                last_double = now
                                self.on_double_clap(gap)
                            else:
                                first_clap = now
        except Exception as exc:
            self.on_status(f"erreur: {exc}", name)
