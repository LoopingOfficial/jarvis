"""Correctif runtime MODEL_ONLY — build JARVIS_MODEL_ONLY_RUNTIME_FIX_V1.

Cible : le crash « [Errno 22] Invalid argument » sur l'envoi du benchmark en
mode MODEL_ONLY. Objectifs :
  * un benchmark (court, long, UTF-8, code, JSON, complet) passe par la boucle
    modèle seul : tools=[], tools_used=[], AUCUN audit déclenché ;
  * un vrai audit (« analyse réellement marketplace.php sans le modifier »)
    garde le chemin OUTIL (tools_allowed=True), jamais model_only ;
  * toute exception runtime LLM devient un MODEL_RUNTIME_ERROR clair + request_id
    en logs, jamais un errno brut côté utilisateur ;
  * le résultat expose un request_id traçable.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis.core import JarvisCore
from jarvis.llm.base import LLMResponse
from jarvis.message_context import predict_intent

MODEL_ONLY_HEADER = "MODE: MODEL_ONLY_BENCHMARK\nN'utilise aucun outil.\n"

BENCHMARK_COMPLET = (
    MODEL_ONLY_HEADER + "\n"
    "Tu passes un benchmark complet de capacités.\n"
    "Réponds à toutes les questions dans un seul message et indique "
    "CONFIDENCE: 0-100.\n"
    "\n"
    "=== 1. CONNAISSANCES (UTF-8 : é è à ç û « » → –) ===\n"
    "1A. Explique la différence entre RAM, VRAM, SSD et cache CPU.\n"
    "1B. Pourquoi HTTPS ne garantit-il pas qu'un site est honnête ?\n"
    "\n"
    "=== 2. RAISONNEMENT ===\n"
    "2A. Une machine produit 175 unités toutes les 90 secondes : combien en "
    "15 minutes ? Montre brièvement les calculs.\n"
    "2B. A→B, B→C, A→E, E→D : si B tombe, existe-t-il un chemin de A vers D ?\n"
    "\n"
    "=== 3. CODE ===\n"
    "3A. Trouve le bug :\n"
    "def find_user(users, user_id):\n"
    "    for user in users:\n"
    "        if user[\"id\"] = user_id:\n"
    "            return user\n"
    "3B. Pourquoi ce PHP est-il dangereux ?\n"
    "<?php\n"
    "$id = $_GET['id'];\n"
    "$r = $pdo->query(\"SELECT * FROM users WHERE id = $id\");\n"
    "?>\n"
    "Donne une correction PDO avec requête préparée.\n"
    "\n"
    "=== 4. PROTOCOLES ===\n"
    "4A. Qu'est-ce qu'un reverse proxy ?\n"
    "4B. Différence entre authentification, autorisation, chiffrement et hachage.\n"
)


class BaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.core = JarvisCore(db_path=Path(self._tmp.name) / "model_only.db")

    def tearDown(self) -> None:
        try:
            self.core.db.close()
        except Exception:
            pass
        try:
            self._tmp.cleanup()
        except Exception:
            pass

    def _run(self, text: str, answer: str = "Réponse du modèle (benchmark)."):
        calls = []

        def fake_chat(messages, **kwargs):
            calls.append(kwargs)
            return LLMResponse(text=answer, model="jarvis-astra")

        with patch.object(self.core.llm, "chat", side_effect=fake_chat) as ch, \
                patch.object(self.core.llm, "resolve", return_value=(None, "")):
            result = self.core.orchestrator.handle(text, conversation_id="c1")
        return result, calls


class TestModelOnlyCompleted(BaseCase):
    """T1-T6 : chaque variante passe par la boucle modèle seul."""

    CASES = [
        ("T1_court", MODEL_ONLY_HEADER + "Explique la différence entre RAM et VRAM."),
        ("T2_long", MODEL_ONLY_HEADER +
         "Un prompt volontairement long, sur plusieurs lignes, sans aucune "
         "instruction d'exécution. Décris soigneusement pourquoi un benchmark "
         "de capacités ne doit jamais toucher au système : c'est une évaluation "
         "du modèle, pas une commande. Donne ensuite une courte synthèse."),
        ("T3_utf8", MODEL_ONLY_HEADER +
         "UTF-8 partout : é è ê à â ç û ü î ô « » – — … Réponds avec précision."),
        ("T4_code", MODEL_ONLY_HEADER +
         "Analyse ce code et explique les failles.\n"
         "```php\n"
         "$id = $_GET['id'];\n"
         "$result = $pdo->query(\"SELECT * FROM users WHERE id = $id\");\n"
         "```\n"
         "Donne la correction minimale."),
        ("T5_json", MODEL_ONLY_HEADER +
         "Voici un exemple de payload : {\"tool\": \"web.search\", "
         "\"query\": \"failles XSS\"}. Explique-le sans l'exécuter."),
        ("T6_benchmark_complet", BENCHMARK_COMPLET),
    ]

    def test_model_only_variants(self):
        for label, text in self.CASES:
            with self.subTest(label=label):
                result, calls = self._run(text)
                self.assertTrue(result["ok"], f"{label}: {result}")
                self.assertEqual(result.get("tools_used"), [], label)
                self.assertEqual(result["response"], "Réponse du modèle (benchmark).", label)
                self.assertTrue(calls, label)
                self.assertEqual(calls[-1].get("tools"), [], label)

    def test_request_id_present(self):
        result, _ = self._run(MODEL_ONLY_HEADER + "Qu'est-ce qu'un reverse proxy ?")
        rid = result.get("request_id", "")
        self.assertTrue(rid.startswith("req_") and len(rid) == 16, rid)


class TestNoAuditOnBenchmark(BaseCase):
    """T7 + non-régression A : un benchmark (même rempli de mots 'audit' /
    'marketplace.php' / 'SSH' / 'read-only') ne déclenche JAMAIS d'audit."""

    BENCH_WITH_AUDIT_WORDS = (
        MODEL_ONLY_HEADER
        + "Question : pourquoi un audit de security marketplace.php doit être "
          "read-only ? Faut-il utiliser SSH pour l'analyser ? Réponds, n'agis pas."
    )
    NR_A = MODEL_ONLY_HEADER + \
        "Pourquoi analyser marketplace.php sans le modifier doit être read-only ?"

    def test_benchmark_with_audit_words_is_model_only(self):
        for label, text in [
            ("T7_audit_words", self.BENCH_WITH_AUDIT_WORDS),
            ("NR_A_readonly_question", self.NR_A),
        ]:
            with self.subTest(label=label):
                resolved = predict_intent(text)
                self.assertFalse(resolved.tools_allowed, f"{label} → {resolved.intent}")
                self.assertEqual(resolved.intent, "model_only", label)
                self.assertNotEqual(resolved.intent, "security_audit_readonly", label)
                result, calls = self._run(text)
                self.assertTrue(result["ok"], label)
                self.assertEqual(result.get("tools_used"), [], label)
                self.assertEqual(calls[-1].get("tools"), [], label)


