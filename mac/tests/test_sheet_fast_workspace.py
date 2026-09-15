"""Phase 7 — le Workspace s'ouvre dès que les données déterministes sont prêtes.

Tout ce que l'interface affiche (tableaux, KPI, previews, preuves, statistiques)
vient de l'analyse déterministe, disponible en quelques secondes. La synthèse du
modèle, elle, prend 20 à 30 s. Attendre la seconde pour publier la première
faisait patienter devant un écran vide.

Ce test vérifie que le payload anticipé est complet SANS narration, que la
synthèse arrive ensuite, et qu'aucun prompt interne ne fuit vers l'interface.
"""
from __future__ import annotations

import unittest

from jarvis.analysis_workspace import build_payload
from jarvis.orchestrator import Orchestrator
from jarvis.sheet_semantics import analyze_workbook
from jarvis.speech_sanitizer import PUBLIC_ACTIVITY_LABELS, is_internal_prompt


def workbook():
    return {
        "ok": True, "sheet_count": 1,
        "sheets": [{
            "name": "Ventes", "rows": 4, "cols": 3,
            "cells": [
                ["Produit", "Prix", "Quantité"],
                ["Alpha", "10", "3"],
                ["Beta", "20", "5"],
                ["Gamma", "30", "2"],
            ],
            "merges": [],
        }],
    }


class EarlyPayloadTests(unittest.TestCase):
    def setUp(self):
        self.analysis = analyze_workbook(workbook())
        self.early = build_payload(
            request="Analyse ce fichier",
            source={"kind": "google_sheet", "label": "Ventes", "url": "https://x"},
            analysis=self.analysis, narrative="", grounding={}, duration_ms=2900)

    def test_early_payload_carries_the_real_data(self):
        """Les données affichables sont présentes avant toute synthèse."""
        self.assertTrue(self.early["tables"], "aucun tableau")
        self.assertTrue(self.early["evidence"], "aucune preuve")
        self.assertTrue(self.early["metrics"], "aucun KPI")
        self.assertTrue(self.early["sections"])
        self.assertEqual(self.early["source"]["sheet_count"], 1)

    def test_early_payload_has_no_narrative(self):
        self.assertEqual(self.early["summary"]["narrative"], "")

    def test_headline_is_deterministic_not_generative(self):
        """Le titre se calcule, il ne s'invente pas : il est déjà juste."""
        self.assertIn("onglets", self.early["summary"]["headline"])
        self.assertIn("tableaux", self.early["summary"]["headline"])

    def test_narrative_can_be_filled_in_afterwards(self):
        final = build_payload(
            request="Analyse ce fichier",
            source={"kind": "google_sheet", "label": "Ventes", "url": "https://x"},
            analysis=self.analysis, narrative="Trois produits, 60 € au total.",
            grounding={"ok": True}, duration_ms=24000)
        # Les données ne changent pas entre les deux versions : seule la
        # narration s'ajoute. Sinon l'interface « sauterait » à l'arrivée.
        self.assertEqual(final["tables"], self.early["tables"])
        self.assertEqual(final["metrics"], self.early["metrics"])
        self.assertEqual(final["evidence"], self.early["evidence"])
        self.assertTrue(final["summary"]["narrative"])


class StageContractTests(unittest.TestCase):
    """L'étape d'ouverture doit précéder l'étape modèle, et rester publique."""

    def test_workspace_ready_comes_before_the_model(self):
        stages = Orchestrator.SHEET_STAGES
        self.assertIn("sheet_workspace_ready", stages)
        self.assertLess(stages["sheet_workspace_ready"][1], stages["sheet_llm"][1],
                        "le Workspace doit s'ouvrir AVANT l'appel au modèle")

    def test_every_stage_label_is_public(self):
        """Aucun libellé d'étape ne doit être un fragment de prompt interne."""
        for stage, (label, _step) in Orchestrator.SHEET_STAGES.items():
            self.assertIn(label, PUBLIC_ACTIVITY_LABELS, f"libellé non public : {stage}")
            self.assertFalse(is_internal_prompt(label))

    def test_steps_are_ordered_and_within_total(self):
        steps = [s for _, s in Orchestrator.SHEET_STAGES.values()]
        self.assertEqual(steps, sorted(steps), "étapes non ordonnées")

    def test_grounding_and_repair_stages_are_preserved(self):
        """Phase 7 ne doit rien retirer de la chaîne de vérification."""
        for stage in ("sheet_grounding", "sheet_repair", "sheet_workspace", "sheet_done"):
            self.assertIn(stage, Orchestrator.SHEET_STAGES)


class NoPromptLeakTests(unittest.TestCase):
    def test_early_payload_contains_no_internal_prompt(self):
        analysis = analyze_workbook(workbook())
        payload = build_payload(
            request="Analyse ce fichier",
            source={"kind": "google_sheet", "label": "Ventes", "url": "https://x"},
            analysis=analysis, narrative="", grounding={})
        blob = repr(payload)
        for marker in ("CONTENU STRUCTUR", "ANALYSE_DETERMINISTE", "SOURCE_POLICY",
                       "DEMANDE DE L'UTILISATEUR", "PERIMETRE_OBLIGATOIRE"):
            self.assertNotIn(marker, blob, f"fuite de prompt interne : {marker}")


if __name__ == "__main__":
    unittest.main()
