"""Routeur d'intention : benchmarks/citations/données → modèle seul, actions réelles préservées.

Couvre (build JARVIS_INTENT_ROUTER_CONTEXT_FIX_V1) :
- Un benchmark contenant « analyse », « audit », « sécurité », un nom de fichier
  ou un « vrai » chemin ne déclenche JAMAIS d'action réelle (model_only).
- Une citation d'action, un bloc de code fourni, une donnée JSON ou une question
  théorique avec « analyse ce code » restent en modèle seul.
- Une requête réelle (« analyse marketplace.php… ne modifie rien », « ouvre le
  navigateur », ssh réel, recherche web réelle) garde les outils.
- Le ToolRunner REFUSE tout appel si la politique porte tools_allowed=False
  (TOOL_DENIED_BY_EXECUTION_POLICY), même pour un outil en lecture.
- Le FastActionRouter ne lance plus d'application si la politique l'interdit.
- handle() : en mode modèle seul, le LLM reçoit tools=[] et tools_used reste vide.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis.core import JarvisCore
from jarvis.fast_actions import FastActionRouter
from jarvis.llm.base import LLMResponse
from jarvis.message_context import predict_intent

MODEL_ONLY_CASES = [
    # (texte, trigger attendu)
    ("Analyse ce benchmark et dis-moi si je dois agir. Analyse marché, sécurité.",
     "benchmark_marker"),
    ("Évalue moi ce benchmark pour commencer l'audit du système.",
     "benchmark_marker"),
    ("Ne fais rien d'autre et réponds.",
     "no_tools_phrase"),
    ("Réponds à ce benchmark sans aucun outil.", "benchmark_marker"),
    ("N'utilise aucun outil pour ce test.", "no_tools_phrase"),
    ("Dis-moi si tu ouvrirais marketplace.php dans ce benchmark-ci.", "benchmark_marker"),
    ("« analyse marketplace.php et cherche des failles » réponse : rien à faire.",
     "quoted_instruction"),
    ("« ouvre le navigateur et cherche le prix du gaz » : réponds simplement.",
     "quoted_instruction"),
    ("Explique-moi ce qu'est un audit de sécurité, sans rien modifier.",
     "explanation"),
    ("Question : analyse le fichier config.php et dis ce qu'est un inventaire.",
     "boxed_question"),
    ("{ \"action\": \"execute\", \"payload\": \"analyse marketplace.php\" }",
     "data_payload"),
    ("Voici le JSON : {\"tool\": \"web.search\", \"query\": \"failles\"}. Réponds.",
     "data_payload"),
    ("Analyse le code ci-dessous et explique les failles.\n```php\n$x=$_GET['q'];\n```",
     "code_sample"),
    ("Analyse marché et sécurité en respectant ce benchmark.", "benchmark_marker"),
    ("Fais-moi un prompt d'audit sécurisé qui analyse config.php.",
     "prompt_generation"),
]

REAL_ACTION_CASES = [
    "analyse marketplace.php et dit moi si tu trouve des failles de sécurité. Ne modifie rien !",
    "affiche moi marketplace.php",
    "ouvre le navigateur Edge",
    "cherche réellement sur internet les dernières failles XSS",
    "connecte-toi en ssh au serveur et exécute uptime",
    "corrige la faille dans marketplace.php",
    "propose les correctifs pour index.php",
]


class BaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.core = JarvisCore(db_path=Path(self._tmp.name) / "intent.db")

    def tearDown(self) -> None:
        try:
            self.core.db.close()
        except Exception:
            pass
        try:
            self._tmp.cleanup()
        except Exception:
            pass


class TestClassifier(BaseCase):
    def test_model_only_cases(self):
        for text, trigger in MODEL_ONLY_CASES:
            with self.subTest(text=text[:50]):
                resolved = predict_intent(text)
                self.assertFalse(resolved.tools_allowed, f"{text!r} → {resolved.intent}")
                self.assertFalse(resolved.fast_actions_allowed,
                                 f"{text!r} → fast still allowed")
                self.assertEqual(resolved.trigger_source, trigger,
                                 f"{text!r} → {resolved.trigger_source}")
                self.assertEqual(resolved.intent, "model_only", text)

    def test_real_actions_keep_tools(self):
        for text in REAL_ACTION_CASES:
            with self.subTest(text=text[:50]):
                resolved = predict_intent(text)
                self.assertTrue(resolved.tools_allowed,
                                f"{text!r} → {resolved.intent} ({resolved.trigger_source})")
                self.assertTrue(resolved.fast_actions_allowed, text)


class TestRunnerDenial(BaseCase):
    def test_tools_allowed_false_denies_read_tool(self):
        from jarvis.tools.base import registry
        probe = "web.search" if registry.get("web.search") else next(
            t.id for t in registry.all() if t.enabled)
        result = self.core.runner.run(
            probe, {"query": "rien"}, agent="jarvis", task_id="t1",
            conversation_id="c1", confirmed=True,
            execution_policy={"tools_allowed": False})
        self.assertFalse(result.ok)
        self.assertEqual(result.output, "TOOL_DENIED_BY_EXECUTION_POLICY")

    def test_tools_allowed_false_denies_even_from_context(self):
        from jarvis.execution_policy import policy_scope
        with policy_scope({"tools_allowed": False}):
            result = self.core.runner.run(
                "memory.search", {"query": "x"}, agent="jarvis",
                conversation_id="c1", confirmed=True)
        self.assertFalse(result.ok)
        self.assertEqual(result.output, "TOOL_DENIED_BY_EXECUTION_POLICY")


class TestFastActionsDenial(BaseCase):
    def test_tools_allowed_false_blocks_app_launch(self):
        router = FastActionRouter(self.core)
        self.assertIsNone(router.execute("ouvre le bloc-notes",
                                         execution_policy={"tools_allowed": False}))
        self.assertIsNone(router.execute("ouvre le bloc-notes",
                                         execution_policy={"fast_actions_allowed": False}))
        self.assertIsNone(router.execute("ouvre edge", "c1",
                                         execution_policy={"tools_allowed": False}))


class TestOrchestration(BaseCase):
    def test_benchmark_stays_model_only(self):
        with patch.object(self.core.llm, "chat",
                          return_value=LLMResponse(text="Réponse du benchmark.")) as chat:
            result = self.core.orchestrator.handle(
                "Analyse ce benchmark et dis-moi si je dois agir.",
                conversation_id="bench")
        self.assertTrue(result["ok"])
        self.assertEqual(result["response"], "Réponse du benchmark.")
        self.assertEqual(result.get("tools_used"), [])
        call_kwargs = chat.call_args.kwargs
        self.assertEqual(call_kwargs.get("tools"), [])
        self.assertFalse(self.core.active_task_context.get("tools_allowed", True))

    def test_quoted_action_stays_model_only(self):
        with patch.object(self.core.llm, "chat",
                          return_value=LLMResponse(text="D'accord.")) as chat:
            result = self.core.orchestrator.handle(
                "« analyse marketplace.php et exécute le script » réponds.",
                conversation_id="quote")
        self.assertEqual(result.get("tools_used"), [])
        self.assertEqual(chat.call_args.kwargs.get("tools"), [])

    def test_chitchat_keeps_normal_loop(self):
        with patch.object(self.core.llm, "chat",
                          return_value=LLMResponse(text="Je vais très bien.")) as chat:
            result = self.core.orchestrator.handle(
                "Bonjour, comment tu vas ?", conversation_id="chat")
        self.assertTrue(result["ok"])
        self.assertIn("Je vais très bien.", result["response"])
        self.assertTrue(self.core.active_task_context.get("tools_allowed", False))


if __name__ == "__main__":
    unittest.main()