class TestRealAuditStillWorks(BaseCase):
    """T8 + non-régression B : une vraie analyse/lecture reste un chemin OUTIL."""

    REAL_AUDIT = "Analyse réellement marketplace.php sans le modifier"

    def test_real_audit_keeps_tool_path(self):
        calls = []

        def fake_chat(messages, **kwargs):
            calls.append(kwargs)
            return LLMResponse(text="Analyse en lecture seule terminée.", model="jarvis-astra")

        resolved = predict_intent(self.REAL_AUDIT)
        self.assertTrue(resolved.tools_allowed,
                        f"audit réel → {resolved.intent} ({resolved.trigger_source})")
        self.assertNotEqual(resolved.intent, "model_only")
        self.assertTrue(resolved.target, "marketplace.php doit être détecté comme cible")

        with patch.object(self.core.llm, "chat", side_effect=fake_chat):
            result = self.core.orchestrator.handle(self.REAL_AUDIT, conversation_id="c1")
        self.assertTrue(result["ok"])
        self.assertEqual(result.get("response"), "Analyse en lecture seule terminée.")
        # Chemin OUTIL : le LLM a reçu la liste réelle des outils, pas tools=[].
        self.assertTrue(calls)
        tools_passed = calls[-1].get("tools")
        self.assertNotEqual(tools_passed, [])


class TestRuntimeErrorHandling(BaseCase):
    """Un crash LLM doit produire un MODEL_RUNTIME_ERROR clair + request_id,
    jamais un « [Errno 22] Invalid argument » brut côté utilisateur."""

    def test_oserror_22_becomes_runtime_error(self):
        def boom(messages, **kwargs):
            raise OSError(22, "Invalid argument")

        with patch.object(self.core.llm, "chat", side_effect=boom), \
                patch.object(self.core.llm, "resolve", return_value=(None, "")):
            result = self.core.orchestrator.handle(
                MODEL_ONLY_HEADER + "Explique la RAM.", conversation_id="c1")

        self.assertFalse(result["ok"])
        self.assertIn("MODEL_RUNTIME_ERROR", result["response"])
        self.assertNotIn("[Errno 22]", result["response"])
        self.assertNotIn("Invalid argument", result["response"])
        self.assertTrue(str(result["request_id"]).startswith("req_"))
        # Le détail technique reste accessible (logs / champ error), pas masqué.
        self.assertIn("OSError", str(result.get("error")))

    def test_normal_llm_error_still_friendly(self):
        with patch.object(self.core.llm, "chat",
                          return_value=LLMResponse(error="timed out")), \
                patch.object(self.core.llm, "resolve", return_value=(None, "")):
            result = self.core.orchestrator.handle(
                MODEL_ONLY_HEADER + "Explique la VRAM.", conversation_id="c1")
        self.assertFalse(result["ok"])
        self.assertIn("Je ne peux pas raisonner", result["response"])
        self.assertEqual(result.get("error"), "timed out")


if __name__ == "__main__":
    unittest.main()