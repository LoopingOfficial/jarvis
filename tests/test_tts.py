"""Synthèse vocale Piper : catalogue français, découverte, WAV, téléchargement."""
import struct
import tempfile
import unittest
from pathlib import Path

from jarvis import tts as tts_mod
from jarvis.tts import FRENCH_VOICES, PiperTTS, _wav_bytes


def _touch(path: Path, content: str | bytes = b"\x00") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode())


class TestFrenchCatalog(unittest.TestCase):
    def test_only_french_voices(self):
        self.assertEqual(len(FRENCH_VOICES), 5)
        for vid, meta in FRENCH_VOICES.items():
            self.assertTrue(vid.startswith("fr_FR-"), vid)
            self.assertIn("hf", meta)
            self.assertIn(meta["hf"].split("/")[-1], vid)  # basename == voix

    def test_hf_urls_are_stable(self):
        for vid, meta in FRENCH_VOICES.items():
            url = f"{tts_mod.HF_BASE}{meta['hf']}/{vid}.onnx"
            self.assertTrue(url.endswith(f"{vid}.onnx"))
            self.assertIn("rhasspy/piper-voices/resolve/main/fr/", url)


class TestWavBuilder(unittest.TestCase):
    def test_wav_header(self):
        wav = _wav_bytes(b"\x00\x01" * 800, sample_rate=22050)
        self.assertEqual(wav[:4], b"RIFF")
        self.assertEqual(wav[8:12], b"WAVE")
        self.assertEqual(struct.unpack("<I", wav[24:28])[0], 22050)
        self.assertEqual(struct.unpack("<HH", wav[20:24]), (1, 1))  # PCM, mono
        self.assertEqual(len(wav) - struct.unpack("<I", wav[40:44])[0], 44)


class TestPiperTTS(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = PiperTTS(voice_dir=Path(self.tmp.name) / "voices" / "fr")

    def test_no_voice_single_dummy(self):
        _touch(self.engine._dir / "fr_FR-tom-medium" / "fr_FR-tom-medium.onnx")
        enc = '{"audio":{"sample_rate":22050,"quality":"medium"},"language":{"code":"fr_FR"}}'
        _touch(self.engine._dir / "fr_FR-tom-medium" / "fr_FR-tom-medium.onnx.json", enc)
        voices = self.engine.voices()
        self.assertEqual(voices[0]["id"], "fr_FR-tom-medium")
        self.assertTrue(voices[0]["installed"])
        self.assertEqual(voices[0]["sample_rate"], 22050)
        self.assertEqual(self.engine.default_voice(), "fr_FR-tom-medium")

    def test_all_installed_reports_french_only(self):
        for vid in FRENCH_VOICES:
            _touch(self.engine._dir / vid / f"{vid}.onnx")
            _touch(self.engine._dir / vid / f"{vid}.onnx.json")
        installed = self.engine.installed()
        ids = {v["id"] for v in installed}
        self.assertEqual(ids, set(FRENCH_VOICES))

    def test_synthesize_missing_voice_returns_none(self):
        self.assertIsNone(self.engine.synthesize("Bonjour", voice_id="fr_FR-tom-medium"))
        self.assertIsNone(self.engine.synthesize(""))

    def test_install_unknown_voice_raises(self):
        with self.assertRaises(ValueError):
            self.engine.install("de_DE-thorsten-medium")


if __name__ == "__main__":
    unittest.main()