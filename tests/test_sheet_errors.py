"""Correctif SHEETTAB_NOT_FOUND : traduction, résolution gid→onglet, non-privacy.

Vérifie que :
- aucun code d'erreur brut ne sort dans le chat (comparison_digest) ;
- SHEET_TAB_NOT_FOUND ne déclenche JAMAIS le message « Google Sheet privé » ;
- la résolution des onglets suit l'ordre documenté (gid résolu → nom exact →
  casse → « brainrot » → premier peuplé → premier) ;
- l'orchestrateur stocke et réinjecte la tâche échouée (TACHE_PRECEDENTE).
"""
import unittest

from jarvis.analysis_workspace import attach_comparison, comparison_digest
from jarvis.brainrot_compare import compare, read_sheet_records, resolve_sheet_tab
from jarvis.google_sheets import parse_sheet_url
from jarvis.orchestrator import Orchestrator
from jarvis.sheet_errors import (canonical_sheet_error, is_access_error,
                                 sheet_error_message, tab_resolution_suggestion)


def wb(sheets):
    return {"workbook": "wb", "sheets": sheets}


def sheet(name, rows=5, filled=True):
    return {"name": name, "cells": [["Rarity", "Name"]], "useful_rows": (rows if filled else 0),
            "row_numbers": [1, 2], "merges": [], "gid": str(rows)}


class CanonicalisationTests(unittest.TestCase):
    def test_canonicalises_every_spelling_of_tab_not_found(self):
        for alias in ("SHEETTAB_NOT_FOUND", "SHEETTABNOT_FOUND", "SHEET_TAB_NOT_FOUND"):
            self.assertEqual(canonical_sheet_error(alias), "SHEET_TAB_NOT_FOUND")

    def test_access_errors_are_the_only_privacy_excuse(self):
        self.assertTrue(is_access_error("SHEET_ACCESS_DENIED"))
        self.assertTrue(is_access_error("GOOGLE_SHEET_ACCESS_DENIED"))
        for safe in ("SHEET_TAB_NOT_FOUND", "SHEET_EMPTY", "SHEET_NOT_FOUND", "NETWORK_ERROR"):
            self.assertFalse(is_access_error(safe), safe)

    def test_tab_not_found_message_never_mentions_privacy(self):
        message = sheet_error_message("SHEET_TAB_NOT_FOUND")
        self.assertIn("trouvé", message)
        self.assertIn("onglet", message)
        for forbidden in ("privé", "privés", "authentification", "accès"):
            self.assertNotIn(forbidden, message, forbidden)

    def test_unknown_code_falls_back_without_leaking_guessed_meaning(self):
        message = sheet_error_message("SOMETHING_NEW", detail="détail additionnel")
        self.assertIn("détail additionnel", message)
        self.assertNotIn("privé", message)


class TabResolutionTests(unittest.TestCase):
    def test_gid_resolved_tab_wins_over_everything(self):
        tabs = [sheet("ALL BRAINROTS", 8), sheet("Archive", 2), sheet("Brouillon", 1)]
        tabs[1]["gid"] = "0"
        workbook = wb(tabs)
        workbook["selected_tab"] = "Archive"
        resolved, mode, _ = resolve_sheet_tab(workbook, "ALL BRAINROTS")
        self.assertEqual(mode, "url_gid")
        self.assertEqual(resolved["name"], "Archive")

    def test_exact_name_beats_casefold_and_contains(self):
        tabs = [sheet("ALL BRAINROTS", 8), sheet("Brainrots2", 3)]
        resolved, mode, _ = resolve_sheet_tab(wb(tabs), "ALL BRAINROTS")
        self.assertEqual(mode, "exact")
        self.assertEqual(resolved["name"], "ALL BRAINROTS")

    def test_casefold_resolves_when_only_the_case_differs(self):
        tabs = [sheet("all brainrots", 8), sheet("Other", 2)]
        resolved, mode, _ = resolve_sheet_tab(wb(tabs), "ALL BRAINROTS")
        self.assertEqual(mode, "casefold")
        self.assertEqual(resolved["name"], "all brainrots")

    def test_contains_brainrot_when_no_exact_match(self):
        tabs = [sheet("Données", 2), sheet("Tableau BRAINROTS complet", 9)]
        resolved, mode, _ = resolve_sheet_tab(wb(tabs), "ALL BRAINROTS")
        self.assertEqual(mode, "contains")
        self.assertEqual(resolved["name"], "Tableau BRAINROTS complet")

    def test_first_populated_is_the_fallback_for_unknown_tab(self):
        tabs = [sheet("A", 0, filled=False), sheet("B", 7), sheet("C", 1)]
        resolved, mode, _ = resolve_sheet_tab(wb(tabs), "ALL BRAINROTS")
        self.assertEqual(mode, "first_populated")
        self.assertEqual(resolved["name"], "B")

    def test_as_last_resort_first_tab_is_used(self):
        tabs = [sheet("Solo", 0, filled=False)]
        resolved, mode, _ = resolve_sheet_tab(wb(tabs), "ALL BRAINROTS")
        self.assertEqual(resolved["name"], "Solo")

    def test_empty_workbook_is_an_error_not_a_guess(self):
        workbook = {"workbook": "wb", "sheets": []}
        resolved, mode, names = resolve_sheet_tab(workbook, "ALL BRAINROTS")
        self.assertIsNone(resolved)
        self.assertEqual(mode, "empty")
        self.assertEqual(names, [])


