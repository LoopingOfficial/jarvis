"""Contracts for automatic fast/quality image-engine routing."""
import unittest

from jarvis.image_engines import (FAST_ENGINE, QUALITY_ENGINE,
                                  SDXLWorkflowAdapter, choose_image_engine)


class TestImageEngineChoice(unittest.TestCase):
    def test_auto_final_prefers_quality(self):
        self.assertEqual(choose_image_engine("fais-moi une image").mode, QUALITY_ENGINE)

    def test_auto_preview_uses_fast(self):
        self.assertEqual(choose_image_engine("fais un aperçu").mode, FAST_ENGINE)

    def test_explicit_modes_win(self):
        self.assertEqual(choose_image_engine("image finale", requested_mode="fast").mode, FAST_ENGINE)
        self.assertEqual(choose_image_engine("brouillon", requested_mode="quality").mode, QUALITY_ENGINE)


class TestSDXLWorkflowAdapter(unittest.TestCase):
    def test_txt2img_injects_positive_negative_and_sampler(self):
        path = SDXLWorkflowAdapter.resolve_path(
            "workflows/comfyui/sdxl_quality_txt2img.json", "")
        workflow = SDXLWorkflowAdapter(path).compile(
            prompt="red sports car", negative_prompt="blurry", width=1024, height=1024,
            seed=42, steps=35, cfg=7.0, sampler="dpmpp_2m", scheduler="karras",
            checkpoint="sd_xl_base_1.0.safetensors")
        self.assertEqual(workflow["3"]["inputs"]["text"], "red sports car")
        self.assertEqual(workflow["4"]["inputs"]["text"], "blurry")
        self.assertEqual(workflow["5"]["inputs"]["seed"], 42)
        self.assertEqual(workflow["5"]["inputs"]["steps"], 35)
        self.assertNotIn("8", workflow)  # empty optional VAE uses checkpoint VAE

    def test_img2img_injects_uploaded_source(self):
        path = SDXLWorkflowAdapter.resolve_path(
            "workflows/comfyui/sdxl_quality_img2img.json", "")
        workflow = SDXLWorkflowAdapter(path).compile(
            prompt="red sports car", negative_prompt="blurry", width=1024, height=1024,
            seed=42, steps=35, cfg=7.0, sampler="dpmpp_2m", scheduler="karras",
            checkpoint="sd_xl_base_1.0.safetensors", input_image="jarvis_source.png")
        self.assertEqual(workflow["2"]["inputs"]["image"], "jarvis_source.png")
        self.assertEqual(workflow["6"]["inputs"]["latent_image"], ["3", 0])


if __name__ == "__main__":
    unittest.main()
