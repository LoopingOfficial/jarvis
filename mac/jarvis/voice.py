"""Machine à états vocale — côté serveur, autorité unique sur le greeting.

CAUSE RACINE DU BUG CORRIGÉ ICI
--------------------------------
Dans l'ancienne version, le frontend disait « Bonjour Jérôme » à chaque fois que
`activation_counter` changeait ; ce compteur était incrémenté à chaque « double
clap » détecté par un simple seuil d'énergie — c'est-à-dire aussi par la voix de
JARVIS sortant des haut-parleurs. Résultat : greeting en boucle, et
`speechSynthesis.cancel()` coupait la phrase en cours.

Nouvelle règle, sans exception possible :
  * le greeting est décidé UNIQUEMENT ici, par `VoiceSessionManager.should_greet()` ;
  * il est lié à une SESSION UTILISATEUR persistée (table `sessions`) ;
  * une fois `greeted=1` écrit en base, aucune reconnexion websocket/SSE, aucun
    redémarrage STT, aucun timeout VAD, aucun heartbeat, aucune fin de TTS ne
    peut le rejouer — la base est la mémoire, pas l'état du navigateur ;
  * une nouvelle session n'est créée que si le client est nouveau OU si la
    dernière activité remonte à plus de `session_idle_reset_hours`.

États : IDLE → WAKE → LISTENING → PROCESSING → EXECUTING → SPEAKING → (INTERRUPTED) → IDLE
"""
from __future__ import annotations

import threading
import time
from typing import Any

from .db import Database, new_id

IDLE = "IDLE"
WAKE = "WAKE"
LISTENING = "LISTENING"
PROCESSING = "PROCESSING"
EXECUTING = "EXECUTING"
SPEAKING = "SPEAKING"
INTERRUPTED = "INTERRUPTED"

STATES = (IDLE, WAKE, LISTENING, PROCESSING, EXECUTING, SPEAKING, INTERRUPTED)

# Transitions autorisées. Toute autre transition est refusée et journalisée.
ALLOWED: dict[str, set[str]] = {
    IDLE: {WAKE, LISTENING, PROCESSING, SPEAKING, IDLE},
    # WAKE → SPEAKING : uniquement pour l'accusé bref (« Oui ? »), jamais le greeting.
    WAKE: {LISTENING, IDLE, PROCESSING, SPEAKING},
    LISTENING: {PROCESSING, IDLE, WAKE, LISTENING},
    PROCESSING: {EXECUTING, SPEAKING, IDLE, INTERRUPTED},
    EXECUTING: {SPEAKING, IDLE, EXECUTING, INTERRUPTED, PROCESSING},
    SPEAKING: {IDLE, INTERRUPTED, LISTENING, SPEAKING},
    INTERRUPTED: {IDLE, LISTENING},
}


class VoiceStateMachine:
    """État vocal courant. Une seule instance par processus (source de vérité)."""

    def __init__(self, events) -> None:
        self._events = events
        self._state = IDLE
        self._since = time.time()
        self._lock = threading.RLock()
        self._speaking_utterance = ""
        self._rejected = 0

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def is_speaking(self) -> bool:
        return self.state == SPEAKING

    def can(self, target: str) -> bool:
        with self._lock:
            return target in ALLOWED.get(self._state, set())

    def transition(self, target: str, *, reason: str = "", utterance: str = "") -> tuple[bool, str]:
        target = (target or "").upper()
        if target not in STATES:
            return False, f"État inconnu: {target}"
        with self._lock:
            current = self._state
            if target == current and target != SPEAKING:
                return True, current
            if target not in ALLOWED.get(current, set()):
                self._rejected += 1
                self._events.emit("voice.error", {
                    "rejected_transition": f"{current}→{target}", "reason": reason})
                return False, current
            self._state = target
            self._since = time.time()
            if target == SPEAKING:
                self._speaking_utterance = utterance[:200]
            elif target in {IDLE, INTERRUPTED}:
                self._speaking_utterance = ""
        self._events.emit("voice.state", {"state": target, "previous": current,
                                          "reason": reason, "ts": time.time()})
        return True, target

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"state": self._state, "since": self._since,
                    "duration_s": round(time.time() - self._since, 1),
                    "speaking": self._state == SPEAKING,
                    "utterance": self._speaking_utterance,
                    "rejected_transitions": self._rejected}


