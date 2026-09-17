"""JARVIS demo web app : avatar holographique + voix edge-tts (gratuite).

Lancement :
    pip install -r requirements.txt
    python app.py          # http://127.0.0.1:8100
"""

from __future__ import annotations

import asyncio
import io
import platform
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import edge_tts
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Voix homme française naturelle (aucune clé API, aucun abonnement).
DEFAULT_VOICE = "fr-FR-HenriNeural"

app = FastAPI(title="JARVIS Web", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

BOOT_TIME = time.time()


# --------------------------------------------------------------------------- #
# Text-to-speech (edge-tts)
# --------------------------------------------------------------------------- #

class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    voice: str = DEFAULT_VOICE
    rate: str = "+0%"      # ex: "-10%", "+15%"
    pitch: str = "+0Hz"    # ex: "-5Hz"
    volume: str = "+0%"


async def synthesize(
    text: str,
    voice: str = DEFAULT_VOICE,
    rate: str = "+0%",
    pitch: str = "+0Hz",
    volume: str = "+0%",
) -> bytes:
    """Agrège le flux edge-tts en un MP3 complet, en mémoire."""
    communicate = edge_tts.Communicate(
        text, voice, rate=rate, pitch=pitch, volume=volume
    )
    buffer = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buffer.write(chunk["data"])
    data = buffer.getvalue()
    if not data:
        raise RuntimeError("edge-tts n'a renvoyé aucun audio")
    return data


@app.post("/speak")
async def speak(req: SpeakRequest):
    """Renvoie la réponse de JARVIS sous forme de MP3."""
    audio = await synthesize(req.text, req.voice, req.rate, req.pitch, req.volume)
    return StreamingResponse(
        io.BytesIO(audio),
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": 'inline; filename="jarvis.mp3"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/voices")
async def voices(locale: str = "fr-FR"):
    """Liste les voix disponibles (utile pour changer de timbre)."""
    found = await edge_tts.list_voices()
    return [
        {"name": v["ShortName"], "gender": v["Gender"], "locale": v["Locale"]}
        for v in found
        if v["Locale"].startswith(locale)
    ]


# --------------------------------------------------------------------------- #
# Diagnostic système
# --------------------------------------------------------------------------- #

@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


async def _check_tts() -> Check:
    try:
        audio = await asyncio.wait_for(synthesize("Test.", DEFAULT_VOICE), timeout=15)
        return Check("tts_engine", True, f"edge-tts OK ({len(audio)} octets)")
    except Exception as exc:  # noqa: BLE001 - on veut le message brut
        return Check("tts_engine", False, str(exc))


def _disk_check() -> Check:
    usage = shutil.disk_usage(BASE_DIR)
    free_gb = usage.free / 1024**3
    return Check("disk_space", free_gb > 1.0, f"{free_gb:.1f} Go libres")


def _static_check(filename: str) -> Check:
    path = STATIC_DIR / filename
    return Check(f"asset:{filename}", path.is_file(), str(path))


@app.get("/check-system")
async def check_system():
    checks: list[Check] = [
        Check("python_runtime", sys.version_info >= (3, 9), platform.python_version()),
        Check("platform", True, f"{platform.system()} {platform.release()}"),
        Check("web_server", True, "FastAPI en ligne"),
        Check("uptime", True, f"{time.time() - BOOT_TIME:.0f}s"),
        _disk_check(),
        _static_check("index.html"),
        _static_check("app.js"),
        _static_check("avatar.js"),
        _static_check("network.js"),
        _static_check("style.css"),
        await _check_tts(),
    ]
    passed = sum(1 for c in checks if c.ok)
    total = len(checks)
    summary = (
        f"All clear, {passed} of {total} checks passed. "
        "Errors since deploy couldn't be checked."
        if passed == total
        else f"{passed} of {total} checks passed — {total - passed} issue(s) detected."
    )
    return {
        "ok": passed == total,
        "passed": passed,
        "total": total,
        "summary": summary,
        "checks": [c.__dict__ for c in checks],
    }


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8100)
