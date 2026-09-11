"""Tests for the immutable official Z-Image-Turbo execution path."""
import unittest

from jarvis.zimage_adapter import GOLDEN_WORKFLOW_ID, ZImageTurboAdapter
from jarvis.intents import detect_image_intent


class TestZImageTurboGolden(unittest.TestCase):
    def test_show_single_visual_is_generation_but_web_image_search_is_not(self):
        self.assertEqual(detect_image_intent("montre moi un requin jaune fluo")["action"],
                         "image.generate")
        self.assertIsNone(detect_image_intent("montre-moi des images de requin"))

    def test_only_allowed_inputs_are_injected(self):
        wf = ZImageTurboAdapter().compile(
            "a fluorescent yellow shark swimming underwater, full body",
            width=1024, height=1024, seed=123)
        self.assertEqual(wf["27"]["inputs"]["text"],
                         "a fluorescent yellow shark swimming underwater, full body")
        self.assertEqual(wf["3"]["inputs"]["seed"], 123)
        self.assertEqual(wf["3"]["inputs"]["sampler_name"], "res_multistep")
        self.assertEqual(wf["11"]["inputs"]["shift"], 3.0)
        self.assertEqual(wf["33"]["class_type"], "ConditioningZeroOut")
        self.assertEqual(ZImageTurboAdapter.metadata()["workflow"], GOLDEN_WORKFLOW_ID)

    def test_golden_graph_rejects_node_drift(self):
        wf = ZImageTurboAdapter().load()
        wf["11"]["inputs"]["shift"] = 1.0
        with self.assertRaises(ValueError):
            ZImageTurboAdapter.validate_fixed_graph(wf)


if __name__ == "__main__":
    unittest.main()
