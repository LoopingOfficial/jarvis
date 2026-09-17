"""Tests for the Quality Validation V2 outcomes and poster text compositing.

These lock in decisions that were *measured*, so a later "improvement" that
quietly reintroduces a rejected setting fails loudly.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from jarvis.image_quality import (
    BALANCED, FAST, POSTER, QUALITY, QUALITY_MODE_SPECS, ULTRA,
    PromptEnricher, RenderPlanner, estimate_budget_s,
)
from jarvis.image_runtime import ImageRenderer
from jarvis.poster_text import (
    PIL_AVAILABLE, BANDS, PosterLayout, PosterTextError, TextBlock,
    compose_poster_text, find_font, layout_from_lines,
)

VRAM = 10240


class TestValidatedProfiles(unittest.TestCase):
    """Guard the settings the sweeps actually justified."""

    def test_no_profile_exceeds_twelve_steps(self):
        """Test A: past 12 steps the distilled sampler over-densifies texture."""
        for name, spec in QUALITY_MODE_SPECS.items():
            with self.subTest(mode=name):
                self.assertLessEqual(spec.base_steps, 12)

    def test_hires_denoise_stays_in_the_measured_band(self):
        """Test B: 0.20-0.30. Above it faces over-texture and geometry drifts."""
        for name, spec in QUALITY_MODE_SPECS.items():
            if not spec.hires_scale:
                continue
            with self.subTest(mode=name):
                self.assertGreaterEqual(spec.hires_denoise, 0.20)
                self.assertLessEqual(spec.hires_denoise, 0.30)

    def test_sharpening_is_disabled_everywhere(self):
        """Test D: 0.10 was invisible, 0.25 produced halos. Removed."""
        for name, spec in QUALITY_MODE_SPECS.items():
            with self.subTest(mode=name):
                self.assertEqual(spec.sharpen, 0.0)

    def test_base_resolution_never_reaches_the_degrading_range(self):
        """Native renders at 1536+ degraded in V1; first passes stay below."""
        for mode in (FAST, BALANCED, QUALITY, ULTRA):
            with self.subTest(mode=mode):
                plan = RenderPlanner().plan("une machine", requested_mode=mode,
                                            total_vram_mb=VRAM)
                self.assertLessEqual(max(plan.width, plan.height), 1408)

    def test_ultra_is_not_merely_a_slower_quality(self):
        """ULTRA must differ only where a measurement justified the extra cost."""
        quality = QUALITY_MODE_SPECS[QUALITY]
        ultra = QUALITY_MODE_SPECS[ULTRA]
        self.assertEqual(ultra.base_steps, quality.base_steps)  # Test A
        self.assertGreater(ultra.resolution_scale, quality.resolution_scale)  # Test C
        self.assertGreater(ultra.post_upscale, quality.post_upscale)  # Test D

    def test_ultra_is_cheaper_than_the_unvalidated_version(self):
        """The rebuild dropped steps, denoise and sharpening: it must not regress."""
        ultra = QUALITY_MODE_SPECS[ULTRA]
        self.assertLess(ultra.base_steps, 20)
        self.assertLess(ultra.hires_denoise, 0.38)


class TestSeedReproducibility(unittest.TestCase):

    def test_plan_resolves_its_own_seed(self):
        """A recorded seed of 0 made liked images impossible to reproduce."""
        plan = RenderPlanner().plan("un phare", total_vram_mb=VRAM)
        self.assertGreater(plan.seed, 0)

    def test_explicit_seed_is_honoured(self):
        plan = RenderPlanner().plan("un phare", seed=4242, total_vram_mb=VRAM)
        self.assertEqual(plan.seed, 4242)

    def test_same_seed_gives_the_same_plan(self):
        a = RenderPlanner().plan("un phare", seed=7, requested_mode=QUALITY,
                                 total_vram_mb=VRAM)
        b = RenderPlanner().plan("un phare", seed=7, requested_mode=QUALITY,
                                 total_vram_mb=VRAM)
        self.assertEqual(a.as_dict(), b.as_dict())


class TestBudget(unittest.TestCase):
    """A flat budget killed a legitimate poster render mid-flight."""

    def test_budget_follows_the_workload(self):
        small = estimate_budget_s(1024, 1024, 8)
        large = estimate_budget_s(1152, 1536, 12, hires_width=1728,
                                  hires_height=2304, hires_steps=12,
                                  post_upscale=1.3)
        self.assertGreater(large, small)

    def test_budget_covers_a_cold_model_load(self):
        """The 12 GB checkpoint reload alone can exceed a warm render."""
        self.assertGreaterEqual(estimate_budget_s(512, 512, 4), 150)

    def test_poster_budget_exceeds_the_value_that_failed(self):
        plan = RenderPlanner().plan("une affiche", requested_mode=BALANCED,
                                    image_type=POSTER, total_vram_mb=VRAM)
        self.assertGreater(plan.budget_s, 120)


class TestOOMDetection(unittest.TestCase):
    """ComfyUI does not always say 'out of memory'."""

    def test_dynamic_loader_failures_count_as_oom(self):
        messages = [
            "Échec ComfyUI (KSampler) : Fault failed: 2",
            "VRAM Allocation failed (non OOM)",
            "CUDA API FAILED (600): device not ready",
            "torch.cuda.OutOfMemoryError: CUDA out of memory",
        ]
        for message in messages:
            with self.subTest(message=message[:30]):
                self.assertTrue(ImageRenderer._is_oom(Exception(message)))

    def test_unrelated_errors_are_not_treated_as_oom(self):
        self.assertFalse(ImageRenderer._is_oom(Exception("prompt outputs failed validation")))


class TestPosterNeverLetters(unittest.TestCase):

    def test_poster_prompt_forbids_lettering_even_without_exact_text(self):
        """A giveaway poster came back stamped 'GIVAWAY' twice; never again."""
        enriched = PromptEnricher().build("affiche pour un giveaway",
                                          image_type=POSTER, mode=ULTRA)
        self.assertIn("Do not draw any text", enriched.prompt)
        self.assertIn("text_composited_downstream", enriched.notes)


@unittest.skipUnless(PIL_AVAILABLE, "Pillow requis")
class TestPosterTextCompositing(unittest.TestCase):

    def _poster(self, directory: str) -> str:
        from PIL import Image
        path = Path(directory) / "poster.png"
        Image.new("RGB", (600, 900), (20, 30, 70)).save(path)
        return str(path)

    def test_composes_and_writes_a_new_file(self):
        with TemporaryDirectory() as tmp:
            source = self._poster(tmp)
            out = compose_poster_text(
                source, layout_from_lines(["Titre", "Sous-titre"]),
                Path(tmp) / "out.png")
            self.assertTrue(Path(out).is_file())

    def test_original_is_left_untouched(self):
        with TemporaryDirectory() as tmp:
            source = self._poster(tmp)
            before = Path(source).read_bytes()
            compose_poster_text(source, layout_from_lines(["Titre"]),
                                Path(tmp) / "out.png")
            self.assertEqual(Path(source).read_bytes(), before)

    def test_band_repaint_erases_residual_lettering(self):
        """Determinism: the model may still draw text despite the instruction."""
        from PIL import Image, ImageDraw
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "poster.png"
            image = Image.new("RGB", (600, 900), (20, 30, 70))
            top = int(900 * BANDS["top"][0]), int(900 * BANDS["top"][1])
            ImageDraw.Draw(image).rectangle(
                [0, top[0] + 5, 600, top[1] - 5], fill=(255, 255, 255))
            image.save(path)
            out = compose_poster_text(
                str(path), PosterLayout(blocks=[TextBlock("X", band="top")]),
                Path(tmp) / "out.png")
            result = Image.open(out).convert("RGB")
            # A corner of the band, away from the glyph, must be background again.
            self.assertEqual(result.getpixel((8, (top[0] + top[1]) // 2)), (20, 30, 70))

    def test_missing_image_is_refused(self):
        with self.assertRaises(PosterTextError):
            compose_poster_text("nope.png", layout_from_lines(["Titre"]))

    def test_empty_text_is_refused(self):
        with self.assertRaises(PosterTextError):
            layout_from_lines(["", "  "])

    def test_a_font_is_available_on_this_machine(self):
        self.assertIsNotNone(find_font())


if __name__ == "__main__":
    unittest.main()
