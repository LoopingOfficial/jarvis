import unittest
from unittest.mock import patch

from jarvis.google_sheets import parse_sheet_url
from jarvis.message_context import predict_intent


SHEET = "https://docs.google.com/spreadsheets/d/abc_DEF-123/edit?gid=42"


class GoogleSheetRoutingTests(unittest.TestCase):
    def test_generic_url_and_gid(self):
        self.assertEqual(parse_sheet_url(SHEET), {"sheet_id": "abc_DEF-123", "gid": "42"})

    def test_content_analysis_is_not_security_audit(self):
        resolved = predict_intent(f"analyse ce fichier {SHEET}")
        self.assertEqual(resolved.intent, "google_sheet")
        self.assertEqual(resolved.resource_type, "GOOGLE_SHEET")
        self.assertNotEqual(resolved.intent, "security_audit_readonly")

    def test_plain_file_analysis_is_not_security_audit(self):
        self.assertNotEqual(predict_intent("analyse marketplace.php").intent,
                            "security_audit_readonly")

    def test_explicit_security_still_audits(self):
        self.assertEqual(predict_intent("cherche les failles dans marketplace.php").intent,
                         "security_audit_readonly")

    def test_model_only_wins(self):
        resolved = predict_intent(f"MODE: MODEL_ONLY_BENCHMARK explique {SHEET}")
        self.assertFalse(resolved.tools_allowed)
        self.assertEqual(resolved.intent, "model_only")


if __name__ == "__main__":
    unittest.main()
