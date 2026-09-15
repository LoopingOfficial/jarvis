"""Fast Action Router : reponses instantanees sans LLM ni outils."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis.core import JarvisCore
from jarvis.fast_actions import FastActionRouter, tools_needed
from jarvis.llm.base import LLMResponse


class BaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.core = JarvisCore(db_path=Path(self._tmp.name) / "fast.db")
        self.core.settings.update("general", {
            "user_name": "Jerome",
            "assistant_name": "JARVIS",
            "operator_title": "Commander",
        })
        self.router = FastActionRouter(self.core)

    def tearDown(self) -> None:
        try:
            self.core.db.close()
        except Exception:
            pass
        try:
            self._tmp.cleanup()
        except Exception:
            pass


class TestConversationMatch(BaseCase):
    def test_greeting(self):
        self.assertEqual(self.router.match_conversation("bonjour"), "greeting")
        self.assertEqual(self.router.match_conversation("Bonjour !"), "greeting")
        self.assertEqual(self.router.match_conversation("salut jarvis"), "greeting")

    def test_identity(self):
        self.assertEqual(self.router.match_conversation("qui es-tu ?"), "identity")
        self.assertEqual(self.router.match_conversation("Jarvis qui es tu"), "identity")

    def test_user_name(self):
        self.assertEqual(self.router.match_conversation("comment je m'appelle ?"), "user_name")
        self.assertEqual(self.router.match_conversation("quel est mon nom"), "user_name")

    def test_compound_falls_through(self):
        self.assertIsNone(self.router.match_conversation(
            "bonjour, ouvre Edge et resume mes mails"))

    def test_how_are_you(self):
        self.assertEqual(self.router.match_conversation("comment tu vas"), "how_are_you")


class TestExecute(BaseCase):
    def test_user_name_from_profile(self):
        out = self.router.execute("comment je m'appelle ?")
        self.assertIsNotNone(out)
        self.assertIn("Jerome", out["response"])
        self.assertEqual(out["fast_intent"], "user_name")
        self.assertEqual(out["tools_used"], [])
        self.assertLess(out["latency_ms"], 200)

    def test_identity_from_profile(self):
        out = self.router.execute("qui es-tu ?")
        self.assertIn("JARVIS", out["response"])
        self.assertEqual(out["fast_intent"], "identity")

    def test_greeting_bypass(self):
        out = self.router.execute("bonjour")
        self.assertTrue(out["ok"])
        self.assertIn("Jerome", out["response"])

    def test_name_from_memory_when_profile_empty(self):
        self.core.settings.update("general", {"user_name": ""})
        self.core.memory.add(content="Prénom : Alice", scope="user", importance=4,
                             source="test")
        out = self.router.execute("comment je m'appelle ?")
        self.assertIn("Alice", out["response"])


class TestToolsNeeded(unittest.TestCase):
    def test_chitchat_needs_no_tools(self):
        self.assertFalse(tools_needed("bonjour"))
        self.assertFalse(tools_needed("comment je m'appelle ?"))
        self.assertFalse(tools_needed("explique-moi la relativite"))

    def test_actions_need_tools(self):
        self.assertTrue(tools_needed("ouvre le navigateur"))
        self.assertTrue(tools_needed("affiche marketplace.php"))
        self.assertTrue(tools_needed("cherche sur internet le prix du gaz"))
        self.assertTrue(tools_needed("connecte-toi en ssh"))


class TestOrchestratorBypass(BaseCase):
    def test_simple_questions_never_call_llm(self):
        with patch.object(self.core.llm, "chat") as chat:
            with patch.object(self.core.llm, "chat_stream") as stream:
                for text in ("bonjour", "qui es-tu ?", "comment je m'appelle ?"):
                    result = self.core.orchestrator.handle(text, conversation_id="c1")
                    self.assertTrue(result["ok"], text)
                    self.assertIn(result.get("fast_intent"),
                                  {"greeting", "identity", "user_name"}, text)
                    self.assertEqual(result.get("tools_used"), [])
        chat.assert_not_called()
        stream.assert_not_called()

    def test_complex_still_uses_llm_without_tools(self):
        with patch.object(self.core.llm, "chat_stream",
                          return_value=LLMResponse(text="Un dragon garde la montagne...")) as stream:
            result = self.core.orchestrator.handle(
                "raconte-moi une histoire de dragon", conversation_id="c2")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result.get("tools_used"), [])
        self.assertEqual(result.get("action"), "fast.llm")
        stream.assert_called_once()
        self.assertIsNone(stream.call_args.kwargs.get("tools"))

    def test_real_action_keeps_agent_tools(self):
        with patch.object(self.core.llm, "chat",
                          return_value=LLMResponse(text="D'accord.")) as chat:
            with patch.object(self.core.llm, "chat_stream",
                              side_effect=AssertionError("stream ne doit pas remplacer l'agent")):
                result = self.core.orchestrator.handle(
                    "cherche des infos sur l intelligence artificielle",
                    conversation_id="c3")
        self.assertTrue(result["ok"], result)
        chat.assert_called()
        tools = chat.call_args.kwargs.get("tools")
        self.assertTrue(tools)


if __name__ == "__main__":
    unittest.main()
