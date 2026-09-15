"""BUILD JARVIS_GOOGLE_SHEETS_SEMANTIC_ANALYSIS_V6 : régions, stats légitimes, synthèse."""
import unittest

from jarvis.orchestrator import _sheet_answer_policy
from jarvis.sheet_semantics import analyze_workbook, analysis_brief, detect_regions, structure_sheet
from jarvis.source_grounding import (deterministic_workbook_report, validate_source_grounding,
                                     workbook_summary)

# ALL BRAINROTS : tableau principal, ligne vide, puis encart INCOME CALCULATION.
ALL_BRAINROTS = [
    ["ALL BRAINROTS", "", "", ""],
    ["Name", "Rarity", "Income", "Cost"],
    ["Tralalero", "Secret", 5000, 67],
    ["Bombardiro", "Epic", 250, 14],
    ["Lirili", "Rare", 120, 30],
    ["Banana", "Rare", 90, 22],
    ["", "", "", ""],
    ["INCOME CALCULATION", "", "", ""],
    ["Step", "Multiplier", "", ""],
    ["Base", 1, "", ""],
    ["Rebirth", 2, "", ""],
    ["Golden", 4, "", ""],
]

# Deux tableaux côte à côte, séparés par une colonne entièrement vide.
SIDE_BY_SIDE = [
    ["Fruit", "Price", "", "Pet", "Level"],
    ["Apple", 3, "", "Cat", 10],
    ["Pear", 5, "", "Dog", 12],
    ["Plum", 7, "", "Owl", 14],
]

NOTES = [
    ["Documentation du classeur"],
    ["Ce fichier est mis à jour chaque semaine par la communauté."],
    ["Merci de ne rien modifier sans prévenir."],
]


def sheet(name, cells):
    return {"name": name, "cells": cells, "useful_rows": len(cells), "useful_columns": 4,
            "non_empty_cells": sum(1 for r in cells for v in r if str(v).strip()),
            "formula_cells": [], "data": [], "headers": [], "header_row": 1}


class TableRegionTests(unittest.TestCase):
    def test_main_table_separated_from_income_calculation(self):
        regions = detect_regions(ALL_BRAINROTS, "ALL BRAINROTS")
        tables = [r for r in regions if r["kind"] == "table"]
        self.assertEqual(len(tables), 2)
        self.assertEqual(tables[0]["title"], "ALL BRAINROTS")
        self.assertEqual(tables[0]["row_count"], 4)
        self.assertEqual(tables[1]["title"], "INCOME CALCULATION")
        self.assertEqual(tables[1]["row_count"], 3)

    def test_side_by_side_blocks_are_two_regions(self):
        tables = [r for r in detect_regions(SIDE_BY_SIDE, "Side") if r["kind"] == "table"]
        self.assertEqual(len(tables), 2)
        self.assertEqual(tables[0]["headers"] if "headers" in tables[0] else tables[0]["raw_headers"],
                         ["Fruit", "Price"])
        self.assertEqual(tables[1]["raw_headers"], ["Pet", "Level"])
        self.assertEqual((tables[1]["first_column"], tables[1]["last_column"]), (4, 5))

    def test_notes_are_not_data_rows(self):
        structured = structure_sheet(sheet("Notes", NOTES))
        self.assertEqual(structured["table_count"], 0)
        self.assertTrue(structured["note_regions"])


class StatisticsTests(unittest.TestCase):
    def profiles(self, name="ALL BRAINROTS"):
        structured = structure_sheet(sheet(name, ALL_BRAINROTS))
        return {c["name"]: c for r in structured["regions"] if r["kind"] == "table"
                for c in r["column_profiles"]}

    def test_label_columns_never_get_numeric_stats(self):
        profiles = self.profiles()
        for label in ("Name", "Rarity", "Step"):
            self.assertNotIn("stats", profiles[label], label)

    def test_real_numeric_columns_keep_stats_per_region(self):
        profiles = self.profiles()
        self.assertEqual(profiles["Income"]["stats"]["min"], 90)
        self.assertEqual(profiles["Income"]["stats"]["max"], 5000)
        self.assertEqual(profiles["Multiplier"]["stats"]["max"], 4)
        # Aucune stat ne mélange le tableau principal et l'encart de calcul.
        self.assertEqual(profiles["Multiplier"]["stats"]["count"], 3)

    def test_report_never_mixes_two_regions(self):
        report = deterministic_workbook_report(
            workbook_summary({"sheets": [sheet("ALL BRAINROTS", ALL_BRAINROTS)]}))
        self.assertIn("INCOME CALCULATION", report)
        self.assertNotIn("Stat Name", report)
        self.assertIn("Stat Income", report)


class AnalysisTests(unittest.TestCase):
    def test_analysis_is_facts_not_a_dump(self):
        analysis = analyze_workbook({"sheets": [sheet("ALL BRAINROTS", ALL_BRAINROTS),
                                                sheet("Notes", NOTES)]})
        brief = analysis_brief(analysis)
        self.assertIn("2 tableaux distincts", brief)
        self.assertIn("min=90", brief)
        self.assertNotIn("Tralalero", brief)

    def test_user_analysis_request_gets_synthesis_plan(self):
        plan = _sheet_answer_policy("analyse ce fichier et dis-moi ce que tu en penses")
        self.assertIn("FORMAT_REPONSE_ANALYSE", plan)
        self.assertIn("Recommandations", plan)
        self.assertNotIn("FORMAT_REPONSE_ANALYSE", _sheet_answer_policy("liste toutes les lignes"))


class GroundingTests(unittest.TestCase):
    def test_grounding_passes_on_computed_and_real_values(self):
        workbook = {"sheets": [sheet("ALL BRAINROTS", ALL_BRAINROTS)]}
        answer = "Onglet ALL BRAINROTS : 4 lignes, Income va de 90 à 5000."
        self.assertTrue(validate_source_grounding(answer, workbook).ok)

    def test_grounding_rejects_invented_number(self):
        workbook = {"sheets": [sheet("ALL BRAINROTS", ALL_BRAINROTS)]}
        result = validate_source_grounding("Le revenu moyen est de 98765.", workbook)
        self.assertFalse(result.ok)

    def test_deterministic_report_always_passes_grounding(self):
        workbook = {"sheets": [sheet("ALL BRAINROTS", ALL_BRAINROTS),
                               sheet("Side", SIDE_BY_SIDE)]}
        report = deterministic_workbook_report(workbook_summary(workbook))
        self.assertTrue(validate_source_grounding(report, workbook).ok)


if __name__ == "__main__":
    unittest.main()
