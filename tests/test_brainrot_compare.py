"""BUILD JARVIS_BRAINROT_COMPARE_V1 : matching, diff, statuts, lecture seule."""
import unittest

from jarvis.analysis_workspace import attach_comparison, build_payload, comparison_digest
from jarvis.brainrot_compare import (CONFLICT, CREATE, FIELD_MAPPING, INVALID, NO_CHANGE,
                                     SERVER_ONLY, UPDATE, compare, match_records,
                                     normalize_number, normalize_text, parse_site_php,
                                     read_sheet_records, slugify)
from jarvis.sheet_semantics import analyze_workbook

SITE_PHP = """<?php
return [
    ['name'=>'Fishini Bossini','rarity'=>'Common','income'=>1,'cost'=>50],
    ['name'=>'Tim Cheese','rarity'=>'Common','income'=>5,'cost'=>250],
    ['name'=>'14','rarity'=>'Secret','income'=>1400000,'cost'=>1400000000],
    ['name'=>'Vieux Modele','rarity'=>'Epic','income'=>10,'cost'=>100],
];
"""


def workbook(rows, merges=None, numbers=None):
    """Onglet ALL BRAINROTS minimal : bandeau, en-tête, puis les lignes données."""
    cells = [["HOME", "", "", ""], ["", "Rarity", "Name", ""],
             ["", "Rarity", "Name", "Base Income ($/s)"]]
    # En-têtes réels sur la ligne 3 (index 2) : Rarity | Name | Base Income | Price.
    cells = [["HOME", "", "", ""],
             ["Rarity", "Name", "Base Income ($/s)", "Price ($)"]] + rows
    sheet = {"name": "ALL BRAINROTS", "cells": cells,
             "row_numbers": numbers or list(range(1, len(cells) + 1)),
             "merges": merges or [], "useful_rows": len(cells), "useful_columns": 4,
             "non_empty_cells": sum(1 for r in cells for v in r if str(v).strip()),
             "formula_cells": [], "data": [], "headers": [], "header_row": 2}
    return {"workbook": "wb", "sheets": [sheet]}


SHEET_ROWS = [
    ["Common", "Fishini Bossini", 1, 50],
    ["", "Tim Cheese", 5, 250],
    ["Secret", 14.0, "1.4M", "1.4B"],
    ["", "Nouveau Brainrot", 999, 9990],
]
# « Common » couvre les lignes 3-4 : c'est ainsi que le vrai Sheet écrit une
# rareté de section, et c'est la fusion qui le dit — jamais une recopie devinée.
SHEET_MERGES = [{"first_row": 3, "last_row": 4, "first_col": 1, "last_col": 1}]


class NormalisationTests(unittest.TestCase):
    def test_case_and_spacing_never_create_a_false_difference(self):
        self.assertEqual(normalize_text("  Tim   Cheese "), normalize_text("tim cheese"))
        self.assertEqual(slugify(" Tim  Cheese "), "tim-cheese")

    def test_integral_float_name_matches_its_string_form(self):
        # Le XLSX rend « 14 » en 14.0 : sans cette règle, faux CREATE + faux SERVER_ONLY.
        self.assertEqual(normalize_text(14.0), normalize_text("14"))

    def test_suffixed_numbers_are_real_numbers(self):
        self.assertEqual(normalize_number("1.4M"), 1_400_000)
        self.assertEqual(normalize_number("1.4B"), 1_400_000_000)
        self.assertIsNone(normalize_number("bientôt"))

    def test_empty_and_null_stay_distinct_from_zero(self):
        self.assertIsNone(normalize_number(""))
        self.assertEqual(normalize_number(0), 0)


class SiteSchemaTests(unittest.TestCase):
    def test_php_records_are_parsed_without_executing_code(self):
        records = parse_site_php(SITE_PHP)
        self.assertEqual(len(records), 4)
        self.assertEqual(records[0]["raw"], {"name": "Fishini Bossini", "rarity": "Common",
                                             "income": 1.0, "cost": 50.0})
        self.assertEqual(records[0]["line"], 3)

    def test_mapping_targets_the_real_site_keys(self):
        self.assertEqual(FIELD_MAPPING["Base Income ($/s)"], "income")
        self.assertEqual(FIELD_MAPPING["Price ($)"], "cost")
        for key in FIELD_MAPPING.values():
            self.assertIn(key, parse_site_php(SITE_PHP)[0]["raw"])


