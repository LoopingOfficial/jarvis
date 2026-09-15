"""Acceptance regressions: real ToolRunner, mocked transport/LLM, no network."""
import base64
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from jarvis.core import JarvisCore
from jarvis.execution_policy import policy_scope
from jarvis.goals import has_write_intent
from jarvis.intents import detect_read_only_intent
from jarvis.llm.base import LLMResponse, ToolCall
from jarvis.security_analysis import split_into_chunks
from jarvis.tools.base import registry, ToolResult

REQUEST = "analyse marketplace.php et dit moi si tu trouve des failles de sécurité. Ne modifie rien !"
ROOT = "/home/brainrotfortnite/public_html"
PATH = ROOT + "/marketplace.php"
POLICY = {"intent": "security_audit_readonly", "read_only": True, "write_allowed": False}
FINDING = {"severity": "critical", "category": "SQL injection", "line": 2,
           "evidence": "$_GET", "risk": "Entrée non filtrée", "recommendation": "Requête paramétrée"}
VALID = json.dumps({"findings": [FINDING], "summary": "Entrée non filtrée"})


class SecurityAnalysisCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.core = JarvisCore(db_path=Path(self.tmp.name) / "security.db")
        self.core.connectors.create({
            "type": "ssh", "id": "ssh", "name": "SSH", "enabled": True,
            "permissions": ["read", "write", "execute"],
            "config": {"host": "example.test", "username": "deploy", "working_directory": ROOT}})
        self.core.db.execute("UPDATE connectors SET status='connected' WHERE id='ssh'")
        self.content = "<?php\n$x = $_GET['q'];\n" + "// unchanged\n" * 30
        self.calls = []
        self.events = []
        self.core.events.on("*", lambda event: self.events.append(event))

    def tearDown(self):
        self.core.db.close()
        self.tmp.cleanup()

    def open_doc(self, content=None):
        doc = self.core.documents.add_from_tool(source="ssh", connector_id="ssh", path=PATH,
            content=self.content if content is None else content, language="php")
        self.core.documents.open(doc.id)
        return doc

    def transport(self, cfg, secrets, command, **kwargs):
        self.calls.append(command)
        if command.startswith("printf 'JARVIS_FILE_B64:'"):
            self.assertIn(PATH, command)
            return True, "JARVIS_FILE_B64:" + base64.b64encode(self.content.encode()).decode()
        raise AssertionError("Unexpected transport mutation: " + command)

    def audit(self, request=REQUEST, llm=None):
        with patch("jarvis.tools.remote_tools.ssh_exec", side_effect=self.transport), \
             patch.object(self.core.llm, "chat", side_effect=llm or (lambda *a, **kw: LLMResponse(text=VALID))):
            return self.core.orchestrator.handle(request, conversation_id="security-test")

    def test_intents_and_negation(self):
        for text in (REQUEST, "analyse marketplace.php et dis-moi si tu trouves des failles",
                     "audit sécurité de blog.php", "cherche les vulnérabilités dans index.php, ne modifie rien",
                     "analyse ce fichier en lecture seule", "readonly", "ne change rien", "inspecte index.php"):
            with self.subTest(text=text):
                intent = detect_read_only_intent(text)
                self.assertEqual(intent.intent, "security_audit_readonly")
                self.assertFalse(intent.write_allowed)
                self.assertFalse(has_write_intent(text))
        for text in ("corrige maintenant les failles critiques que tu as trouvées",
                     "corrige seulement la faille SQL injection", "ouvre index.php et modifie le titre"):
            self.assertEqual(detect_read_only_intent(text).intent, "file_edit")
        self.assertEqual(detect_read_only_intent("affiche moi marketplace.php").intent, "read_only")
        self.assertEqual(detect_read_only_intent("affiche security.php").intent, "read_only")
        for text in ("explique comment modifier index.php", "propose les correctifs",
                     "analyse index.php et explique comment corriger la faille"):
            self.assertTrue(detect_read_only_intent(text).read_only)
            self.assertFalse(has_write_intent(text))

    def test_open_is_only_open(self):
        with patch("jarvis.tools.remote_tools.ssh_exec", side_effect=self.transport), \
             patch.object(self.core.documents, "opened_response", return_value="Ouvert"), \
             patch.object(self.core.llm, "chat") as llm:
            result = self.core.orchestrator.handle("affiche moi marketplace.php", conversation_id="security-test")
        self.assertTrue(result["ok"])
        self.assertEqual(self.core.orchestrator.code_edit_router.active_document().content, self.content)
        llm.assert_not_called()

    def test_open_document_regression_and_report_in_conversation(self):
        doc = self.open_doc()
        before = (doc.content, doc.original_content)
        with patch.object(self.core.orchestrator.code_edit_router, "execute") as editor, \
             patch.object(self.core.orchestrator.file_router, "execute") as opener:
            result = self.audit()
        editor.assert_not_called()
        opener.assert_not_called()
        self.assertEqual(self.calls, [])  # Source is the already open Coding buffer.
        self.assertEqual((doc.content, doc.original_content), before)
        self.assertTrue(doc.read_only)
        self.assertEqual(doc.audit_jobs, 0)
        self.assertTrue(result["ok"])
        for phrase in ("marketplace.php", "Risque global : critical", "Résumé exécutif", "SQL injection",
                       "Extrait", "Explication", "Recommandation", "Aucune modification n’a été appliquée"):
            self.assertIn(phrase, result["response"])
        self.assertNotIn("anormalement court", result["response"])
        row = self.core.db.one("SELECT content FROM messages WHERE role='assistant' ORDER BY created_at DESC LIMIT 1")
        self.assertEqual(row["content"], result["response"])

    def test_direct_editor_refuses_before_llm(self):
        self.open_doc()
        router = self.core.orchestrator.code_edit_router
        self.assertFalse(router.should_handle(REQUEST))
        with patch.object(self.core.llm, "chat") as llm:
            result = router.execute(REQUEST, "security-test")
        self.assertEqual(result["response"], "WRITE_DENIED_READ_ONLY")
        llm.assert_not_called()

    def test_large_closed_file_chunks_exact_hash_and_no_writes(self):
        self.content = ("<?php\n" + "echo 'test';\n" * 13000)[:153522]
        self.assertEqual(len(self.content), 153522)
        lengths = []
        def model(messages, **kw):
            self.assertEqual(kw["tools"], [])
            self.assertIn('"intent":"security_audit_readonly"', messages[0].content)
            lengths.append(len(messages[1].content))
            return LLMResponse(text='{"findings":[]}')
        result = self.audit(llm=model)
        self.assertTrue(result["ok"], result["response"])
        self.assertGreater(result["analysis"]["chunks"], 1)
        self.assertEqual(len(lengths), result["analysis"]["chunks"])
        self.assertLess(max(lengths), 8500)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(result["tools_used"], ["ssh.read_file", "ssh.read_file"])
        expected = sha256(self.content.encode()).hexdigest()
        self.assertEqual(result["analysis"]["source_hash_before"], expected)
        self.assertEqual(result["analysis"]["source_hash_after"], expected)

    def test_overlap_covers_every_character(self):
        source = "x" * 153522
        chunks = split_into_chunks(source, size=6000, overlap=600)
        end = 0
        for offset, chunk in chunks:
            self.assertLessEqual(offset - 1, end)
            end = offset - 1 + len(chunk)
        self.assertEqual(end, len(source))

    def test_model_write_is_denied_and_retry_yields_report(self):
        self.open_doc()
        answers = [LLMResponse(tool_calls=[ToolCall("bad", "ssh.write_file",
                      {"connector_id": "ssh", "path": PATH, "content": "bad",
                       "read_only": False, "write_allowed": True})]), LLMResponse(text=VALID)]
        with patch.object(registry.get("ssh.write_file"), "handler") as handler:
            result = self.audit(llm=lambda *a, **kw: answers.pop(0))
        handler.assert_not_called()
        self.assertTrue(result["ok"], result["response"])
        denied = self.core.db.one("SELECT detail FROM audit_log WHERE status='denied' ORDER BY ts DESC LIMIT 1")
        self.assertEqual(denied["detail"], "WRITE_DENIED_READ_ONLY")

    def test_runner_fail_closed_even_with_confirmed_or_inconsistent_flags(self):
        task = self.core.tasks.create(name="audit", meta={"execution_policy": POLICY})
        for name in ("ssh.write_file", "fs.write", "filesystem.write", "apply_patch", "replace_file",
                     "deploy", "ssh.run", "terminal.run", "database.write", "agent.delegate", "memory.save",
                     "ssh.create_file", "ssh.delete_file", "ssh.move", "ssh.rename", "service.restart"):
            with self.subTest(tool=name):
                result = self.core.runner.run(name, {"command": "ls; touch bad"}, task_id=task["id"],
                    confirmed=True, execution_policy={"read_only": False, "write_allowed": True})
                self.assertEqual(result.output, "WRITE_DENIED_READ_ONLY")
        result = self.core.runner.run("fs.write", {}, execution_policy={"intent": "security_audit_readonly",
                                                                       "write_allowed": True})
        self.assertEqual(result.output, "WRITE_DENIED_READ_ONLY")

    def test_request_scopes_are_isolated(self):
        def worker(readonly):
            try:
                with policy_scope(POLICY if readonly else {"intent": "file_edit"}):
                    return self.core.runner.run("unknown.tool").output
            finally:
                self.core.db.close()
        with ThreadPoolExecutor(2) as pool:
            values = list(pool.map(worker, (True, False)))
        self.assertEqual(values, ["WRITE_DENIED_READ_ONLY", "Outil inconnu: unknown.tool"])

    def test_invalid_or_failed_model_never_reports_clean(self):
        self.open_doc()
        for response in (LLMResponse(error="offline"), LLMResponse(text="rapport malformé"),
                         LLMResponse(text='{}'), LLMResponse(tool_calls=[ToolCall("x", "fs.write", {})])):
            result = self.audit(llm=lambda *a, **kw: response)
            self.assertFalse(result["ok"])
            self.assertEqual(result["analysis"]["overall_risk"], "unknown")
            self.assertIn("Audit incomplet", result["response"])

    def test_failed_recheck_does_not_fake_hash(self):
        count = 0
        def transport(*a, **kw):
            nonlocal count
            count += 1
            return self.transport(*a, **kw) if count == 1 else (False, "network lost")
        with patch("jarvis.tools.remote_tools.ssh_exec", side_effect=transport), \
             patch.object(self.core.llm, "chat", return_value=LLMResponse(text=VALID)):
            result = self.core.orchestrator.handle(REQUEST, conversation_id="security-test")
        self.assertFalse(result["ok"])
        self.assertEqual(result["analysis"]["source_hash_after"], "")

    def test_changed_buffer_is_detected(self):
        doc = self.open_doc()
        def model(*a, **kw):
            doc.content += "// another actor\n"
            return LLMResponse(text=VALID)
        result = self.audit(llm=model)
        self.assertFalse(result["ok"])
        self.assertNotEqual(result["analysis"]["source_hash_before"], result["analysis"]["source_hash_after"])

    def test_missing_workspace_never_falls_back_local(self):
        self.core.connectors.update("ssh", {"config": {"working_directory": ""}})
        result = self.audit()
        self.assertFalse(result["ok"])
        self.assertEqual(self.calls, [])
        self.assertEqual(result["tools_used"], [])

    def test_followup_new_edit_job_confirmation_then_write_once(self):
        doc = self.open_doc()
        audit = self.audit()
        updated = self.content.replace("$_GET['q']", "'fixed'")
        def write(ctx):
            self.content = ctx.arguments["content"]
            return ToolResult(True, "Écriture effectuée")
        with patch("jarvis.tools.remote_tools.ssh_exec", side_effect=self.transport), \
             patch.object(self.core.llm, "chat", return_value=LLMResponse(text=updated)) as llm, \
             patch.object(registry.get("ssh.write_file"), "handler", side_effect=write) as handler:
            result = self.core.orchestrator.handle(
                "corrige maintenant les failles critiques que tu as trouvées", conversation_id="security-test")
            self.assertIn("needs_confirmation", result)
            self.assertNotEqual(result["task_id"], audit["task_id"])
            handler.assert_not_called()
            self.assertFalse(doc.read_only)
            self.assertIn("SQL injection", llm.call_args[0][0][1].content)
            confirmation = result["needs_confirmation"]["id"]
            final = self.core.orchestrator.resume_confirmation(confirmation, True)
            self.assertTrue(final["ok"], final)
            self.assertEqual(handler.call_count, 1)
            self.assertFalse(self.core.orchestrator.resume_confirmation(confirmation, True)["ok"])

    def test_edit_guard_still_rejects_truncated_replacement(self):
        self.open_doc("a" * 153522)
        with patch.object(self.core.llm, "chat", return_value=LLMResponse(text="tiny")), \
             patch.object(registry.get("ssh.write_file"), "handler") as handler:
            result = self.core.orchestrator.handle("corrige marketplace.php", conversation_id="security-test")
        self.assertIn("anormalement court", result["response"])
        handler.assert_not_called()

    def test_editor_save_blocked_while_document_locked(self):
        doc = self.open_doc()
        self.audit()
        result = self.core.runner.run("ssh.write_file",
            {"path": PATH, "connector_id": "ssh", "content": "bad"}, confirmed=True)
        self.assertEqual(result.output, "WRITE_DENIED_READ_ONLY")
        self.assertEqual(doc.content, self.content)


if __name__ == "__main__":
    unittest.main()
