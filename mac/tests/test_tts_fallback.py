"""Repli edge-tts : bascule quand Piper n'a aucune voix à offrir.

Le réseau n'est jamais sollicité ici : la synthèse Edge est remplacée par un
faux, seul le chemin de décision du serveur est testé.
"""
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from jarvis.core import JarvisCore
from jarvis.server import create_server
from jarvis.tts_edge import DEFAULT_VOICE, FRENCH_VOICES, MIME, EdgeTTS, _rate_pct


class TestRateConversion(unittest.TestCase):
    def test_piper_rate_maps_to_edge_percent(self):
        self.assertEqual(_rate_pct(1.0), "+0%")
        self.assertEqual(_rate_pct(1.2), "+20%")
        self.assertEqual(_rate_pct(0.85), "-15%")

    def test_extreme_rates_are_clamped(self):
        # Au-delà de ±50 %, la prosodie Edge devient inintelligible.
        self.assertEqual(_rate_pct(4.0), "+50%")
        self.assertEqual(_rate_pct(0.1), "-50%")

    def test_garbage_rate_falls_back_to_normal(self):
        self.assertEqual(_rate_pct(None), "+0%")
        self.assertEqual(_rate_pct("vite"), "+0%")


class TestEdgeEngine(unittest.TestCase):
    def test_catalog_is_french_and_offline(self):
        voices = EdgeTTS().voices()
        self.assertTrue(voices)
        for v in voices:
            self.assertTrue(v["id"].startswith(("fr-FR-", "fr-CA-")), v["id"])
        self.assertIn(DEFAULT_VOICE, FRENCH_VOICES)

    def test_status_declares_the_engine_as_remote(self):
        # Le texte sort de la machine : l'UI doit pouvoir le dire à l'utilisateur.
        self.assertTrue(EdgeTTS().engine_status()["remote"])

    def test_empty_text_never_calls_the_network(self):
        engine = EdgeTTS()
        engine._stream = lambda *a, **k: self.fail("réseau appelé pour un texte vide")
        self.assertIsNone(engine.synthesize("   "))


class TestSynthesizeRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.core = JarvisCore(Path(cls.tmp.name) / "tts.db")
        cls.server = create_server(cls.core, "127.0.0.1", 8801)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.4)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.core.shutdown()
        cls.tmp.cleanup()

    def post(self, path, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:8801{path}", data=json.dumps(body).encode(),
            method="POST", headers={"Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=15)

    def test_piper_output_is_preferred_over_the_fallback(self):
        self.core.tts.synthesize = lambda *a, **k: b"RIFF....WAVEfake"
        self.core.tts_fallback.synthesize = lambda *a, **k: self.fail("repli utilisé à tort")
        resp = self.post("/api/tts/synthesize", {"text": "Bonjour Monsieur."})
        self.assertEqual(resp.headers["Content-Type"], "audio/wav")
        self.assertEqual(resp.read(), b"RIFF....WAVEfake")

    def test_fallback_serves_mp3_when_piper_has_no_voice(self):
        self.core.tts.synthesize = lambda *a, **k: None
        seen = {}

        def fake(text, voice_id="", rate=1.0):
            seen.update(text=text, voice_id=voice_id, rate=rate)
            return b"ID3fake-mp3", MIME

        self.core.tts_fallback.synthesize = fake
        resp = self.post("/api/tts/synthesize",
                         {"text": "Tous les systèmes sont opérationnels."})
        self.assertEqual(resp.headers["Content-Type"], MIME)
        self.assertEqual(resp.read(), b"ID3fake-mp3")
        # Le texte transmis est bien celui qui a été sanitisé, pas un placeholder.
        self.assertIn("systèmes", seen["text"])

    def test_error_stays_503_when_both_engines_are_down(self):
        self.core.tts.synthesize = lambda *a, **k: None
        self.core.tts_fallback.synthesize = lambda *a, **k: None
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/api/tts/synthesize", {"text": "Bonjour."})
        self.assertEqual(ctx.exception.code, 503)

    def test_status_exposes_the_fallback_state(self):
        with urllib.request.urlopen("http://127.0.0.1:8801/api/tts/status", timeout=10) as r:
            res = json.loads(r.read().decode())
        self.assertIn("fallback", res)
        self.assertIn("available", res["fallback"])
        self.assertIsInstance(res["fallback_summary"], str)


if __name__ == "__main__":
    unittest.main()
