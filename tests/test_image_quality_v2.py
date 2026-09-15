"""Tests for the V2 image pipeline: planning, prompt fidelity and graph shape."""
from __future__ import annotations

import unittest

from jarvis.image_edit_graph import (
    ADD_OBJECT, OUTPAINT, RECOLOR, REMOVE, REPLACE_BACKGROUND, RESTYLE,
    UPSCALE_ONLY, EditGraphBuilder, detect_edit_operation, plan_edit,
)
from jarvis.image_quality import (
    BALANCED, FAST, GAMING, ILLUSTRATION, IMAGE_EDIT, PHOTOREAL, PORTRAIT,
    POSTER, PRODUCT, QUALITY, UI_CONCEPT, ULTRA, PromptEnricher, RenderPlanner,
    detect_image_type, detect_quality_mode, resolve_mode,
)
from jarvis.zimage_graph import GraphBuildError, ZImageGraphBuilder, assert_official_core

VRAM = 10240


class TestRouting(unittest.TestCase):
    """The right pipeline and the right effort for the right request."""

    def test_detect_image_type(self):
        cases = [
            ("Crée une affiche premium pour cet événement", POSTER),
            ("Fais-moi une image réaliste d'un vieux pêcheur", PHOTOREAL),
            ("Crée une illustration gaming d'un chevalier", GAMING),
            ("Crée une UI concept pour une app bancaire", UI_CONCEPT),
            ("Crée un visuel produit pour une bouteille de parfum", PRODUCT),
            ("fais un portrait de ma soeur", PORTRAIT),
            ("dessine un chat qui dort", ILLUSTRATION),
        ]
        for request_text, expected in cases:
            with self.subTest(request=request_text):
                self.assertEqual(detect_image_type(request_text), expected)

    def test_source_image_routes_to_edit(self):
        self.assertEqual(
            detect_image_type("retire la voiture", has_source_image=True), IMAGE_EDIT)

    def test_detect_quality_mode(self):
        cases = [
            ("fais-moi un aperçu rapide d'un chat", FAST),
            ("dessine un chat", BALANCED),
            ("je veux une image de qualité d'un chat", QUALITY),
            ("crée un visuel premium pour l'impression", ULTRA),
        ]
        for request_text, expected in cases:
            with self.subTest(request=request_text):
                self.assertEqual(detect_quality_mode(request_text), expected)

    def test_speed_wording_beats_quality_wording(self):
        """A user asking for a draft should not wait for a multi-pass render."""
        self.assertEqual(detect_quality_mode("un aperçu rapide mais de qualité"), FAST)

    def test_explicit_mode_always_wins(self):
        mode, reason = resolve_mode("FAST", "crée une affiche premium ultra détaillée")
        self.assertEqual(mode, FAST)
        self.assertIn("forcé", reason)

    def test_poster_has_a_quality_floor(self):
        """A poster is a keepsake; FAST would waste the request."""
        mode, _ = resolve_mode("", "crée une affiche pour mon concert", image_type=POSTER)
        self.assertIn(mode, {QUALITY, ULTRA})


class TestPromptFidelity(unittest.TestCase):
    """Enrichment may add, but must never alter what the user asked for."""

    def test_subject_is_never_translated(self):
        """V1 produced franglais ('moi un shark yellow neon'); V2 must not."""
        subject, _ = PromptEnricher().extract_subject("montre moi un requin jaune fluo")
        self.assertEqual(subject, "requin jaune fluo")

    def test_user_wording_survives_enrichment(self):
        cases = [
            ("crée-moi une image d'un chien roux qui court", "chien roux qui court"),
            ("génère une photo de la tour Eiffel sous la neige", "tour Eiffel sous la neige"),
            ("dessine un dragon à trois têtes", "dragon à trois têtes"),
        ]
        for request_text, must_contain in cases:
            with self.subTest(request=request_text):
                enriched = PromptEnricher().build(
                    request_text, image_type=ILLUSTRATION, mode=BALANCED)
                self.assertIn(must_contain, enriched.prompt)
                self.assertTrue(enriched.prompt.startswith(enriched.subject))

    def test_quality_wording_does_not_pollute_the_subject(self):
        subject, _ = PromptEnricher().extract_subject(
            "crée une image 4k ultra qualité d'un phare")
        self.assertNotIn("4k", subject.casefold())
        self.assertIn("phare", subject)

    def test_exact_text_is_extracted_not_drawn(self):
        enriched = PromptEnricher().build(
            'crée une affiche avec le texte "Festival 2026"', image_type=POSTER, mode=ULTRA)
        self.assertEqual(enriched.exact_text, "Festival 2026")
        self.assertIn("text_composited_downstream", enriched.notes)

    def test_negative_prompt_is_reported_inactive_at_cfg1(self):
        """Z-Image-Turbo runs at CFG 1.0, where a negative prompt does nothing."""
        enriched = PromptEnricher().build("un chat", image_type=ILLUSTRATION, mode=BALANCED)
        self.assertFalse(enriched.negative_active)
        self.assertIn("negative_prompt_inactive_at_cfg_1", enriched.notes)


