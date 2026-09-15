"""Phase 3B — sélection de locuteur et paramètres d'expressivité Piper.

`speaker_id` se passe par SynthesisConfig, pas en argument nommé de
`synthesize()`. L'y passer levait un TypeError silencieusement avalé : toute
voix multi-locuteurs retombait sur son locuteur 0, rendant les voix masculines
de `fr_FR-upmc-medium` et `fr_FR-mls-medium` inatteignables.
"""
from __future__ import annotations

import unittest

from jarvis.tts import PiperTTS

VOICE_MULTI = "fr_FR-upmc-medium"
VOICE_MONO = "fr_FR-tom-medium"


def _installed(vid: str) -> bool:
    return any(v["id"] == vid and v["installed"] for v in PiperTTS().catalog())


class SpeakerResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tts = PiperTTS()

    @unittest.skipUnless(_installed(VOICE_MULTI), "voix multi-locuteurs absente")
    def test_named_speaker_resolves_to_its_numeric_id(self):
        self.assertEqual(self.tts.resolve_speaker_id(VOICE_MULTI, "jessica"), 0)
        self.assertEqual(self.tts.resolve_speaker_id(VOICE_MULTI, "pierre"), 1)

    @unittest.skipUnless(_installed(VOICE_MULTI), "voix multi-locuteurs absente")
    def test_numeric_speaker_is_accepted_as_is(self):
        self.assertEqual(self.tts.resolve_speaker_id(VOICE_MULTI, "1"), 1)

    def test_unknown_speaker_falls_back_to_the_default(self):
        """Un nom inconnu ne doit pas faire échouer la synthèse."""
        self.assertIsNone(self.tts.resolve_speaker_id(VOICE_MULTI, "zorglub"))
        self.assertIsNone(self.tts.resolve_speaker_id(VOICE_MULTI, ""))

    @unittest.skipUnless(_installed(VOICE_MONO), "voix mono-locuteur absente")
    def test_mono_speaker_voice_ignores_a_speaker_name(self):
        self.assertIsNone(self.tts.resolve_speaker_id(VOICE_MONO, "pierre"))


class SynthesisConfigTests(unittest.TestCase):
    def setUp(self):
        self.tts = PiperTTS()

    @unittest.skipUnless(_installed(VOICE_MULTI), "voix multi-locuteurs absente")
    def test_speaker_id_is_carried_by_the_synthesis_config(self):
        config = self.tts._synthesis_config(VOICE_MULTI, 1.0, "pierre")
        self.assertIsNotNone(config)
        self.assertEqual(getattr(config, "speaker_id", None), 1)

    def test_rate_maps_to_length_scale_inversely(self):
        """Débit plus rapide = phonèmes plus courts."""
        slow = self.tts._synthesis_config(VOICE_MONO, 0.8, "")
        fast = self.tts._synthesis_config(VOICE_MONO, 1.4, "")
        self.assertGreater(slow.length_scale, fast.length_scale)

    def test_expressivity_raises_both_noise_parameters(self):
        neutral = self.tts._synthesis_config(VOICE_MONO, 1.0, "", expressivity=0.0)
        lively = self.tts._synthesis_config(VOICE_MONO, 1.0, "", expressivity=1.0)
        self.assertGreater(lively.noise_scale, neutral.noise_scale)
        self.assertGreater(lively.noise_w_scale, neutral.noise_w_scale)

    def test_expressivity_is_clamped(self):
        low = self.tts._synthesis_config(VOICE_MONO, 1.0, "", expressivity=-5)
        high = self.tts._synthesis_config(VOICE_MONO, 1.0, "", expressivity=99)
        self.assertAlmostEqual(low.noise_scale, 0.667, places=3)
        self.assertAlmostEqual(high.noise_scale, 0.900, places=3)


class SpeakerProducesDistinctAudioTests(unittest.TestCase):
    """La preuve qui compte : deux locuteurs, deux sorties différentes."""

    @unittest.skipUnless(_installed(VOICE_MULTI), "voix multi-locuteurs absente")
    def test_two_speakers_produce_different_audio(self):
        tts = PiperTTS()
        male = tts.synthesize("Bonsoir Jérôme.", voice_id=VOICE_MULTI, speaker="pierre")
        female = tts.synthesize("Bonsoir Jérôme.", voice_id=VOICE_MULTI, speaker="jessica")
        self.assertTrue(male and female)
        self.assertNotEqual(male, female,
                            "les deux locuteurs rendent le même audio : "
                            "speaker_id n'est pas pris en compte")

    @unittest.skipUnless(_installed(VOICE_MONO), "voix mono-locuteur absente")
    def test_synthesis_still_works_without_any_speaker(self):
        wav = PiperTTS().synthesize("Bonsoir Jérôme.", voice_id=VOICE_MONO)
        self.assertTrue(wav and wav.startswith(b"RIFF"))


class AlignmentCapabilityTests(unittest.TestCase):
    """Les alignements sont sondés réellement, jamais supposés."""

    @unittest.skipUnless(_installed(VOICE_MONO), "voix absente")
    def test_capability_is_probed_not_assumed(self):
        tts = PiperTTS()
        supported = tts.supports_alignments(VOICE_MONO)
        self.assertIsInstance(supported, bool)
        # Le sondage est mis en cache : même réponse, sans re-synthèse.
        self.assertEqual(supported, tts.supports_alignments(VOICE_MONO))

    @unittest.skipUnless(_installed(VOICE_MONO), "voix absente")
    def test_caller_can_always_fall_back(self):
        """Sans alignements, l'appelant obtient None et retombe sur synthesize."""
        tts = PiperTTS()
        result = tts.synthesize_with_alignments("Bonsoir.", voice_id=VOICE_MONO)
        if not tts.supports_alignments(VOICE_MONO):
            self.assertIsNone(result)
        else:
            self.assertIn("phonemes", result)
            self.assertTrue(result["wav"].startswith(b"RIFF"))
            for item in result["phonemes"]:
                self.assertLessEqual(item["start"], item["end"])


if __name__ == "__main__":
    unittest.main()