class VoiceSessionManager:
    """Sessions utilisateur + politique de greeting (une seule fois par session)."""

    def __init__(self, db: Database, settings, events) -> None:
        self._db = db
        self._settings = settings
        self._events = events
        self._lock = threading.RLock()

    def _idle_seconds(self) -> float:
        return float(self._settings.get("voice", "session_idle_reset_hours", 8)) * 3600

    def open(self, client_id: str = "") -> dict[str, Any]:
        """Ouvre (ou reprend) une session. Une reconnexion reprend TOUJOURS la session
        existante tant qu'elle n'a pas expiré — donc sans nouveau greeting."""
        client_id = (client_id or "").strip() or new_id("client")
        now = time.time()
        with self._lock:
            row = self._db.one(
                "SELECT * FROM sessions WHERE client_id=? AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1",
                (client_id,))
            if row and (now - (row["last_seen_at"] or row["started_at"] or 0)) < self._idle_seconds():
                self._db.execute("UPDATE sessions SET last_seen_at=? WHERE id=?", (now, row["id"]))
                return {"session_id": row["id"], "client_id": client_id,
                        "greeted": bool(row["greeted"]), "resumed": True,
                        "started_at": row["started_at"]}
            if row:
                self._db.execute("UPDATE sessions SET ended_at=? WHERE id=?", (now, row["id"]))
            sid = new_id("sess")
            self._db.execute(
                "INSERT INTO sessions(id, client_id, started_at, last_seen_at, greeted) VALUES(?,?,?,?,0)",
                (sid, client_id, now, now))
            return {"session_id": sid, "client_id": client_id, "greeted": False,
                    "resumed": False, "started_at": now}

    def touch(self, session_id: str) -> None:
        """Heartbeat. N'a AUCUN effet sur le greeting — c'est volontaire."""
        self._db.execute("UPDATE sessions SET last_seen_at=? WHERE id=?", (time.time(), session_id))

    def should_greet(self, session_id: str) -> tuple[bool, str]:
        """Décide UNE FOIS pour toutes. L'écriture `greeted=1` est atomique :
        deux onglets ou deux appels concurrents ne peuvent pas saluer deux fois."""
        voice = self._settings.section("voice")
        if not voice.get("greeting_enabled", True):
            return False, ""
        if voice.get("greeting_frequency") != "once_per_session":
            # Verrouillé par config.LOCKED_SETTINGS ; garde-fou supplémentaire.
            return False, ""
        with self._lock:
            row = self._db.one("SELECT greeted FROM sessions WHERE id=?", (session_id,))
            if row is None or int(row["greeted"] or 0) == 1:
                return False, ""
            cur = self._db.execute(
                "UPDATE sessions SET greeted=1, greeted_at=? WHERE id=? AND greeted=0",
                (time.time(), session_id))
            if not cur.rowcount:
                return False, ""
        template = str(voice.get("greeting_text") or "Bonjour {user}.")
        text = template.replace("{user}", str(self._settings.get("general", "user_name", "Jérôme")))
        self._events.emit("voice.speaking", {"kind": "greeting", "text": text})
        return True, text

    def end(self, session_id: str) -> None:
        self._db.execute("UPDATE sessions SET ended_at=? WHERE id=?", (time.time(), session_id))

    def info(self, session_id: str) -> dict[str, Any] | None:
        row = self._db.one("SELECT * FROM sessions WHERE id=?", (session_id,))
        return {k: row[k] for k in row.keys()} if row else None

    def stats(self) -> dict[str, Any]:
        return {
            "sessions": int(self._db.scalar("SELECT COUNT(*) FROM sessions") or 0),
            "greeted": int(self._db.scalar("SELECT COUNT(*) FROM sessions WHERE greeted=1") or 0),
            "active": int(self._db.scalar(
                "SELECT COUNT(*) FROM sessions WHERE ended_at IS NULL AND last_seen_at > ?",
                (time.time() - 3600,)) or 0),
        }

    def prune(self, keep: int = 200) -> None:
        self._db.execute(
            "DELETE FROM sessions WHERE id NOT IN (SELECT id FROM sessions ORDER BY started_at DESC LIMIT ?)",
            (keep,))