class TestRenderPlans(unittest.TestCase):

    def test_modes_form_an_increasing_quality_ladder(self):
        planner = RenderPlanner()
        plans = [planner.plan("un phare", requested_mode=m, total_vram_mb=VRAM)
                 for m in (FAST, BALANCED, QUALITY, ULTRA)]
        pixels = [p.final_width * p.final_height for p in plans]
        self.assertEqual(pixels, sorted(pixels))
        self.assertEqual(plans[0].hires_width, 0)
        self.assertGreater(plans[2].hires_width, 0)
        self.assertGreater(plans[3].hires_width, 0)

    def test_fast_mode_stays_single_pass(self):
        plan = RenderPlanner().plan("un chat", requested_mode=FAST, total_vram_mb=VRAM)
        self.assertEqual(plan.stages,
                         ["PREPARING", "PROMPTING", "LOADING_MODEL", "GENERATING",
                          "SAVING", "COMPLETE"])
        self.assertNotIn("HI_RES", plan.stages)   # never advertise unrun work
        self.assertNotIn("UPSCALE", plan.stages)

    def test_quality_mode_reports_refining_stage(self):
        plan = RenderPlanner().plan("un chat", requested_mode=QUALITY, total_vram_mb=VRAM)
        self.assertIn("HI_RES", plan.stages)
        self.assertIn("LOADING_MODEL", plan.stages)

    def test_critically_low_vram_shrinks_the_plan(self):
        plan = RenderPlanner().plan("un chat", requested_mode=ULTRA,
                                    total_vram_mb=VRAM, free_vram_mb=800)
        self.assertTrue(plan.vram_adapted)
        self.assertIn("resolution_reduced_for_vram", plan.notes)

    def test_plenty_of_vram_keeps_the_hires_pass(self):
        """The measured 1536 hi-res pass fits a 10 GB card; do not pre-emptively cut it."""
        plan = RenderPlanner().plan("un chat", requested_mode=QUALITY,
                                    total_vram_mb=VRAM, free_vram_mb=6000)
        self.assertGreaterEqual(plan.hires_width, 1536)
        self.assertFalse(plan.vram_adapted)

    def test_dimensions_are_multiples_of_64(self):
        for mode in (FAST, BALANCED, QUALITY, ULTRA):
            with self.subTest(mode=mode):
                plan = RenderPlanner().plan("une affiche", requested_mode=mode,
                                            total_vram_mb=VRAM)
                self.assertEqual(plan.width % 64, 0)
                self.assertEqual(plan.height % 64, 0)


class TestGraphConstruction(unittest.TestCase):

    def test_graph_keeps_the_official_zimage_core(self):
        plan = RenderPlanner().plan("un phare", requested_mode=QUALITY, total_vram_mb=VRAM)
        graph = ZImageGraphBuilder().build(plan)
        assert_official_core(graph)
        self.assertEqual(graph["3"]["inputs"]["cfg"], 1.0)
        self.assertEqual(graph["3"]["inputs"]["sampler_name"], "res_multistep")
        self.assertEqual(graph["30"]["inputs"]["type"], "lumina2")

    def test_quality_graph_has_two_samplers_and_an_upscaler(self):
        plan = RenderPlanner().plan("un phare", requested_mode=QUALITY, total_vram_mb=VRAM)
        graph = ZImageGraphBuilder().build(plan)
        samplers = [n for n in graph.values() if n["class_type"] == "KSampler"]
        self.assertEqual(len(samplers), 2)
        self.assertTrue(any(n["class_type"] == "ImageUpscaleWithModel"
                            for n in graph.values()))
        self.assertLess(samplers[1]["inputs"]["denoise"], 0.5)  # refine, never redraw

    def test_fast_graph_has_a_single_sampler(self):
        plan = RenderPlanner().plan("un phare", requested_mode=FAST, total_vram_mb=VRAM)
        graph = ZImageGraphBuilder().build(plan)
        self.assertEqual(len([n for n in graph.values()
                              if n["class_type"] == "KSampler"]), 1)
        self.assertFalse(any(n["class_type"] == "ImageUpscaleWithModel"
                             for n in graph.values()))

    def test_every_graph_link_resolves(self):
        for mode in (FAST, BALANCED, QUALITY, ULTRA):
            with self.subTest(mode=mode):
                plan = RenderPlanner().plan("une affiche", requested_mode=mode,
                                            total_vram_mb=VRAM)
                graph = ZImageGraphBuilder().build(plan)
                for node_id, node in graph.items():
                    for key, value in node["inputs"].items():
                        if (isinstance(value, list) and len(value) == 2
                                and isinstance(value[0], str)):
                            self.assertIn(value[0], graph, f"{node_id}.{key}")

    def test_broken_core_is_rejected(self):
        plan = RenderPlanner().plan("un phare", requested_mode=FAST, total_vram_mb=VRAM)
        graph = ZImageGraphBuilder().build(plan)
        graph["3"]["inputs"]["cfg"] = 7.0
        with self.assertRaises(GraphBuildError):
            assert_official_core(graph)

    def test_edit_type_without_source_is_refused(self):
        plan = RenderPlanner().plan("retire la voiture", image_type=IMAGE_EDIT,
                                    total_vram_mb=VRAM)
        with self.assertRaises(GraphBuildError):
            ZImageGraphBuilder().build(plan)


