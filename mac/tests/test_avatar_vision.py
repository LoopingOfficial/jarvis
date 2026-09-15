"""Tests du pipeline vision de l'avatar (exigences 57, 58, 59).

Ces tests sont des tests d'INTEGRATION : ils parlent au vrai fournisseur de
vision configure. Sans fournisseur vision disponible, les tests 57/58 sont
sautes — mais le test 59 (comportement en cas de panne) tourne toujours, car
c'est precisement lui qui garantit qu'aucune valeur n'est inventee.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REFERENCE_ID = "avref_3cdff7e5e17a"
REFERENCE_PNG = ROOT / "data" / "avatar_references" / f"{REFERENCE_ID}.png"
CRUDE_RENDER = ROOT / "docs" / "avatar" / "01_full_body.png"

_CORE = None


def core():
    global _CORE
    if _CORE is None:
        from jarvis.core import JarvisCore
        _CORE = JarvisCore()
    return _CORE


def vision_available() -> bool:
    return bool(core().llm.vision_status().get("available"))


def brown(hex_colour: str) -> bool:
    """Teinte brune : rouge dominant, sombre."""
    value = str(hex_colour or "").lstrip("#")
    if len(value) != 6:
        return False
    try:
        r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return False
    return r > g >= b and 30 < r < 170


class TestAvatarVisionAnalysis(unittest.TestCase):
    """Exigence 57 — l'analyse doit vraiment comprendre l'image."""

    @classmethod
    def setUpClass(cls):
        if not REFERENCE_PNG.is_file():
            raise unittest.SkipTest(f"Référence absente : {REFERENCE_PNG}")
        if not vision_available():
            raise unittest.SkipTest("Aucun fournisseur vision connecté.")
        cls.features = core().avatar_ref.analyze(REFERENCE_ID)

    def test_analysis_success(self):
        self.assertTrue(self.features.get("analysis_success"),
                        self.features.get("analysis_error"))

    def test_image_was_really_sent(self):
        width, height = self.features.get("image_dimensions", [0, 0])
        self.assertGreater(width, 0)
        self.assertGreater(height, 0)
        self.assertTrue(self.features.get("vision_model"))

    def test_hair_is_brown_and_voluminous(self):
        hair = self.features.get("hair") or {}
        self.assertTrue(brown(hair.get("color", "")), f"couleur={hair.get('color')}")
        self.assertIn(str(hair.get("volume", "")).lower(),
                      {"voluminous", "medium"}, f"volume={hair.get('volume')}")

    def test_eyes_are_large_and_brown(self):
        eyes = self.features.get("eyes") or {}
        self.assertEqual(str(eyes.get("size", "")).lower(), "large")
        self.assertIn(str(eyes.get("color_name", "")).lower(),
                      {"brown", "hazel", "amber"}, f"iris={eyes.get('color_name')}")

    def test_outfit_pieces_detected(self):
        outfit = self.features.get("outfit") or {}
        for piece in ("shirt", "overshirt", "pants", "shoes"):
            self.assertTrue((outfit.get(piece) or {}).get("present"),
                            f"{piece} non détecté")

    def test_body_is_slim_and_stylised(self):
        self.assertIn("slim", str((self.features.get("body") or {}).get("build", "")).lower())
        self.assertTrue((self.features.get("style") or {}).get("animation_movie_style"))


class TestAvatarEvaluationDiscriminates(unittest.TestCase):
    """Exigence 58 — une image proche doit scorer plus haut qu'une image éloignée."""

    @classmethod
    def setUpClass(cls):
        if not (REFERENCE_PNG.is_file() and CRUDE_RENDER.is_file()):
            raise unittest.SkipTest("Images de test absentes.")
        if not vision_available():
            raise unittest.SkipTest("Aucun fournisseur vision connecté.")
        cls.close = core().avatar_ref.evaluate_similarity(str(REFERENCE_PNG), REFERENCE_ID)
        cls.far = core().avatar_ref.evaluate_similarity(str(CRUDE_RENDER), REFERENCE_ID)

    def test_both_evaluations_available(self):
        self.assertTrue(self.close.get("available"), self.close.get("reason"))
        self.assertTrue(self.far.get("available"), self.far.get("reason"))

    def test_close_scores_higher_than_far(self):
        self.assertGreater(self.close["overall_score"], self.far["overall_score"])

    def test_scores_are_not_the_constant_fallback(self):
        """L'ancien bug renvoyait 60 partout : c'est exactement ce qu'on interdit."""
        values = [self.close["overall_score"], self.far["overall_score"]]
        self.assertNotEqual(values[0], values[1])
        self.assertFalse(all(v == 60 for v in values))

    def test_issues_are_reported(self):
        self.assertTrue(self.far.get("issues"))


class TestNoSilentFallback(unittest.TestCase):
    """Exigence 59 — fournisseur vision indisponible : N/A, jamais d'invention."""

    def setUp(self):
        self.manager = core().llm
        self._saved = self.manager.vision_status
        self.manager.vision_status = lambda: {
            "available": False, "provider": "", "model": "",
            "reason": "Fournisseur vision simulé hors service."}

    def tearDown(self):
        self.manager.vision_status = self._saved

    def test_analysis_fails_loudly(self):
        features = core().avatar_ref._extract_features(
            {"source_path": str(REFERENCE_PNG), "reference_type": "mixed"})
        self.assertFalse(features.get("analysis_success"))
        self.assertIn("hors service", features.get("analysis_error", ""))
        # Aucune feature fabriquee : pas de coiffure/tenue inventee.
        self.assertIsNone(features.get("hair"))
        self.assertIsNone(features.get("outfit"))

    def test_evaluation_is_unavailable_not_sixty(self):
        evaluation = core().avatar_ref.evaluate_similarity(
            str(CRUDE_RENDER), REFERENCE_ID)
        self.assertFalse(evaluation.get("available"))
        self.assertIsNone(evaluation.get("overall_score"))
        for key in ("face_score", "hair_score", "outfit_score", "style_score"):
            self.assertNotEqual(evaluation.get(key), 60)
            self.assertIsNone(evaluation.get(key))


if __name__ == "__main__":
    unittest.main(verbosity=2)
