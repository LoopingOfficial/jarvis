"""BUILD JARVIS_ANALYSIS_WORKSPACE_V1 : payload structuré, preuves, intents."""
import unittest

from jarvis.analysis_workspace import (SECTION_ORDER, build_payload, chat_digest,
                                       detect_intent)
from jarvis.sheet_semantics import analyze_workbook
from tests.test_sheet_semantics import ALL_BRAINROTS, NOTES, SIDE_BY_SIDE, sheet


def payload(request="analyse ce fichier et dis-moi ce que tu en penses", narrative="Synthèse."):
    workbook = {"workbook": "abc", "sheets": [sheet("ALL BRAINROTS", ALL_BRAINROTS),
                                              sheet("Side", SIDE_BY_SIDE),
                                              sheet("Notes", NOTES)]}
    return build_payload(request=request,
                         source={"kind": "google_sheet", "label": "le classeur", "url": "u"},
                         analysis=analyze_workbook(workbook),
                         narrative=narrative,
                         grounding={"ok": True, "code": ""},
                         duration_ms=1234)


class PayloadShapeTests(unittest.TestCase):
    def test_payload_exposes_every_contract_field(self):
        p = payload()
        for key in ("request", "source", "summary", "metrics", "findings", "warnings",
                    "recommendations", "sections", "evidence", "tables", "actions"):
            self.assertIn(key, p, key)
        self.assertEqual([s["id"] for s in p["sections"]], SECTION_ORDER)

    def test_metrics_are_counted_not_narrated(self):
        p = payload(narrative="Il y a 999 onglets et 888 tableaux.")
        metrics = {m["key"]: m["value"] for m in p["metrics"]}
        self.assertEqual(metrics["sheets"], 3)
        # ALL BRAINROTS (2 regions) + Side (2 regions cote a cote).
        self.assertEqual(metrics["tables"], 4)
        self.assertNotIn(999, metrics.values())

    def test_source_lists_real_sheet_names(self):
        self.assertEqual(payload()["source"]["sheets"], ["ALL BRAINROTS", "Side", "Notes"])


class GroundingTests(unittest.TestCase):
    def test_no_finding_without_resolvable_evidence(self):
        p = payload()
        ids = {e["id"] for e in p["evidence"]}
        self.assertTrue(p["findings"])
        for item in p["findings"]:
            self.assertIn(item["evidence"], ids, item["title"])
        for item in p["warnings"]:
            self.assertTrue(not item["evidence"] or item["evidence"] in ids, item["title"])

    def test_evidence_carries_real_cells_or_a_traced_computation(self):
        for ev in payload()["evidence"]:
            self.assertTrue(ev["rows"] or ev["computation"], ev["id"])
            self.assertTrue(ev["sheet"] and ev["location"])

    def test_narrative_is_flagged_as_generative(self):
        p = payload(narrative="texte du modèle")
        self.assertTrue(p["summary"]["narrative_is_generative"])
        self.assertEqual(p["summary"]["narrative"], "texte du modèle")

    def test_statistics_only_on_real_numeric_columns(self):
        columns = {s["column"] for s in payload()["statistics"]}
        self.assertIn("Income", columns)
        for label in ("Name", "Rarity", "Step", "Pet"):
            self.assertNotIn(label, columns)

    def test_tables_keep_regions_separate(self):
        titles = [t["title"] for t in payload()["tables"]]
        self.assertIn("ALL BRAINROTS", titles)
        self.assertIn("INCOME CALCULATION", titles)
        self.assertEqual(len([t for t in payload()["tables"] if t["sheet"] == "Side"]), 2)


class IntentTests(unittest.TestCase):
    def test_intent_drives_the_opened_view(self):
        cases = {"analyse ce fichier": ("overview", "summary"),
                 "trouve les incohérences": ("anomalies", "warnings"),
                 "compare les deux onglets": ("comparison", "tables"),
                 "donne les statistiques": ("statistics", "statistics"),
                 "synchronise avec ma base": ("sync", "findings")}
        for request, (intent, focus) in cases.items():
            with self.subTest(request):
                self.assertEqual(detect_intent(request), intent)
                self.assertEqual(payload(request)["focus"], focus)

    def test_chat_digest_is_short(self):
        digest = chat_digest(payload())
        self.assertLessEqual(len(digest.splitlines()), 3)
        self.assertIn("onglets", digest)


if __name__ == "__main__":
    unittest.main()