class TestEditing(unittest.TestCase):

    def test_detect_edit_operation(self):
        cases = [
            ("retire la voiture rouge", REMOVE),
            ("change le fond pour une plage", REPLACE_BACKGROUND),
            ("mets la robe en rouge", RECOLOR),
            ("ajoute un chien à côté", ADD_OBJECT),
            ("étends l'image vers la gauche", OUTPAINT),
            ("upscale cette image", UPSCALE_ONLY),
            ("refais cette image dans un style aquarelle", RESTYLE),
        ]
        for request_text, expected in cases:
            with self.subTest(request=request_text):
                self.assertEqual(detect_edit_operation(request_text), expected)

    def test_masked_operations_get_an_automatic_mask(self):
        """Image Edit V3 supersedes the old "paint a mask first" contract.

        Segmentation now supplies the selection; the operation only fails when
        the request names a region BiRefNet cannot isolate.
        """
        plan = plan_edit(REMOVE, source_image="a.png", prompt="enlève le fond")
        self.assertIsNotNone(plan.auto_mask)
        with self.assertRaises(GraphBuildError):
            plan_edit(REMOVE, source_image="a.png", prompt="enlève la voiture")

    def test_restyle_needs_no_mask(self):
        plan = plan_edit(RESTYLE, source_image="a.png", prompt="aquarelle")
        self.assertLessEqual(plan.denoise, 0.6)  # must preserve the composition
        graph = EditGraphBuilder().build_edit(plan)
        self.assertFalse(any(n["class_type"] == "SetLatentNoiseMask"
                             for n in graph.values()))

    def test_inpaint_graph_uses_a_noise_mask(self):
        plan = plan_edit(REPLACE_BACKGROUND, source_image="a.png", mask_image="m.png",
                         prompt="plage tropicale")
        graph = EditGraphBuilder().build_edit(plan)
        self.assertTrue(any(n["class_type"] == "SetLatentNoiseMask"
                            for n in graph.values()))

    def test_outpaint_requires_padding(self):
        with self.assertRaises(GraphBuildError):
            plan_edit(OUTPAINT, source_image="a.png")
        plan = plan_edit(OUTPAINT, source_image="a.png", pad=(256, 0, 256, 0))
        graph = EditGraphBuilder().build_edit(plan)
        self.assertTrue(any(n["class_type"] == "ImagePadForOutpaint"
                            for n in graph.values()))

    def test_pure_upscale_does_not_diffuse(self):
        """An upscale must not invent content, so it carries no sampler."""
        plan = plan_edit(UPSCALE_ONLY, source_image="a.png")
        graph = EditGraphBuilder().build_edit(plan)
        self.assertFalse(any(n["class_type"] == "KSampler" for n in graph.values()))
        self.assertTrue(any(n["class_type"] == "ImageUpscaleWithModel"
                            for n in graph.values()))

    def test_add_object_is_flagged_as_less_reliable(self):
        """No inpainting checkpoint is installed; say so rather than overpromise."""
        plan = plan_edit(ADD_OBJECT, source_image="a.png", mask_image="m.png",
                         prompt="un chien")
        self.assertEqual(plan.confidence, "moderate")
        self.assertIn("no_inpainting_checkpoint_installed", plan.notes)

    def test_unknown_operation_is_refused(self):
        with self.assertRaises(GraphBuildError):
            plan_edit("teleport", source_image="a.png")


if __name__ == "__main__":
    unittest.main()
