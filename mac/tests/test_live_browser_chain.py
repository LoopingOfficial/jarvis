"""Chaîne Live Browser : LLM → tool registry → BrowserManager.

Régression : le chat principal (orchestrator) ne voit que les outils du
registre principal (jarvis.tools.base.registry). Les outils browser.* vivent
désormais dans ce registre et doivent être exposés à l'agent « jarvis » ainsi
qu'à l'agent « browser », en pilotant la SESSION PARTAGÉE (pas un second
Chromium). Une requête « ouvre <URL> dans le navigateur » doit garder les outils.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis.core import JarvisCore
from jarvis.message_context import predict_intent
from jarvis.tools.base import registry

BROWSER_TOOLS = ("browser.navigate", "browser.click", "browser.type",
                 "browser.scroll", "browser.wait", "browser.back",
                 "browser.pause", "browser.close")


class BrowserChainCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.core = JarvisCore(db_path=Path(self._tmp.name) / "browser.db")

    def tearDown(self) -> None:
        try:
            self.core.db.close()
        except Exception:
            pass
        try:
            self._tmp.cleanup()
        except Exception:
            pass


class TestChatRegistry(BrowserChainCase):
    def test_browser_tools_are_in_the_main_registry(self):
        for tool_id in BROWSER_TOOLS:
            with self.subTest(tool=tool_id):
                tool = registry.get(tool_id)
                self.assertIsNotNone(tool, f"{tool_id} absent du registre principal")
                self.assertTrue(tool.enabled, tool_id)

    def test_browser_tools_are_exposed_to_jarvis_core(self):
        ids = {t.id for t in registry.for_agent("jarvis")}
        for tool_id in BROWSER_TOOLS:
            self.assertIn(tool_id, ids, f"{tool_id} non exposé au chat principal")

    def test_browser_tools_have_llm_schema(self):
        for tool_id in BROWSER_TOOLS:
            with self.subTest(tool=tool_id):
                schema = registry.get(tool_id).llm_schema()
                self.assertEqual(schema["name"], tool_id)
                self.assertIn("description", schema)
                self.assertIn("input_schema", schema)

    def test_browser_agent_has_browser_tools(self):
        ids = {t.id for t in self.core.agents.allowed_tools("browser", registry)}
        for tool_id in ("browser.navigate", "browser.click", "browser.type", "browser.close"):
            self.assertIn(tool_id, ids, f"agent browser sans {tool_id}")

    def test_shared_manager_is_core_browser(self):
        from jarvis.browser_manager import get_manager
        self.assertIs(get_manager(), self.core.browser,
                      "le manager partagé doit être l'instance de JarvisCore")

    def test_readonly_mode_keeps_navigation(self):
        from jarvis.security_analysis import READ_ONLY_ALLOWED_TOOLS
        for tool_id in ("browser.navigate", "browser.wait", "browser.back",
                        "browser.scroll", "browser.pause", "browser.close"):
            self.assertIn(tool_id, READ_ONLY_ALLOWED_TOOLS,
                          f"{tool_id} filtré en mode lecture seule (montre-moi la page)")
        self.assertNotIn("browser.click", READ_ONLY_ALLOWED_TOOLS)
        self.assertNotIn("browser.type", READ_ONLY_ALLOWED_TOOLS)


class TestRunnerChain(BrowserChainCase):
    def _fake_manager(self):
        class FakeManager:
            available = lambda self: True
            start = lambda self: None

            def action(self, op, args, timeout=45):
                self.calls.append((op, args))
                if op == "navigate":
                    return {"ok": True, "url": args.get("url"), "title": "Brainrot Fortnite"}
                return {"ok": True, "target": "ok"}
        fake = FakeManager()
        fake.calls = []
        return fake

    def test_navigate_routes_to_shared_manager(self):
        fake = self._fake_manager()
        with patch("jarvis.browser_manager.get_manager", return_value=fake):
            result = self.core.runner.run(
                "browser.navigate", {"url": "https://brainrot-fortnite.com"},
                agent="jarvis", task_id="t1", conversation_id="c1", confirmed=True)
        self.assertTrue(result.ok, result.output)
        self.assertEqual(fake.calls, [("navigate", {"url": "https://brainrot-fortnite.com"})])
        self.assertIn("brainrot-fortnite.com", result.output)

    def test_click_needs_selector(self):
        fake = self._fake_manager()
        with patch("jarvis.browser_manager.get_manager", return_value=fake):
            result = self.core.runner.run(
                "browser.click", {"selector": "Voir les prix"},
                agent="jarvis", task_id="t1", conversation_id="c1", confirmed=True)
        self.assertTrue(result.ok, result.output)
        self.assertEqual(fake.calls[0][0], "click")

    def test_playwright_missing_is_honest(self):
        with patch("jarvis.browser_manager.get_manager") as gm:
            mgr = gm.return_value
            mgr.available.return_value = False
            result = self.core.runner.run(
                "browser.navigate", {"url": "https://brainrot-fortnite.com"},
                agent="jarvis", task_id="t1", conversation_id="c1", confirmed=True)
        self.assertFalse(result.ok)
        self.assertIn("Playwright", result.output)

    def test_readonly_policy_allows_navigate_blocks_click(self):
        fake = self._fake_manager()
        policy = {"intent": "read_only", "read_only": True, "write_allowed": False}
        with patch("jarvis.browser_manager.get_manager", return_value=fake):
            nav = self.core.runner.run(
                "browser.navigate", {"url": "https://brainrot-fortnite.com"},
                agent="jarvis", task_id="t1", conversation_id="c1", confirmed=True,
                execution_policy=policy)
            click = self.core.runner.run(
                "browser.click", {"selector": "Voir les prix"},
                agent="jarvis", task_id="t1", conversation_id="c1", confirmed=True,
                execution_policy=policy)
        self.assertTrue(nav.ok, nav.output)
        self.assertFalse(click.ok)
        self.assertIn("WRITE_DENIED_READ_ONLY", click.output)


class TestIntentRouter(BrowserChainCase):
    def test_open_url_keeps_tools(self):
        for text in (
            "Ouvre https://brainrot-fortnite.com dans le navigateur et montre-moi la page. Ne fais aucune autre action.",
            "Ouvre brainrot-fortnite.com dans le navigateur.",
            "Affiche la page brainrot-fortnite.com dans le navigateur intégré.",
        ):
            with self.subTest(text=text[:50]):
                resolved = predict_intent(text)
                self.assertTrue(resolved.tools_allowed,
                                f"{text!r} → {resolved.intent} ({resolved.trigger_source})")

    def test_quoted_browser_request_stays_model_only(self):
        resolved = predict_intent("« ouvre le navigateur et cherche le prix du gaz » : réponds simplement.")
        self.assertFalse(resolved.tools_allowed, resolved.trigger_source)


if __name__ == "__main__":
    unittest.main()