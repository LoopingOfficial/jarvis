"""Contrats de non-régression de la refonte AvatarEngine."""
import json
import unittest
from pathlib import Path

from jarvis.avatar_engine import BUILD_ID, OPERATIONS
from jarvis.avatar_engine import identity
from jarvis.llm.base import LLMResponse
from jarvis.llm.normalize import normalize_message
from jarvis.llm.tool_calls import recover_text_tool_calls
from jarvis.llm.manager import LLMManager


class TestToolCallContract(unittest.TestCase):
    def test_text_dump_becomes_real_call_even_after_status_json(self):
        calls = recover_text_tool_calls(
            '{"status":"thinking"}\n{"name":"blender.inspect","arguments":{}}',
            {"blender.inspect"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "blender.inspect")
        self.assertEqual(calls[0].arguments, {})

    def test_native_and_double_encoded_arguments_have_one_shape(self):
        native = normalize_message({"role": "assistant", "tool_calls": [
            {"function": {"name": "avatar.engine.inspect", "arguments": {}}}]})
        encoded = normalize_message({"role": "assistant", "tool_calls": [
            {"function": {"name": "avatar.engine.inspect", "arguments": json.dumps(json.dumps({"x": 1}))}}]})
        self.assertIsInstance(native.tool_calls[0].arguments, dict)
        self.assertEqual(encoded.tool_calls[0].arguments, {"x": 1})

    def test_manager_finalizer_hides_recovered_json(self):
        response = LLMResponse(text='{"name":"blender.inspect","arguments":{}}')
        result = LLMManager._finalize_tool_response(response, [{"name": "blender.inspect"}])
        self.assertEqual(result.text, "")
        self.assertEqual(result.tool_calls[0].name, "blender.inspect")


class TestAvatarEngineContract(unittest.TestCase):
    def test_build_and_operation_catalog(self):
        self.assertEqual(BUILD_ID, "JARVIS_AVATAR_ENGINE_20260911_A")
        for operation in ("load_base", "set_body_proportions", "set_face_morphs",
                          "set_eyes", "set_hair", "set_outfit", "set_materials",
                          "ensure_rig", "ensure_face_rig", "create_visemes",
                          "create_animations", "validate", "export_glb"):
            self.assertIn(operation, OPERATIONS)

    def test_canonical_assets_exist_and_live_is_separate(self):
        self.assertTrue(identity.BASE_BLEND.is_file(), identity.BASE_BLEND)
        self.assertTrue(identity.MASTER_BLEND.is_file(), identity.MASTER_BLEND)
        self.assertNotEqual(identity.MASTER_BLEND.resolve(), identity.LIVE_GLB.resolve())

    def test_component_contract_modules_are_importable(self):
        from jarvis.avatar_engine.base import BASE_MESH_ID
        from jarvis.avatar_engine.clothing import GARMENT_OBJECTS
        from jarvis.avatar_engine.eyes import EYE_OBJECTS
        from jarvis.avatar_engine.facial import VISEME_KEYS
        from jarvis.avatar_engine.render import REQUIRED_VIEWS

        self.assertEqual(BASE_MESH_ID, "jarvis_humanoid_loft_v1")
        self.assertEqual(set(GARMENT_OBJECTS), {"shirt", "overshirt", "trousers", "belt", "shoes"})
        self.assertEqual(EYE_OBJECTS, ("Eye.L", "Eye.R"))
        self.assertIn("viseme_A", VISEME_KEYS)
        self.assertIn("three_quarter", REQUIRED_VIEWS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
