"""Auto-réparation des erreurs JSON d'appel d'outil (backends Go, ex. Ollama)."""
import unittest

from jarvis.llm.base import ChatMessage, LLMProvider, LLMResponse
from jarvis.llm.manager import LLMManager


class FakeProvider(LLMProvider):
    type = "fake"

    def __init__(self, script):
        self._script = list(script)
        self.calls = []
        super().__init__({"id": "fake", "name": "Fake", "config": {}}, lambda *a: "")

    def chat(self, messages, **kwargs):
        self.calls.append([(m.role, m.content) for m in messages])
        return self._script.pop(0)


class StubEvents:
    def emit(self, *_a, **_k):
        pass

    def feed(self, *_a, **_k):
        pass


class StubSettings:
    def get(self, *_a, **_k):
        return ""


def make_manager():
    return LLMManager(connectors=None, vault=None, settings=StubSettings(), events=StubEvents())


class TestHealEscapeError(unittest.TestCase):
    def test_non_escape_error_is_ignored(self):
        provider = FakeProvider([])  # aucun appel attendu
        manager = make_manager()
        result = manager._heal_escape_error(
            provider, [ChatMessage("user", "bonjour")], error="HTTP 404: model introuvable",
            model="m", tools=None, temperature=0.3, max_tokens=100, timeout=10)
        self.assertIsNone(result)
        self.assertEqual(provider.calls, [])

    def test_heal_retries_without_polluting_messages(self):
        boom = LLMResponse(error='HTTP 500: {"error":"invalid character \';\' in string escape code"}')
        ok = LLMResponse(text="C'est fait.")
        provider = FakeProvider([boom, ok])
        manager = make_manager()
        base = [ChatMessage("user", "regarde"), ChatMessage("tool", "sortie", tool_call_id="c1", name="shell")]
        result = manager._heal_escape_error(
            provider, base, error=boom.error, model="m", tools=None,
            temperature=0.3, max_tokens=100, timeout=10)
        self.assertTrue(result.ok)
        self.assertEqual(result.text, "C'est fait.")
        # Les messages d'origine ne sont pas modifiés (consigne sur copie).
        self.assertEqual(base[1].content, "sortie")
        self.assertEqual([(m.role, m.content) for m in base],
                         [("user", "regarde"), ("tool", "sortie")])
        # Deux appels : un brut (sans consigne) puis un avec consigne.
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(provider.calls[0], [("user", "regarde"), ("tool", "sortie")])
        self.assertIn("Correction JSON", provider.calls[1][-1][1])

    def test_heal_gives_up_when_always_failing(self):
        boom = LLMResponse(error='HTTP 500: {"error":"invalid character \';\' in string escape code"}')
        provider = FakeProvider([boom, boom, boom])
        manager = make_manager()
        result = manager._heal_escape_error(
            provider, [], error=boom.error, model="m", tools=None,
            temperature=0.3, max_tokens=100, timeout=10)
        self.assertIsNone(result)
        self.assertEqual(len(provider.calls), 3)

    def test_heal_stops_on_unrelated_error(self):
        boom = LLMResponse(error='HTTP 500: {"error":"invalid character \';\' in string escape code"}')
        other = LLMResponse(error="HTTP 429: rate limit")
        provider = FakeProvider([boom, other])
        manager = make_manager()
        result = manager._heal_escape_error(
            provider, [], error=boom.error, model="m", tools=None,
            temperature=0.3, max_tokens=100, timeout=10)
        self.assertIsNone(result)
        self.assertEqual(len(provider.calls), 2)


if __name__ == "__main__":
    unittest.main()