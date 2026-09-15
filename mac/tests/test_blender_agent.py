"""Routage de l'agent spécialiste Blender `jarvis-blender`.

Couvre les exigences d'intégration de l'agent 3D :
- une demande normale reste sur le modèle principal (jamais de 3D) ;
- une demande Blender / avatar est routée vers le spécialiste ;
- l'avatar passe par la VISION (le spécialiste n'invente pas l'image) ;
- le filtrage d'outils n'expose que les outils 3D (jamais SSH/agenda/mail) ;
- pas de bascule silencieuse vers le modèle général quand le spécialiste manque ;
- pas de fabrication d'humanoïde en primitives.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis.agents import AGENTS, blender_tools_for
from jarvis.core import JarvisCore
from jarvis.intents import detect_3d_intent, detect_avatar_update_intent
from jarvis.llm.manager import LLMManager
from jarvis.tools import registry


class _StubConnectors:
    def by_type(self, *a, **k):
        return []

    def raw(self, *a, **k):
        return None


class _StubEvents:
    def emit(self, *a, **k):
        pass

    def feed(self, *a, **k):
        pass


class _StubSettings:
    def get(self, *a, **k):
        return ""


class TestIntentRouting(unittest.TestCase):
    """Une demande 3D/avatar est détectée ; une demande normale ne l'est pas."""

    def test_normal_requests_stay_on_main_model(self):
        for text in ("quelle heure est-il", "ouvre Edge", "lis index.php",
                     "quel temps fait-il", "raconte une blague"):
            self.assertIsNone(detect_3d_intent(text), f"devrait rester principal: {text}")

    def test_blender_requests_detected(self):
        cases = {
            "crée un personnage 3D": "blender.create_model",
            "crée un modèle 3D": "blender.create_model",
            "rig ce personnage": "blender.rig",
            "crée une animation de marche": "blender.animate",
            "exporte en GLB": "blender.export",
            "inspecte ce modèle": "blender.inspect",
        }
        for text, action in cases.items():
            intent = detect_3d_intent(text)
            self.assertIsNotNone(intent, text)
            self.assertEqual(intent["action"], action, text)

    def test_avatar_requests_detected(self):
        for text in ("ajoute des cheveux à ton avatar",
                     "améliore ton visage",
                     "inspecte ton avatar Blender"):
            intent = detect_3d_intent(text)
            self.assertIsNotNone(intent, text)
            self.assertEqual(intent["action"], "avatar.craft", text)

    def test_avatar_update_from_reference_detected(self):
        intent = detect_3d_intent("modifie ton avatar selon cette image")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["action"], "avatar.update_from_reference")
        self.assertIsNotNone(detect_avatar_update_intent("adapte ton visage à cette photo"))


class TestBlenderAgentSpec(unittest.TestCase):
    def test_blender_agent_registered_with_dedicated_role(self):
        spec = AGENTS["blender"]
        self.assertEqual(spec.model_role, "blender")
        # Le spécialiste ne parle jamais directement à l'utilisateur.
        self.assertFalse(spec.speaks_to_user)

    def test_system_prompt_forbids_primitive_humanoid(self):
        prompt = AGENTS["blender"].system_prompt.lower()
        self.assertIn("primitive", prompt)
        self.assertIn("inspecte avant de modifier", prompt)
        self.assertIn("n'invente jamais", prompt)

    def test_per_action_tool_subset(self):
        self.assertEqual(blender_tools_for("blender.rig"),
                         ("blender.inspect", "blender.rig"))
        self.assertIn("avatar.update_from_reference",
                      blender_tools_for("avatar.update_from_reference"))
        self.assertEqual(blender_tools_for("action.inconnue"), ())


