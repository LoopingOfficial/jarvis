"""Synthèse vocale de repli — voix Microsoft Edge (`edge-tts`).

Rôle : couvrir le seul trou du moteur Piper — tant qu'aucune voix française
n'est installée localement, `/api/tts/synthesize` n'a rien à renvoyer et JARVIS
reste muet. Ce module fournit alors une voix française naturelle sans clé API
ni abonnement.

Règles d'architecture :
  * REPLI, jamais moteur principal : Piper reste prioritaire, parce que lui est
    réellement local. Ici le texte transite par les serveurs Microsoft ;
  * comme Piper, le moteur ne parle pas tout seul : il produit des octets ;
  * edge-tts ne sait livrer que du MP3 — `synthesize()` retourne donc le type
    MIME avec les octets, au lieu de le supposer comme le fait `tts.py`.
"""
from __future__ import annotations

import asyncio
import io
import threading
from typing import Any

# Voix françaises Edge, dans l'ordre de préférence pour JARVIS (timbre masculin
# posé d'abord, pour rester cohérent avec l'identité de l'assistant).
FRENCH_VOICES: dict[str, str] = {
    "fr-FR-HenriNeural": "Henri (homme, France)",
    "fr-FR-RemyMultilingualNeural": "Rémy (homme, multilingue)",
    "fr-FR-DeniseNeural": "Denise (femme, France)",
    "fr-FR-EloiseNeural": "Éloise (femme, France)",
    "fr-CA-ThierryNeural": "Thierry (homme, Canada)",
}
DEFAULT_VOICE = "fr-FR-HenriNeural"
MIME = "audio/mpeg"
TIMEOUT_S = 20.0


def _rate_pct(rate: float) -> str:
    """Convertit la vitesse Piper (1.0 = normal) au format edge-tts (« +0% »).

    Bornée à ±50 % : au-delà, la prosodie Edge se dégrade en bouillie.
    """
    try:
        pct = int(round((float(rate) - 1.0) * 100))
    except (TypeError, ValueError):
        pct = 0
    pct = max(-50, min(50, pct))
    return f"{pct:+d}%"


class EdgeTTS:
    """Moteur de repli. Instance unique portée par CORE.tts_fallback."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._last_error: str = ""

    # ------------------------------------------------------------------ état
    @staticmethod
    def _module_available() -> bool:
        try:
            import edge_tts  # noqa: F401
            return True
        except Exception:
            return False

    def available(self) -> bool:
        return self._module_available()

    def engine_status(self) -> dict[str, Any]:
        ok = self._module_available()
        return {
            "available": ok,
            "name": "edge-tts" if ok else None,
            "default_voice": DEFAULT_VOICE,
            "remote": True,   # le texte sort de la machine : à dire à l'utilisateur
            "last_error": self._last_error,
        }

    def voices(self) -> list[dict[str, Any]]:
        """Catalogue statique : pas d'appel réseau pour afficher les réglages."""
        return [
            {"id": vid, "label": label, "installed": True, "language": vid[:5],
             "provider": "edge"}
            for vid, label in FRENCH_VOICES.items()
        ]

    def default_voice(self) -> str:
        return DEFAULT_VOICE

    def summary(self) -> str:
        if not self._module_available():
            return "edge-tts non installé"
        return f"{len(FRENCH_VOICES)} voix Edge françaises (réseau Microsoft)"

    # -------------------------------------------------------------- synthèse
    def synthesize(self, text: str, voice_id: str = "", rate: float = 1.0,
                   pitch: str = "+0Hz") -> tuple[bytes, str] | None:
        """Synthétise `text` → (MP3, mime). None si le repli est indisponible.

        Appelé depuis un thread du serveur HTTP : on ouvre notre propre boucle
        asyncio, sans jamais toucher à une boucle déjà en cours ailleurs.
        """
        text = (text or "").strip()
        if not text or not self._module_available():
            return None
        vid = (voice_id or "").strip()
        if vid not in FRENCH_VOICES:
            vid = DEFAULT_VOICE
        text = text[:4000]   # même garde-fou que Piper

        with self._lock:
            try:
                audio = asyncio.run(
                    asyncio.wait_for(
                        self._stream(text, vid, _rate_pct(rate), pitch),
                        timeout=TIMEOUT_S,
                    )
                )
            except Exception as exc:  # réseau coupé, voix retirée, timeout…
                self._last_error = str(exc)
                return None
        if not audio:
            self._last_error = "edge-tts n'a renvoyé aucun audio"
            return None
        self._last_error = ""
        return audio, MIME

    @staticmethod
    async def _stream(text: str, voice: str, rate: str, pitch: str) -> bytes:
        """Agrège le flux edge-tts en un MP3 complet, en mémoire."""
        import edge_tts

        communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        buffer = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buffer.write(chunk["data"])
        return buffer.getvalue()
