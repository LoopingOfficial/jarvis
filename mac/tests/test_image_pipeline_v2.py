"""Contract tests for the Image Generation Engine V2 decision layer."""
import unittest

from jarvis.image_pipeline import (
    ComfyWorkflowCompiler, GenerationProfileFactory, ImageIntentAnalyzer,
    ImageModelRegistry, ModelSelector, NegativePromptBuilder, PromptComposer,
    WorkflowSelector,
)
from jarvis.imagegen import zimage_workflow


class TestImagePipelineV2(unittest.TestCase):
    def setUp(self):
        self.detection = {
            "files": {
                "diffusion_models": ["z_image_turbo_bf16.safetensors"],
                "text_encoders": ["qwen_3_4b.safetensors"],
                "vae": ["ae.safetensors"],
            },
            "engines": [{"id": "zimage", "model": "z_image_turbo_bf16.safetensors",
                         "text_encoder": "qwen_3_4b.safetensors", "vae": "ae.safetensors"}],
        }

    def test_short_french_request_keeps_subject_and_adds_context(self):
        intent = ImageIntentAnalyzer().analyze("montre moi un requin rose")
        self.assertEqual(intent.subject, "pink shark")
        self.assertEqual(intent.scene, "underwater ocean")
        self.assertEqual(intent.colors, ["pink", "blue"])
        prompt = PromptComposer().compose(intent)
        self.assertIn("pink shark", prompt)
        self.assertNotIn("8k", prompt.lower())
        self.assertIn("extra fins", NegativePromptBuilder().build(intent))

    def test_real_installed_model_and_architecture_profile(self):
        intent = ImageIntentAnalyzer().analyze("montre moi un requin rose")
        registry = ImageModelRegistry(self.detection)
        model = ModelSelector().select(intent, registry)
        self.assertEqual(model.id, "z_image_turbo_bf16.safetensors")
        self.assertEqual(model.architecture, "Z-Image-Turbo / Lumina2")
        profile = GenerationProfileFactory().create(intent, model)
        self.assertEqual((profile.width, profile.height), (1024, 1024))
        self.assertEqual((profile.steps, profile.cfg, profile.sampler, profile.scheduler),
                         (8, 1.0, "euler", "simple"))
        self.assertEqual(WorkflowSelector().select(intent, model).id, "text2image_fast")

    def test_compiler_rejects_absent_model_and_accepts_valid_workflow(self):
        registry = ImageModelRegistry(self.detection)
        wf = zimage_workflow(prompt="pink shark", negative_prompt="blurry", width=1024, height=1024,
                             seed=42, steps=8, cfg=1.0, sampler="euler", scheduler="simple",
                             model="z_image_turbo_bf16.safetensors", text_encoder="qwen_3_4b.safetensors",
                             vae="ae.safetensors")
        ComfyWorkflowCompiler().validate(wf, registry=registry)
        wf["10"]["inputs"]["unet_name"] = "invented.safetensors"
        with self.assertRaises(ValueError):
            ComfyWorkflowCompiler().validate(wf, registry=registry)


if __name__ == "__main__":
    unittest.main()