class TestBlenderToolFiltering(unittest.TestCase):
    """Le spécialiste ne reçoit QUE les outils 3D, jamais SSH/agenda/mail."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.core = JarvisCore(db_path=Path(self._tmp.name) / "test.db")

    def tearDown(self):
        try:
            self.core.db.close()
        except Exception:
            pass
        try:
            self._tmp.cleanup()
        except Exception:
            pass

    def test_allowed_tools_are_3d_only(self):
        tools = self.core.agents.allowed_tools("blender", registry)
        ids = {t.id for t in tools}
        # Aucun outil hors atelier 3D.
        for banned_prefix in ("ssh.", "calendar.", "email.", "weather.", "memory.",
                              "n8n.", "web.", "connector.", "task.", "fs."):
            self.assertFalse(any(i.startswith(banned_prefix) for i in ids),
                             f"{banned_prefix} ne doit pas être exposé")
        # Les outils 3D et avatar attendus sont bien présents.
        self.assertTrue(any(i == "blender.inspect" for i in ids))
        self.assertTrue(any(i == "blender.export" for i in ids))

    def test_orchestrator_filters_per_action(self):
        tools = self.core.agents.allowed_tools("blender", registry)
        subset = self.core.orchestrator._blender_tools(tools, "blender.rig")
        ids = {t.id for t in subset}
        self.assertEqual(ids, {"blender.inspect", "blender.rig"})

    def test_presence_tools_not_exposed_to_specialist(self):
        tools = self.core.agents.allowed_tools("blender", registry)
        subset = self.core.orchestrator._blender_tools(tools, "")
        ids = {t.id for t in subset}
        self.assertNotIn("avatar.move", ids)
        self.assertNotIn("avatar.gesture", ids)
        self.assertNotIn("avatar.look", ids)


class TestBlenderModelResolution(unittest.TestCase):
    """Le rôle `blender` résout le spécialiste, sans cascade vers le général."""

    def _manager(self):
        return LLMManager(connectors=_StubConnectors(), vault=None,
                          settings=_StubSettings(), events=_StubEvents())

    def test_specialist_detects_jarvis_blender(self):
        manager = self._manager()
        sentinel = object()
        with patch.object(manager, "status", return_value=[
                {"connected": True, "id": "oll", "type": "ollama",
                 "models": ["llama3.1", "jarvis-blender"], "default_model": "llama3.1"}]):
            with patch.object(manager, "provider_by_id", return_value=sentinel):
                provider, model = manager.blender_specialist()
        self.assertEqual(model, "jarvis-blender")
        self.assertIs(provider, sentinel)

    def test_resolve_blender_does_not_fall_back_to_general(self):
        manager = self._manager()
        with patch.object(manager, "status", return_value=[
                {"connected": True, "id": "oll", "type": "ollama",
                 "models": ["llama3.1"], "default_model": "llama3.1"}]):
            provider, model = manager.resolve("blender")
        self.assertIsNone(provider)
        self.assertEqual(model, "")

    def test_blender_status_reports_missing(self):
        manager = self._manager()
        with patch.object(manager, "status", return_value=[]):
            status = manager.blender_status()
        self.assertFalse(status["available"])
        self.assertIn("jarvis-blender", status["reason"])


class TestBlenderDelegationFallback(unittest.TestCase):
    """Pas de génération 3D avec le modèle général quand le spécialiste manque."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.core = JarvisCore(db_path=Path(self._tmp.name) / "test.db")

    def tearDown(self):
        try:
            self.core.db.close()
        except Exception:
            pass
        try:
            self._tmp.cleanup()
        except Exception:
            pass

    def test_missing_specialist_is_explicit(self):
        with patch.object(self.core.llm, "blender_status",
                          return_value={"available": False, "reason": "aucun modèle"}), \
             patch.object(self.core.orchestrator, "run_agent") as run_agent:
            result = self.core.orchestrator.delegate_to_blender(
                "crée un personnage", "blender.create_model",
                task_id="t", conversation_id="c")
        self.assertFalse(result["ok"])
        self.assertIn("spécialiste Blender", result["response"])
        run_agent.assert_not_called()

    def test_available_specialist_delegates_with_agent(self):
        with patch.object(self.core.llm, "blender_status",
                          return_value={"available": True, "model": "jarvis-blender",
                                        "provider": "ollama", "name": "Ollama"}):
            with patch.object(self.core.orchestrator, "run_agent",
                              return_value={"ok": True, "output": "Terminé.",
                                            "tools_used": ["blender.inspect"]}):
                result = self.core.orchestrator.delegate_to_blender(
                    "rig ce personnage", "blender.rig",
                    task_id="t", conversation_id="c")
        self.assertTrue(result["ok"])
        self.assertEqual(result["model"], "jarvis-blender")


if __name__ == "__main__":
    unittest.main(verbosity=2)
