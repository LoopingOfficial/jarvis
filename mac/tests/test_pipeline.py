"""Pipeline connecteurs : intention déterministe, outil réel, anti-hallucination.

Couvre les exigences :
- « utilise la connexion ssh enregistré et exécute uptime » déclenche un vrai outil.
- Le modèle ne doit JAMAIS raconter une action sans qu'un outil n'ait tourné.
- Une réponse d'action sans outil n'est JAMAIS apprise (anti-pollution).
- Les tools SSH (fichiers inclus) sont bien exposés au format Ollama.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis.core import JarvisCore
from jarvis.intents import resolve_connector_intent
from jarvis.llm.base import LLMResponse
from jarvis.tools.base import ToolResult, registry

SSH_TEXT = "utilise la connexion ssh enregistré et exécute uptime"
HALLUCINATED = (
    "Le serveur utilise la connexion ssh enregistrée. Uptime : 1:23 PM, 1 utilisateur, "
    "charge moyenne 0.00, 0.00, 0.00. Tout va bien."
)
REAL_OUTPUT = "[SSH] 21:00:00 up 100 days,  1 user,  load average: 0.11, 0.22, 0.33"


class BaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.core = JarvisCore(db_path=Path(self._tmp.name) / "test.db")

    def tearDown(self) -> None:
        try:
            self.core.db.close()
        except Exception:
            pass
        try:
            self._tmp.cleanup()
        except Exception:
            pass  # handle SQLite encore verrouillé (Windows)

    def _add_ssh(self, cid: str = "ssh", host: str = "cpanel.example.fr") -> None:
        self.core.connectors.create({
            "type": "ssh", "id": cid, "name": "SSH", "enabled": True,
            "permissions": ["read", "write", "execute"],
            "config": {"host": host, "username": "deploy",
                       "working_directory": "/home/deploy"},
        })


class TestIntentDetection(BaseCase):
    def setUp(self) -> None:
        super().setUp()
        self._add_ssh()

    def test_execute_uptime(self):
        intent = resolve_connector_intent(self.core, SSH_TEXT)
        self.assertIsNotNone(intent)
        self.assertTrue(intent.executable)
        self.assertEqual(intent.tool_id, "ssh.run")
        self.assertEqual(intent.arguments["command"], "uptime")
        self.assertEqual(intent.connector_id, "ssh")

    def test_execute_uptime_and_whoami(self):
        intent = resolve_connector_intent(
            self.core, "utilise la connexion ssh enregistrée et exécute whoami puis uptime")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.tool_id, "ssh.run")
        self.assertEqual(intent.arguments["command"], "uptime; whoami")

    def test_status(self):
        intent = resolve_connector_intent(self.core, "regarde l'état de mon serveur")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.tool_id, "ssh.status")

    def test_logs_with_service(self):
        intent = resolve_connector_intent(self.core, "regarde les logs nginx")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.tool_id, "ssh.logs")
        self.assertEqual(intent.arguments.get("service"), "nginx")

    def test_logs_generic(self):
        intent = resolve_connector_intent(self.core, "regarde les logs du serveur")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.tool_id, "ssh.logs")
        self.assertNotIn("service", intent.arguments)  # « serveur » n'est pas un service

    def test_service_restart(self):
        intent = resolve_connector_intent(self.core, "redémarre le service nginx")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.tool_id, "ssh.service")
        self.assertEqual(intent.arguments, {"service": "nginx", "action": "restart"})

    def test_run_dedupe(self):
        intent = resolve_connector_intent(self.core, "exécute df -h sur le serveur")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.arguments["command"], "df -h")  # pas de doublon

    def test_read_file_path_preserved(self):
        intent = resolve_connector_intent(
            self.core, "lis le fichier /home/deploy/public_html/index.php")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.tool_id, "ssh.read_file")
        self.assertEqual(intent.arguments["path"], "/home/deploy/public_html/index.php")

    def test_list_files(self):
        intent = resolve_connector_intent(
            self.core, "listes les fichiers dans /home/deploy/public_html")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.tool_id, "ssh.list")
        self.assertEqual(intent.arguments["path"], "/home/deploy/public_html")

    def test_implicit_ssh_without_keyword(self):
        intent = resolve_connector_intent(self.core, "exécute uptime")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.tool_id, "ssh.run")

    def test_no_false_positive_conversational(self):
        self.assertIsNone(resolve_connector_intent(self.core, "bonjour, ça va ?"))
        self.assertIsNone(resolve_connector_intent(
            self.core, "quels serveurs ssh ai-je configurés ?"))
        self.assertIsNone(resolve_connector_intent(self.core, "analyse les fichiers de mon site"))

    def test_no_intent_without_connector(self):
        core = JarvisCore(db_path=Path(self._tmp.name) / "empty.db")
        try:
            self.assertIsNone(resolve_connector_intent(core, SSH_TEXT))
        finally:
            core.db.close()

    def test_not_executable_with_many_connectors(self):
        core = JarvisCore(db_path=Path(self._tmp.name) / "ambig.db")
        try:
            for cid, host in (("prod", "a.example.fr"), ("staging", "b.example.fr")):
                core.connectors.create({
                    "type": "ssh", "id": cid, "name": cid, "enabled": True,
                    "permissions": ["read", "write", "execute"],
                    "config": {"host": host, "username": "deploy"},
                })
            intent = resolve_connector_intent(core, "exécute uptime sur mes serveurs")
            self.assertIsNotNone(intent)
            self.assertFalse(intent.executable)  # ambigu : 2 cibles possibles
            self.assertEqual(intent.connector_id, "")
        finally:
            core.db.close()


class TestOtherConnectorIntents(BaseCase):
    """Fast-path déterministe par type de connecteur (connecteur unique)."""

    def _add(self, ctype: str, cid: str, config: dict) -> None:
        self.core.connectors.create({
            "type": ctype, "id": cid, "name": cid, "enabled": True,
            "permissions": ["read", "write", "execute"], "config": config,
        })

    def test_ftp_list(self):
        self._add("ftp", "ftp", {"host": "ftp.example.fr", "username": "u"})
        intent = resolve_connector_intent(
            self.core, "liste les fichiers dans /public via ftp")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.tool_id, "ftp.op")
        self.assertEqual(intent.arguments["action"], "list")
        self.assertEqual(intent.arguments["path"], "/public")
        self.assertTrue(intent.executable)

    def test_ftp_not_executable_when_ambiguous(self):
        for cid in ("a", "b"):
            self._add("ftp", cid, {"host": "ftp.example.fr", "username": "u"})
        intent = resolve_connector_intent(self.core, "liste les fichiers via ftp")
        self.assertIsNotNone(intent)
        self.assertFalse(intent.executable)
        self.assertEqual(intent.connector_id, "")

    def test_docker_ps_local_without_connector(self):
        intent = resolve_connector_intent(self.core, "montre les conteneurs docker")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.tool_id, "docker.control")
        self.assertEqual(intent.arguments["action"], "ps")
        self.assertTrue(intent.executable)
        self.assertEqual(intent.connector_id, "")

    def test_docker_restart_named_container(self):
        intent = resolve_connector_intent(self.core, "redémarre le conteneur mysql")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.tool_id, "docker.control")
        self.assertEqual(intent.arguments, {"action": "restart", "container": "mysql"})
        self.assertTrue(intent.executable)

    def test_docker_mention_not_an_action(self):
        self.assertIsNone(resolve_connector_intent(
            self.core, "explique-moi comment fonctionne docker"))

    def test_docker_needs_target_not_executable(self):
        intent = resolve_connector_intent(self.core, "redémarre docker")
        self.assertIsNotNone(intent)
        self.assertFalse(intent.executable)

    def test_n8n_list(self):
        self._add("n8n", "n8n", {"url": "https://n8n.example.fr", "api_key": "x"})
        intent = resolve_connector_intent(self.core, "liste les workflows n8n")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.tool_id, "n8n.workflow")
        self.assertEqual(intent.arguments["action"], "list")
        self.assertTrue(intent.executable)

    def test_github_repos_with_connector(self):
        self._add("github", "github", {"token": "x"})
        intent = resolve_connector_intent(self.core, "liste mes dépôts github")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.tool_id, "github.query")
        self.assertEqual(intent.arguments["action"], "repos")
        self.assertTrue(intent.executable)

    def test_github_without_connector_is_none(self):
        self.assertIsNone(resolve_connector_intent(self.core, "liste mes dépôts github"))

    def test_github_mention_not_an_action(self):
        self._add("github", "github", {"token": "x"})
        self.assertIsNone(resolve_connector_intent(
            self.core, "explique ce qu'est github"))

    def test_cpanel_summary(self):
        self._add("cpanel", "cpanel", {"host": "https://cpanel.example.fr", "username": "r"})
        intent = resolve_connector_intent(self.core, "regarde l'état de mon cpanel")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.tool_id, "cpanel.query")
        self.assertEqual(intent.arguments["action"], "summary")
        self.assertTrue(intent.executable)

    def test_whm_loadavg(self):
        self._add("whm", "whm", {"host": "https://whm.example.fr", "username": "r"})
        intent = resolve_connector_intent(self.core, "donne-moi le loadavg whm")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.tool_id, "whm.query")
        self.assertEqual(intent.arguments["function"], "loadavg")
        self.assertTrue(intent.executable)


class TestToolExposure(BaseCase):
    def test_ssh_tools_registered(self):
        ids = [t.id for t in registry.for_agent("jarvis") if t.id.startswith("ssh.")]
        expected = {"ssh.run", "ssh.status", "ssh.logs", "ssh.service",
                    "ssh.list", "ssh.read_file", "ssh.write_file",
                    "ssh.upload", "ssh.download"}
        self.assertTrue(expected.issubset(set(ids)))

    def test_schemas_are_valid_functions(self):
        for name in ("ssh.run", "ssh.read_file", "ssh.write_file", "ssh.list"):
            tool = registry.get(name)
            schema = tool.llm_schema()
            self.assertEqual(schema["name"], name)
            self.assertIsInstance(schema["input_schema"], dict)
            self.assertEqual(schema["input_schema"].get("type"), "object")


class TestAutoLearningGate(BaseCase):
    def test_connector_action_without_tool_is_never_learned(self):
        self._add_ssh()
        learned = self.core.auto_learning.is_learnable(
            request=SSH_TEXT, response=HALLUCINATED, tools_used=[])
        self.assertFalse(learned)

    def test_connector_action_with_tool_can_be_learned(self):
        self._add_ssh()
        learned = self.core.auto_learning.is_learnable(
            request=SSH_TEXT, response="Le serveur répond : " + REAL_OUTPUT,
            tools_used=["ssh.run"])
        self.assertTrue(learned)


class TestOrchestratorAntiHallucination(BaseCase):
    def test_real_tool_executed_when_model_hallucinates(self):
        self._add_ssh()
        calls = []

        def fake_llm(messages, **kwargs):
            step = len(calls)
            calls.append(True)
            if step < 2:  # le modèle « invente » sans appeler d'outil
                return LLMResponse(text=HALLUCINATED, tool_calls=[])
            return LLMResponse(text="Le serveur est actif : " + REAL_OUTPUT, tool_calls=[])

        with patch.object(self.core.llm, "chat", side_effect=fake_llm) as llm, \
             patch.object(self.core.runner, "run", wraps=self._fake_run) as runner:
            result = self.core.orchestrator.handle(SSH_TEXT, conversation_id="test-conv")

        self.assertTrue(result["ok"])
        # Le résultat RÉEL de l'outil est présent dans la réponse finale.
        self.assertIn("up 100 days", result["response"])
        # La réponse hallucinée ne doit pas transparaître.
        self.assertNotIn("1:23 PM", result["response"])
        # L'outil a bien été exécuté une seule fois avec les bons arguments.
        self.assertEqual(runner.call_count, 1)
        call = runner.call_args
        self.assertEqual(call[0][0], "ssh.run")
        self.assertEqual(call[0][1], {"command": "uptime"})
        self.assertIn("ssh.run", result["tools_used"])
        # Deux refus du modèle + une passe de résumé final = 3 appels LLM.
        self.assertEqual(llm.call_count, 3)

    @staticmethod
    def _fake_run(name, arguments, **kwargs):
        if name == "ssh.run":
            return ToolResult(ok=True, output=REAL_OUTPUT)
        raise AssertionError(f"outil inattendu dans le test: {name} {arguments}")

    def test_empty_arguments_json_stays_object(self):
        # Régression : le format Ollama natif conserve un objet, pas une chaîne.
        from jarvis.llm.base import ChatMessage, ToolCall, messages_to_ollama
        m = ChatMessage(role="assistant", tool_calls=[ToolCall("c1", "ssh.status", {})])
        wire = json.loads(json.dumps(messages_to_ollama([m])))
        self.assertEqual(wire[0]["tool_calls"][0]["function"]["arguments"], {})


if __name__ == "__main__":
    unittest.main()