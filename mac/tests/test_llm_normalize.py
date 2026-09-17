"""Tests de regression du bug `'dict' object has no attribute 'role'`.

Ce bug faisait echouer silencieusement l'analyse de reference et l'evaluation
d'avatar, qui repartaient alors sur des valeurs par defaut : l'avatar genere
ne ressemblait a rien et les scores etaient des 60 fabriques.
"""
from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.llm.base import ChatMessage, LLMResponse, messages_to_ollama, messages_to_openai
from jarvis.llm.normalize import (normalize_llm_response, normalize_message,
                                  normalize_messages)


@dataclass
class Message:
    """Message facon dataclass externe / provider."""
    role: str
    content: str


class PydanticLike:
    """Objet expose via model_dump(), comme Pydantic v2."""

    def __init__(self, role, content):
        self._data = {"role": role, "content": content}

    def model_dump(self):
        return dict(self._data)


class TestNormalizeMessage(unittest.TestCase):
    def test_dict(self):
        m = normalize_message({"role": "user", "content": "test"})
        self.assertIsInstance(m, ChatMessage)
        self.assertEqual((m.role, m.content), ("user", "test"))

    def test_dataclass_object(self):
        m = normalize_message(Message(role="user", content="test"))
        self.assertEqual((m.role, m.content), ("user", "test"))

    def test_pydantic_like(self):
        m = normalize_message(PydanticLike("assistant", "salut"))
        self.assertEqual((m.role, m.content), ("assistant", "salut"))

    def test_chatmessage_passthrough(self):
        original = ChatMessage(role="system", content="sys")
        self.assertIs(normalize_message(original), original)

    def test_plain_string(self):
        self.assertEqual(normalize_message("coucou").role, "user")

    def test_role_defaults_and_aliases(self):
        self.assertEqual(normalize_message({"content": "x"}).role, "user")
        self.assertEqual(normalize_message({"role": "model", "content": "x"}).role, "assistant")
        self.assertEqual(normalize_message({"role": "bogus", "content": "x"}).role, "user")

    def test_block_content_is_flattened(self):
        m = normalize_message({"role": "user", "content": [
            {"type": "text", "text": "a"}, {"type": "text", "text": "b"}]})
        self.assertEqual(m.content, "ab")

    def test_images_strip_data_uri_prefix(self):
        m = normalize_message({"role": "user", "content": "c",
                               "images": ["data:image/png;base64,AAAA", "BBBB"]})
        self.assertEqual(m.images, ["AAAA", "BBBB"])

    def test_tool_calls_from_dicts(self):
        m = normalize_message({"role": "assistant", "content": "", "tool_calls": [
            {"id": "1", "function": {"name": "f", "arguments": '{"a": 1}'}}]})
        self.assertEqual(m.tool_calls[0].name, "f")
        self.assertEqual(m.tool_calls[0].arguments, {"a": 1})


class TestNormalizeMessages(unittest.TestCase):
    def test_mixed_list(self):
        out = normalize_messages([
            {"role": "system", "content": "s"},
            Message(role="user", content="u"),
            ChatMessage(role="assistant", content="a"),
            "brut",
        ])
        self.assertEqual([m.role for m in out], ["system", "user", "assistant", "user"])

    def test_single_dict_not_a_list(self):
        self.assertEqual(len(normalize_messages({"role": "user", "content": "x"})), 1)

    def test_none(self):
        self.assertEqual(normalize_messages(None), [])


class TestProviderEncodersAcceptNormalized(unittest.TestCase):
    """C'est exactement ici que le bug se manifestait."""

    def test_dicts_crash_encoder_without_normalization(self):
        with self.assertRaises(AttributeError):
            messages_to_openai([{"role": "user", "content": "test"}])

    def test_normalized_dicts_work(self):
        out = messages_to_openai(normalize_messages([{"role": "user", "content": "test"}]))
        self.assertEqual(out, [{"role": "user", "content": "test"}])

    def test_openai_encodes_images_multimodal(self):
        out = messages_to_openai(normalize_messages(
            [{"role": "user", "content": "décris", "images": ["QUJD"]}]))
        blocks = out[0]["content"]
        self.assertEqual(blocks[0]["type"], "text")
        self.assertEqual(blocks[1]["type"], "image_url")
        self.assertIn("base64,QUJD", blocks[1]["image_url"]["url"])

    def test_ollama_encodes_images_natively(self):
        out = messages_to_ollama(normalize_messages(
            [{"role": "user", "content": "décris", "images": ["QUJD"]}]))
        self.assertEqual(out[0]["images"], ["QUJD"])


class TestNormalizeResponse(unittest.TestCase):
    def test_ollama_shape(self):
        r = normalize_llm_response({"message": {"role": "assistant", "content": "ok"},
                                    "model": "m"})
        self.assertEqual(r.text, "ok")
        self.assertEqual(r.model, "m")

    def test_openai_shape(self):
        r = normalize_llm_response({"choices": [{"message": {"role": "assistant",
                                                             "content": "ok"}}]})
        self.assertEqual(r.text, "ok")

    def test_passthrough(self):
        original = LLMResponse(text="x")
        self.assertIs(normalize_llm_response(original), original)

    def test_error_is_preserved(self):
        self.assertFalse(normalize_llm_response({"error": "boom"}).ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
