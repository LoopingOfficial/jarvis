"""Tests obligatoires du système vocal (§31 du cahier des charges).

Ces tests vérifient la CAUSE, pas le symptôme : le greeting est décidé par le
serveur, une seule fois par session, et aucun événement technique ne peut le
rejouer.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("JARVIS_MASTER_KEY", "test-key-for-unit-tests")

from jarvis.config import SettingsStore  # noqa: E402
from jarvis.db import Database  # noqa: E402
from jarvis.events import EventBus  # noqa: E402
from jarvis.voice import (  # noqa: E402
    EXECUTING, IDLE, INTERRUPTED, LISTENING, PROCESSING, SPEAKING, WAKE,
    VoiceSessionManager, VoiceStateMachine,
)


class VoiceTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Database(Path(self.tmp.name) / "test.db")
        self.settings = SettingsStore(self.db)
        self.events = EventBus(self.db)
        self.sessions = VoiceSessionManager(self.db, self.settings, self.events)
        self.fsm = VoiceStateMachine(self.events)

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()


class TestGreetingPolicy(VoiceTestBase):
    """TEST 1 · Au démarrage, JARVIS peut saluer UNE fois."""

    def test_01_greeting_once_then_never(self):
        session = self.sessions.open("client-a")
        self.assertFalse(session["resumed"])
        speak, text = self.sessions.should_greet(session["session_id"])
        self.assertTrue(speak)
        self.assertIn("Bonjour", text)

        # Toute demande ultérieure est refusée, quel que soit le déclencheur.
        for _ in range(50):
            speak_again, _ = self.sessions.should_greet(session["session_id"])
            self.assertFalse(speak_again, "Le greeting a été rejoué : régression critique.")

    def test_01b_five_minutes_of_activity_never_regreets(self):
        """TEST 1 · Après 5 minutes simulées (heartbeats, VAD, TTS), aucun greeting."""
        session = self.sessions.open("client-b")
        self.assertTrue(self.sessions.should_greet(session["session_id"])[0])

        for minute in range(5):
            for _ in range(6):                       # heartbeat toutes les 10 s
                self.sessions.touch(session["session_id"])
                self.assertFalse(self.sessions.should_greet(session["session_id"])[0])
            # timeout VAD → retour IDLE
            self.fsm.transition(LISTENING, reason="micro")
            self.fsm.transition(IDLE, reason="silence")
            self.assertFalse(self.sessions.should_greet(session["session_id"])[0])

    def test_02_tts_completion_does_not_greet(self):
        """TEST 2 · La réponse n'est jamais interrompue par un greeting."""
        session = self.sessions.open("client-c")
        self.sessions.should_greet(session["session_id"])
        self.fsm.transition(PROCESSING, reason="commande")
        self.fsm.transition(SPEAKING, reason="réponse", utterance="Le serveur fonctionne.")
        # Pendant la parole, aucune autorisation de greeting.
        self.assertFalse(self.sessions.should_greet(session["session_id"])[0])
        self.assertEqual(self.fsm.state, SPEAKING)
        # Fin du TTS → IDLE silencieux
        self.fsm.transition(IDLE, reason="fin de parole")
        self.assertFalse(self.sessions.should_greet(session["session_id"])[0])

    def test_03_two_minutes_of_silence_is_silent(self):
        """TEST 3 · Après une réponse, le silence reste silencieux."""
        session = self.sessions.open("client-d")
        self.sessions.should_greet(session["session_id"])
        self.fsm.transition(PROCESSING)
        self.fsm.transition(SPEAKING)
        self.fsm.transition(IDLE, reason="fin de parole")
        for _ in range(24):                          # 2 minutes par pas de 5 s
            self.sessions.touch(session["session_id"])
            self.assertFalse(self.sessions.should_greet(session["session_id"])[0])
            self.assertEqual(self.fsm.state, IDLE)

    def test_04_mic_reactivation_does_not_greet(self):
        """TEST 4 · Réactiver le micro n'entraîne aucun bonjour."""
        session = self.sessions.open("client-e")
        self.sessions.should_greet(session["session_id"])
        for _ in range(10):
            self.fsm.transition(LISTENING, reason="micro activé")
            self.assertFalse(self.sessions.should_greet(session["session_id"])[0])
            self.fsm.transition(IDLE, reason="micro coupé")
            self.assertFalse(self.sessions.should_greet(session["session_id"])[0])

    def test_05_websocket_reconnect_does_not_greet(self):
        """TEST 5 · Une reconnexion du flux reprend la session sans resaluer."""
        first = self.sessions.open("client-f")
        self.assertTrue(self.sessions.should_greet(first["session_id"])[0])
        for _ in range(15):                          # 15 reconnexions
            again = self.sessions.open("client-f")
            self.assertTrue(again["resumed"], "La session aurait dû être reprise.")
            self.assertEqual(again["session_id"], first["session_id"])
            self.assertTrue(again["greeted"])
            self.assertFalse(self.sessions.should_greet(again["session_id"])[0])

    def test_06_stt_restart_does_not_greet(self):
        """TEST 6 · Redémarrage STT : aucun greeting."""
        session = self.sessions.open("client-g")
        self.sessions.should_greet(session["session_id"])
        for _ in range(30):                          # 30 redémarrages de reconnaissance
            self.fsm.transition(LISTENING, reason="stt start")
            self.fsm.transition(IDLE, reason="stt end")
            self.assertFalse(self.sessions.should_greet(session["session_id"])[0])

    def test_07_vad_timeout_does_not_greet(self):
        """TEST 7 · Timeout VAD : retour silencieux."""
        session = self.sessions.open("client-h")
        self.sessions.should_greet(session["session_id"])
        for _ in range(20):
            self.fsm.transition(WAKE, reason="wake word")
            self.fsm.transition(LISTENING, reason="attente")
            self.fsm.transition(IDLE, reason="timeout vad")
            self.assertFalse(self.sessions.should_greet(session["session_id"])[0])

    def test_08_new_real_session_may_greet_once(self):
        """TEST 8 · Une vraie nouvelle session peut saluer une seule fois."""
        first = self.sessions.open("client-i")
        self.assertTrue(self.sessions.should_greet(first["session_id"])[0])

        # On force l'expiration : dernière activité au-delà du seuil d'inactivité.
        old = time.time() - (self.settings.get("voice", "session_idle_reset_hours", 8) + 1) * 3600
        self.db.execute("UPDATE sessions SET last_seen_at=?, started_at=? WHERE id=?",
                        (old, old, first["session_id"]))
        second = self.sessions.open("client-i")
        self.assertFalse(second["resumed"])
        self.assertNotEqual(second["session_id"], first["session_id"])
        self.assertTrue(self.sessions.should_greet(second["session_id"])[0])
        self.assertFalse(self.sessions.should_greet(second["session_id"])[0])

    def test_09_greeting_disabled_is_respected(self):
        self.settings.update("voice", {"greeting_enabled": False})
        session = self.sessions.open("client-j")
        self.assertFalse(self.sessions.should_greet(session["session_id"])[0])

    def test_10_frequency_is_locked(self):
        """Aucun réglage ne peut faire répéter le greeting."""
        self.settings.update("voice", {"greeting_frequency": "every_minute"})
        self.assertEqual(self.settings.get("voice", "greeting_frequency"), "once_per_session")
        session = self.sessions.open("client-k")
        self.assertTrue(self.sessions.should_greet(session["session_id"])[0])
        self.assertFalse(self.sessions.should_greet(session["session_id"])[0])

    def test_11_concurrent_tabs_greet_once(self):
        """Deux onglets ouverts simultanément : un seul greeting."""
        import threading

        session = self.sessions.open("client-l")
        results = []
        lock = threading.Lock()

        def worker():
            speak, _ = self.sessions.should_greet(session["session_id"])
            with lock:
                results.append(speak)

        threads = [threading.Thread(target=worker) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(1 for r in results if r), 1, "Le greeting doit être unique et atomique.")


