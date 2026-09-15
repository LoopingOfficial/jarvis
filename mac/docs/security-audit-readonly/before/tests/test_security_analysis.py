"""Régressions du routage analyse sécurité -> lecture seule."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis.core import JarvisCore
from jarvis.intents import detect_read_only_intent
from jarvis.llm.base import LLMResponse
from jarvis.tools.base import ToolResult


REQUEST = "analyse marketplace.php et dit moi si tu trouve des failles de sécurité. Ne modifie rien !"


class SecurityAnalysisCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.core = JarvisCore(db_path=Path(self.tmp.name) / "security.db")
        self.core.connectors.create({
            "type": "ssh", "id": "ssh", "name": "SSH", "enabled": True,
            "permissions": ["read", "write", "execute"],
            "config": {"host": "example.test", "username": "deploy",
                       "working_directory": "/home/brainrotfortnite/public_html"},
        })

    def tearDown(self):
        self.core.db.close()
        self.tmp.cleanup()

    def test_deterministic_policy(self):
        policy = detect_read_only_intent(REQUEST)
        self.assertEqual(policy.intent, "security_analysis")
        self.assertTrue(policy.read_only)
        self.assertFalse(policy.write_allowed)
        self.assertEqual(policy.target, "marketplace.php")

    def test_runner_denies_write_even_when_requested_by_model(self):
        self.core.active_task_context.update({"read_only": True, "write_allowed": False})
        with patch("jarvis.tools.remote_tools._ssh_write_file") as handler:
            result = self.core.runner.run(
                "ssh.write_file", {"connector_id": "ssh", "path": "x", "content": "bad"})
        self.assertFalse(result.ok)
        self.assertEqual(result.output, "WRITE_DENIED_READ_ONLY")
        handler.assert_not_called()

    def test_security_request_reads_workspace_and_never_edits(self):
        content = "<?php\n" + ("$query = $_GET['q'];\n" * 3200)
        runner_calls = []

        def fake_run(tool, args, **kwargs):
            runner_calls.append((tool, args, kwargs))
            self.assertEqual(tool, "ssh.read_file")
            self.assertEqual(args["path"], "/home/brainrotfortnite/public_html/marketplace.php")
            self.assertFalse(kwargs["execution_policy"]["write_allowed"])
            return ToolResult(True, content, data={"content": content,
                                                     "path": args["path"], "source": "ssh",
                                                     "connector": "ssh"})

        def fake_chat(messages, **kwargs):
            if kwargs.get("tools") == []:
                return LLMResponse(text='{"findings": [{"severity": "high", "category": "sql_injection", "line": 2, "evidence": "$_GET", "risk": "injection", "recommendation": "parametrize"}]}')
            return LLMResponse(text="rapport")

        with patch.object(self.core.runner, "run", side_effect=fake_run) as runner, \
             patch.object(self.core.llm, "chat", side_effect=fake_chat):
            result = self.core.orchestrator.handle(REQUEST, conversation_id="security-test")

        self.assertTrue(result["ok"])
        self.assertEqual([call[0][0] for call in runner.call_args_list],
                         ["ssh.read_file", "ssh.read_file"])
        self.assertNotIn("WRITE_DENIED_READ_ONLY", result["response"])
        self.assertIn("vulnérabilité", result["response"])
        self.assertTrue(result["analysis"]["read_only"])
        self.assertEqual(result["analysis"]["source_hash_before"], result["analysis"]["source_hash_after"])
        self.assertGreater(result["analysis"]["chunks"], 1)


if __name__ == "__main__":
    unittest.main()