class DiffTests(unittest.TestCase):
    def run_compare(self, rows=None, php=SITE_PHP, merges=None):
        site = {"ok": True, "path": "data/wiki/x.php", "records": parse_site_php(php),
                "fields": ["cost", "income", "name", "rarity"]}
        merges = SHEET_MERGES if rows is None and merges is None else merges
        return compare(workbook(rows or SHEET_ROWS, merges), site)

    def test_statuses_cover_every_record_exactly_once(self):
        result = self.run_compare()
        self.assertTrue(result["ok"])
        self.assertEqual(result["counts"][NO_CHANGE], 3)
        self.assertEqual(result["counts"][CREATE], 1)
        self.assertEqual(result["counts"][SERVER_ONLY], 1)
        self.assertEqual(result["counts"][CONFLICT], 0)

    def test_identical_rows_never_produce_an_update(self):
        entry = next(e for e in self.run_compare()["entries"] if e["identity"] == "Tim Cheese")
        self.assertEqual(entry["status"], NO_CHANGE)
        self.assertEqual(entry["changed_fields"], [])

    def test_real_difference_is_an_update_with_both_values(self):
        rows = [["Common", "Fishini Bossini", 1, 50], ["Common", "Tim Cheese", 9, 250]]
        entry = next(e for e in self.run_compare(rows)["entries"] if e["identity"] == "Tim Cheese")
        self.assertEqual(entry["status"], UPDATE)
        changed = {c["field"]: (c["sheet"], c["site"]) for c in entry["changed_fields"]}
        self.assertEqual(changed["Base Income ($/s)"], (9, 5.0))
        self.assertEqual(entry["evidence_sheet"][:16], "ev::compare::she")
        self.assertTrue(entry["evidence_site"])

    def test_server_only_never_proposes_a_deletion(self):
        entry = next(e for e in self.run_compare()["entries"] if e["status"] == SERVER_ONLY)
        self.assertEqual(entry["identity"], "Vieux Modele")
        self.assertIn("Aucune suppression", entry["recommended_action"])

    def test_empty_sheet_field_never_proposes_blanking_the_site(self):
        # Le Sheet muet n'est pas un ordre d'effacement : sinon la comparaison
        # deviendrait un outil de destruction de donnees.
        rows = [["Common", "Fishini Bossini", 1, 50], ["", "Tim Cheese", 5, 250]]
        entry = next(e for e in self.run_compare(rows)["entries"] if e["identity"] == "Tim Cheese")
        self.assertEqual(entry["status"], NO_CHANGE)
        self.assertEqual(entry["changed_fields"], [])
        self.assertEqual([c["field"] for c in entry["missing_fields"]], ["Rarity"])


    def test_non_numeric_sheet_value_is_invalid_not_an_update(self):
        rows = [["Common", "Fishini Bossini", 1, 50], ["Common", "Tim Cheese", "à venir", 250]]
        entry = next(e for e in self.run_compare(rows)["entries"] if e["identity"] == "Tim Cheese")
        self.assertEqual(entry["status"], INVALID)
        self.assertEqual(entry["changed_fields"], [])

    def test_ambiguous_identity_is_a_conflict_never_an_update(self):
        rows = [["Common", "Tim Cheese", 1, 50], ["Common", "tim  cheese", 7, 70]]
        result = self.run_compare(rows)
        self.assertEqual(result["counts"][CONFLICT], 2)
        self.assertEqual(result["counts"][UPDATE], 0)

    def test_no_write_is_ever_performed(self):
        self.assertFalse(self.run_compare()["write_performed"])


class MergedRarityTests(unittest.TestCase):
    def test_section_rarity_comes_from_the_merge_range(self):
        # Rareté écrite une seule fois, fusionnée sur toute la section.
        rows = [["Secret", "A", 1, 10], ["", "B", 2, 20], ["", "C", 3, 30]]
        merges = [{"first_row": 3, "last_row": 5, "first_col": 1, "last_col": 1}]
        records = read_sheet_records(workbook(rows, merges))["records"]
        self.assertEqual([r["raw"]["Rarity"] for r in records], ["Secret", "Secret", "Secret"])

    def test_without_a_merge_an_empty_rarity_stays_empty(self):
        rows = [["Secret", "A", 1, 10], ["", "B", 2, 20]]
        records = read_sheet_records(workbook(rows))["records"]
        self.assertEqual(records[1]["raw"]["Rarity"], "")


class WorkspaceTests(unittest.TestCase):
    def payload(self):
        wb = workbook(SHEET_ROWS, SHEET_MERGES)
        site = {"ok": True, "path": "data/wiki/x.php", "records": parse_site_php(SITE_PHP),
                "fields": []}
        result = compare(wb, site)
        base = build_payload(request="Compare ce Google Sheet avec mon site",
                             source={"kind": "google_sheet", "label": "wb", "url": "u"},
                             analysis=analyze_workbook(wb), narrative="",
                             grounding={"ok": True})
        return attach_comparison(base, result), result

    def test_comparison_section_is_exposed_and_focused(self):
        payload, _ = self.payload()
        self.assertIn("site_comparison", [s["id"] for s in payload["sections"]])
        self.assertEqual(payload["focus"], "site_comparison")
        self.assertFalse(payload["comparison"]["write_performed"])

    def test_every_entry_resolves_its_evidence(self):
        payload, _ = self.payload()
        ids = {e["id"] for e in payload["evidence"]}
        for entry in payload["comparison"]["entries"]:
            for key in ("evidence_sheet", "evidence_site"):
                if entry[key]:
                    self.assertIn(entry[key], ids, f"{entry['identity']} {key}")

    def test_digest_reports_counts_and_read_only(self):
        _, result = self.payload()
        digest = comparison_digest(result)
        self.assertIn("Aucune écriture", digest)
        self.assertIn("manquants sur le site", digest)


if __name__ == "__main__":
    unittest.main()
