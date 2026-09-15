"""Tests for Image Edit V3: automatic segmentation, masks and subject fidelity."""
from __future__ import annotations

import unittest

from jarvis.image_edit_graph import (
    RECOLOR, REMOVE, REPLACE_BACKGROUND, RESTYLE, UPSCALE_ONLY,
    EditGraphBuilder, _strip_subject_clauses, plan_edit,
)
from jarvis.image_segmentation import (
    BACKGROUND, SUBJECT, MaskPlan, SegmentationUnavailable, build_cutout_graph,
    build_mask_nodes, build_preview_graph, build_rect_feather_nodes,
    needs_text_segmentation, plan_mask, supports_request, wants_background_split,
)
from jarvis.zimage_graph import GraphBuildError


class TestRequestUnderstanding(unittest.TestCase):

    def test_background_phrasings_are_recognised(self):
        for request in ["change seulement le fond",
                        "remplace l'arrière-plan par une plage",
                        "garde exactement le personnage",
                        "détoure cette image"]:
            with self.subTest(request=request):
                self.assertTrue(wants_background_split(request))

    def test_named_regions_are_refused_not_guessed(self):
        """BiRefNet splits subject/background only; anything else must say so."""
        for request in ["enlève l'objet à gauche",
                        "modifie uniquement le ciel",
                        "change la voiture de couleur"]:
            with self.subTest(request=request):
                self.assertTrue(needs_text_segmentation(request))
                ok, reason = supports_request(request)
                self.assertFalse(ok)
                self.assertIn("BiRefNet", reason)

    def test_background_requests_are_supported(self):
        ok, _ = supports_request("change seulement le fond")
        self.assertTrue(ok)


class TestMaskPlanning(unittest.TestCase):

    def test_background_edit_targets_the_background(self):
        plan = plan_mask(REPLACE_BACKGROUND, "remplace le fond")
        self.assertEqual(plan.target, BACKGROUND)

    def test_cutout_targets_the_subject(self):
        self.assertEqual(plan_mask("cutout", "détoure le sujet").target, SUBJECT)

    def test_background_mask_bites_into_the_subject_edge(self):
        """A positive grow stops a halo of old background surviving the edit."""
        self.assertGreater(plan_mask(REPLACE_BACKGROUND, "change le fond").grow, 0)

    def test_subject_mask_erodes_instead(self):
        self.assertLess(plan_mask("recolor_subject", "recolore le produit").grow, 0)

    def test_user_can_force_the_target(self):
        plan = plan_mask(REPLACE_BACKGROUND, "change le fond", target=SUBJECT)
        self.assertEqual(plan.target, SUBJECT)
        self.assertIn("target_forced_by_user", plan.notes)

    def test_unsupported_request_raises(self):
        with self.assertRaises(SegmentationUnavailable):
            plan_mask(REMOVE, "enlève l'objet à gauche")