class ReadRecordsErrorTests(unittest.TestCase):
    def test_auto_detects_when_requested_tab_name_is_missing(self):
        # Le nom « ALL BRAINROTS » n'existe pas : la résolution retombe sur le
        # premier onglet peuplé au lieu d'échouer (correctif gid/onglet).
        cells = [["HOME", "", "", ""],
                 ["Rarity", "Name", "Base Income ($/s)", "Price ($)"],
                 ["Common", "Fishini Bossini", 1, 50],
                 ["Common", "Tim Cheese", 5, 250]]
        tabs = [{"name": "Données", "gid": "7", "cells": cells,
                 "useful_rows": 4, "row_numbers": [1, 2, 3, 4], "merges": [],
                 "non_empty_cells": 8, "headers": ["Rarity", "Name",
                                                   "Base Income ($/s)", "Price ($)"]}]
        result = read_sheet_records(wb(tabs), tab="ALL BRAINROTS")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["tab"], "Données")
        self.assertEqual(result["tab_resolution"], "first_populated")
        self.assertEqual(len(result["records"]), 2)
        self.assertNotIn("gid:", result["tab"])

    def test_empty_workbook_reports_sheet_empty_not_privacy(self):
        result = read_sheet_records({"workbook": "wb", "sheets": []}, tab="ALL BRAINROTS")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "SHEET_EMPTY")
        self.assertEqual(result["available_tabs"], [])

    def test_compare_propagates_error_context(self):
        result = compare({"workbook": "wb", "sheets": []}, {"ok": True, "records": []})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "SHEET_EMPTY")
        self.assertEqual(result["tab_requested"], "ALL BRAINROTS")

    def test_empty_populated_check_returns_sheet_empty(self):
        tabs = [sheet("ALL BRAINROTS", 0, filled=False)]
        result = read_sheet_records(wb(tabs), tab="ALL BRAINROTS")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "SHEET_EMPTY")


class DigestTests(unittest.TestCase):
    def test_interrupted_digest_never_leaks_raw_code(self):
        comparison = {"ok": False, "error": "SHEET_TAB_NOT_FOUND",
                      "tab_requested": "ALL BRAINROTS",
                      "available_tabs": [{"name": "Données", "gid": "0"}]}
        digest = comparison_digest(comparison)
        self.assertIn("COMPARAISON INTERROMPUE", digest)
        self.assertIn("onglet", digest.lower())
        self.assertIn("Données", digest)
        self.assertNotIn("SHEET_TAB_NOT_FOUND", digest)
        self.assertNotIn("privé", digest)

    def test_workspace_payload_exposes_suggestion_and_available_tabs(self):
        comparison = {"ok": False, "error": "SHEET_TAB_NOT_FOUND",
                      "tab_requested": "ALL BRAINROTS",
                      "available_tabs": [{"name": "Données", "gid": "0"}]}
        payload = attach_comparison({"sections": [], "actions": [], "evidence": []}, comparison)
        c = payload["comparison"]
        self.assertFalse(c["ok"])
        self.assertTrue(c["interrupted"])
        self.assertEqual(c["code"], "SHEET_TAB_NOT_FOUND")
        self.assertNotIn("privé", c["error"])
        self.assertIn("Données", c["suggestion"])


class UrlParsingTests(unittest.TestCase):
    def test_gid_in_query_string(self):
        parsed = parse_sheet_url(
            "https://docs.google.com/spreadsheets/d/abc123/edit?gid=2038985060#gid=2038985060")
        self.assertEqual(parsed["sheet_id"], "abc123")
        self.assertEqual(parsed["gid"], "2038985060")

    def test_gid_in_fragment_only(self):
        parsed = parse_sheet_url("https://docs.google.com/spreadsheets/d/abc123/edit#gid=42")
        self.assertEqual(parsed["sheet_id"], "abc123")
        self.assertEqual(parsed["gid"], "42")

    def test_missing_gid_defaults_to_zero(self):
        parsed = parse_sheet_url("https://docs.google.com/spreadsheets/d/abc123/edit")
        self.assertEqual(parsed["sheet_id"], "abc123")
        self.assertEqual(parsed["gid"], "0")


class LastTaskInjectionTests(unittest.TestCase):
    def test_last_task_block_honours_failed_tab_task_without_privacy(self):
        block = Orchestrator._last_task_block({
            "last_task": {"kind": "google_sheet_compare", "status": "failed",
                          "reason": "SHEET_TAB_NOT_FOUND", "url": "https://x", "gid": "7",
                          "spreadsheet_id": "abc", "available_tabs": [{"name": "Données"}]}})
        self.assertIn("TACHE_PRECEDENTE", block)
        self.assertIn("SHEET_TAB_NOT_FOUND", block)
        self.assertIn("Interdiction : ne dis PAS que le Sheet est privé", block)
        self.assertIn("'Données'", block)

    def test_last_task_block_for_access_error_still_guides(self):
        block = Orchestrator._last_task_block({
            "last_task": {"kind": "google_sheet_compare", "status": "failed",
                          "reason": "SHEET_ACCESS_DENIED", "url": "https://x"}})
        self.assertIn("Accès refusé : propose une authentification", block)

    def test_no_last_task_means_no_block(self):
        self.assertEqual(Orchestrator._last_task_block({}), "")
        self.assertEqual(Orchestrator._last_task_block(None), "")


if __name__ == "__main__":
    unittest.main()