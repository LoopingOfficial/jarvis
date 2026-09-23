"""n8n : détection langage naturel, recherche, classification d'échec, états santé."""
import time
import unittest
from types import SimpleNamespace

from jarvis.connector_health import state_for
from jarvis.intents import TYPE_PATTERNS
from jarvis.n8n_connector import N8nConnector, classify_http_failure
from jarvis.n8n_service import N8nService
from jarvis.recovery import VelkoRecoveryManager, classify_failure

WORKFLOWS = [
    {"workflow_id": "1", "name": "VELKO_10_Email", "active": False, "tags": [], "nodes": ["gmail", "if"],
     "description": "Agent d EXECUTION email.", "triggers": []},
    {"workflow_id": "2", "name": "VELKO_11_Calendar", "active": False, "tags": [], "nodes": ["googleCalendar"],
     "description": "Agent calendrier.", "triggers": []},
    {"workflow_id": "3", "name": "Brainrot Import", "active": True, "tags": ["brainrot"], "nodes": ["mySql"],
     "description": "", "triggers": [{"kind": "webhook", "path": "import", "method": "POST"}]},
]


def service():
    svc = N8nService.__new__(N8nService)
    svc._core, svc._context, svc._pending = None, {}, {}
    return svc


class DetectTests(unittest.TestCase):
    def test_plural_workflows_matches_connector_pattern(self):
        self.assertTrue(TYPE_PATTERNS["n8n"].search("Liste moi les workflows que je possède"))

    def test_list_requests(self):
        svc = service()
        for text in ("Liste moi les workflows que je possède", "Liste mes workflows.",
                     "Quels workflows n8n j'ai ?"):
            self.assertEqual(svc.detect(text)["action"], "list", text)

    def test_follow_up_filter_uses_context(self):
        svc = service()
        self.assertIsNone(svc.detect("Montre seulement ceux qui sont actifs.", "c1"))
        svc._context["c1"] = {"at": time.time()}
        self.assertEqual(svc.detect("Montre seulement ceux qui sont actifs.", "c1"),
                         {"action": "list", "filter": "active"})

    def test_actions(self):
        svc = service()
        self.assertEqual(svc.detect("Lance le workflow Brainrot Import"),
                         {"action": "run", "workflow": "Brainrot Import"})
        self.assertEqual(svc.detect("Désactive le workflow VELKO_10_Email")["action"], "deactivate")
        self.assertEqual(svc.detect("Quel workflow gère les emails ?")["action"], "search")
        self.assertEqual(svc.detect("Quand Brainrot Import a-t-il tourné pour la dernière fois ? workflow")
                         ["action"], "last_run")

    def test_unrelated_text_ignored(self):
        self.assertIsNone(service().detect("Crée une image de chat"))


class SearchTests(unittest.TestCase):
    def test_semantic_email_search(self):
        conn = N8nConnector.__new__(N8nConnector)
        hits = conn.search("J'ai un workflow qui gère les emails ?", WORKFLOWS)
        self.assertEqual(hits[0]["name"], "VELKO_10_Email")


class RecoveryTests(unittest.TestCase):
    def test_classification(self):
        self.assertEqual(classify_failure("HTTP 401: unauthorized"), "AUTH_REQUIRED")
        self.assertEqual(classify_failure("<urlopen error timed out>"), "NETWORK_ERROR")
        self.assertEqual(classify_failure("HTTP 502: bad gateway"), "EXTERNAL_SERVICE_ERROR")
        self.assertEqual(classify_http_failure("HTTP 403: nope"), ("AUTH_REQUIRED", 403))

    def test_missing_connector_payload(self):
        core = SimpleNamespace(vault=SimpleNamespace(scrub=lambda t: t))
        r = VelkoRecoveryManager(core).build("CONNECTOR_MISSING", connector="n8n", task_id="t1")
        self.assertIn("URL n8n + clé API", r["message"])
        self.assertEqual(r["actions"][0]["id"], "configure")
        self.assertTrue(r["resumable"])
        self.assertIn("cancel", [a["id"] for a in r["actions"]])


class HealthStateTests(unittest.TestCase):
    def test_states_never_fake_connected(self):
        self.assertEqual(state_for(True, "", 80), "CONNECTED")
        self.assertEqual(state_for(True, "", 5000), "DEGRADED")
        self.assertEqual(state_for(False, "HTTP 401: unauthorized", None), "WAITING_USER")
        self.assertEqual(state_for(False, "Clé API n8n manquante.", None), "WAITING_USER")
        self.assertEqual(state_for(False, "timed out", None), "DISCONNECTED")


if __name__ == "__main__":
    unittest.main()