class TestMaskGraphs(unittest.TestCase):

    def test_background_mask_is_inverted_once(self):
        nodes, _ = build_mask_nodes(MaskPlan(target=BACKGROUND), "a.png")
        inverts = [n for n in nodes.values() if n["class_type"] == "InvertMask"]
        self.assertEqual(len(inverts), 1)

    def test_subject_mask_is_not_inverted(self):
        nodes, _ = build_mask_nodes(MaskPlan(target=SUBJECT), "a.png")
        self.assertFalse(any(n["class_type"] == "InvertMask" for n in nodes.values()))

    def test_silhouette_masks_never_use_feathermask(self):
        """FeatherMask fades the image borders, not the silhouette.

        Measured: it dropped a background mask's outer 10 px from 254 to 22-50,
        which would leave a frame of untouched old background.
        """
        nodes, _ = build_mask_nodes(MaskPlan(target=BACKGROUND, feather=12), "a.png")
        self.assertFalse(any(n["class_type"] == "FeatherMask" for n in nodes.values()))

    def test_rectangular_masks_may_feather(self):
        nodes: dict = {}
        tail = build_rect_feather_nodes(nodes, ["9", 0], 16)
        self.assertTrue(any(n["class_type"] == "FeatherMask" for n in nodes.values()))
        self.assertNotEqual(tail, ["9", 0])

    def test_preview_graph_saves_an_image(self):
        graph = build_preview_graph(MaskPlan(), "a.png")
        self.assertTrue(any(n["class_type"] == "MaskToImage" for n in graph.values()))
        self.assertTrue(any(n["class_type"] == "SaveImage" for n in graph.values()))

    def test_cutout_graph_never_diffuses(self):
        """A cutout is the original pixels plus alpha; nothing may be invented."""
        graph = build_cutout_graph("a.png")
        self.assertFalse(any(n["class_type"] == "KSampler" for n in graph.values()))
        self.assertTrue(any(n["class_type"] == "JoinImageWithAlpha"
                            for n in graph.values()))

    def test_graph_links_all_resolve(self):
        for graph in (build_preview_graph(MaskPlan(), "a.png"),
                      build_cutout_graph("a.png")):
            for node_id, node in graph.items():
                for key, value in node["inputs"].items():
                    if (isinstance(value, list) and len(value) == 2
                            and isinstance(value[0], str)):
                        self.assertIn(value[0], graph, f"{node_id}.{key}")


class TestAutomaticEditing(unittest.TestCase):

    def test_background_change_no_longer_needs_a_hand_made_mask(self):
        plan = plan_edit(REPLACE_BACKGROUND, source_image="a.png",
                         prompt="remplace le fond par une plage")
        self.assertIsNotNone(plan.auto_mask)
        self.assertEqual(plan.auto_mask.target, BACKGROUND)
        self.assertIn("auto_mask:background", plan.notes)

    def test_named_object_edit_still_refuses_clearly(self):
        with self.assertRaises(GraphBuildError) as ctx:
            plan_edit(REMOVE, source_image="a.png", prompt="enlève l'objet à gauche")
        self.assertIn("BiRefNet", str(ctx.exception))

    def test_subject_is_composited_back_over_a_background_edit(self):
        """Fidelity contract: a local edit must not be a re-generation."""
        plan = plan_edit(REPLACE_BACKGROUND, source_image="a.png",
                         prompt="remplace le fond par une plage")
        graph = EditGraphBuilder().build_edit(plan)
        self.assertTrue(any(n["class_type"] == "ImageCompositeMasked"
                            for n in graph.values()))

    def test_restyle_is_not_composited(self):
        """Restyle changes the whole image on purpose, so nothing is pasted back."""
        plan = plan_edit(RESTYLE, source_image="a.png", prompt="aquarelle")
        graph = EditGraphBuilder().build_edit(plan)
        self.assertFalse(any(n["class_type"] == "ImageCompositeMasked"
                             for n in graph.values()))

    def test_background_prompt_drops_subject_clauses(self):
        """Naming a subject in the masked region made the model draw a second one."""
        cleaned = _strip_subject_clauses(
            "Garde exactement le sujet. Remplace le fond par une plage.")
        self.assertNotIn("sujet", cleaned.casefold())
        self.assertIn("plage", cleaned)

    def test_background_edit_prompt_mentions_no_subject(self):
        plan = plan_edit(REPLACE_BACKGROUND, source_image="a.png",
                         prompt="Garde exactement le personnage. Remplace le fond par une plage")
        self.assertNotIn("personnage", plan.prompt.casefold())

    def test_explicit_mask_still_wins_over_segmentation(self):
        plan = plan_edit(RECOLOR, source_image="a.png", mask_image="m.png",
                         prompt="mets-le en rouge")
        self.assertIsNone(plan.auto_mask)

    def test_upscale_needs_neither_mask_nor_segmentation(self):
        plan = plan_edit(UPSCALE_ONLY, source_image="a.png")
        self.assertIsNone(plan.auto_mask)


if __name__ == "__main__":
    unittest.main()