class TestStateMachine(VoiceTestBase):
    def test_allowed_transitions(self):
        self.assertTrue(self.fsm.transition(LISTENING)[0])
        self.assertTrue(self.fsm.transition(PROCESSING)[0])
        self.assertTrue(self.fsm.transition(EXECUTING)[0])
        self.assertTrue(self.fsm.transition(SPEAKING)[0])
        self.assertTrue(self.fsm.transition(IDLE)[0])

    def test_forbidden_transitions_are_rejected(self):
        self.fsm.transition(LISTENING)
        ok, state = self.fsm.transition(EXECUTING)     # LISTENING → EXECUTING interdit
        self.assertFalse(ok)
        self.assertEqual(state, LISTENING)
        self.assertEqual(self.fsm.snapshot()["rejected_transitions"], 1)

    def test_speaking_flag(self):
        self.fsm.transition(PROCESSING)
        self.fsm.transition(SPEAKING, utterance="Le serveur répond.")
        self.assertTrue(self.fsm.is_speaking)
        snapshot = self.fsm.snapshot()
        self.assertEqual(snapshot["utterance"], "Le serveur répond.")
        self.fsm.transition(INTERRUPTED, reason="stop utilisateur")
        self.assertFalse(self.fsm.is_speaking)

    def test_interrupt_returns_to_idle(self):
        self.fsm.transition(PROCESSING)
        self.fsm.transition(SPEAKING)
        self.fsm.transition(INTERRUPTED)
        self.assertTrue(self.fsm.transition(IDLE)[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
