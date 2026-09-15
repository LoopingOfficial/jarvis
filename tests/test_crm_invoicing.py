"""CRM (levée d'ambiguïté) et facturation (arithmétique HT/TVA/TTC).

Le calcul monétaire est la partie qui doit être irréprochable : une facture
fausse d'un centime est une facture fausse. Ces tests ne touchent ni au réseau
ni à Chromium — le rendu PDF est vérifié séparément et sauté s'il est absent.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.crm import (  # noqa: E402
    AMBIGUOUS, FOUND, NOT_FOUND, CrmStore, clarification_question, fold, label_of,
)
from jarvis.db import Database  # noqa: E402
from jarvis.invoicing import (  # noqa: E402
    INVOICE, QUOTE, LineItem, build_document, compute_totals, fmt_money, money,
    next_number, playwright_available, render_html, render_pdf,
)


def store() -> CrmStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return CrmStore(Database(tmp.name))


CONTACTS = [
    {"name": "Martin Lefevre", "company": "L'Atelier", "email": "martin@atelier.fr"},
    {"name": "Martin Roux", "company": "Pixel Studio", "email": "m.roux@pixel.fr"},
    {"name": "Sophie Renard", "company": "Renard & Associes", "email": "s@renard.fr"},
    {"name": "Frédéric Petit", "company": "", "email": "f.petit@exemple.fr"},
]


class TestCrmSearch(unittest.TestCase):
    def setUp(self):
        self.crm = store()
        for c in CONTACTS:
            self.crm.upsert(c)

    def test_ambiguous_returns_every_option_and_picks_none(self):
        """Le cas central : « Martin » ne doit JAMAIS être résolu tout seul."""
        result = self.crm.resolve("Martin")
        self.assertEqual(result["status"], AMBIGUOUS)
        self.assertNotIn("contact", result)
        labels = [o["label"] for o in result["options"]]
        self.assertIn("Martin Lefevre — L'Atelier", labels)
        self.assertIn("Martin Roux — Pixel Studio", labels)

    def test_exact_name_lifts_the_ambiguity(self):
        result = self.crm.resolve("Martin Roux")
        self.assertEqual(result["status"], FOUND)
        self.assertEqual(result["contact"]["company"], "Pixel Studio")

    def test_single_partial_match_is_found(self):
        self.assertEqual(self.crm.resolve("Sophie")["status"], FOUND)

    def test_company_search(self):
        result = self.crm.resolve("Pixel")
        self.assertEqual(result["status"], FOUND)
        self.assertEqual(result["contact"]["name"], "Martin Roux")

    def test_accents_are_ignored(self):
        self.assertEqual(self.crm.resolve("frederic")["status"], FOUND)
        self.assertEqual(self.crm.resolve("Frédéric")["status"], FOUND)

    def test_unknown_is_not_invented(self):
        result = self.crm.resolve("Personne Inexistante")
        self.assertEqual(result["status"], NOT_FOUND)
        self.assertEqual(result["matches"], [])

    def test_empty_query_finds_nothing(self):
        self.assertEqual(self.crm.search(""), [])
        self.assertEqual(self.crm.search("   "), [])

    def test_clarification_question_names_the_options(self):
        question = clarification_question(self.crm.resolve("Martin"))
        self.assertIn("Martin Lefevre", question)
        self.assertIn("Martin Roux", question)
        self.assertTrue(question.rstrip().endswith("?"))

    def test_label_without_company(self):
        self.assertEqual(label_of({"name": "Camille", "company": ""}), "Camille")

    def test_fold(self):
        self.assertEqual(fold("Frédéric ÉLAN"), "frederic elan")


class TestCrmWrite(unittest.TestCase):
    def setUp(self):
        self.crm = store()

    def test_seed_only_when_empty(self):
        payload = '{"contacts":[{"name":"Amorce","company":"Test"}]}'
        with tempfile.TemporaryDirectory() as tmp:
            seed = Path(tmp) / "seed.json"
            seed.write_text(payload, encoding="utf-8")
            self.assertEqual(self.crm.seed_if_empty(seed), 1)
            # Deuxième passage : la table n'est plus vide, on n'écrase rien.
            self.assertEqual(self.crm.seed_if_empty(seed), 0)
            self.assertEqual(self.crm.count(), 1)

    def test_upsert_updates_in_place(self):
        created = self.crm.upsert({"name": "Client", "email": "a@b.fr"})
        updated = self.crm.upsert({"id": created["id"], "name": "Client", "email": "c@d.fr"})
        self.assertEqual(created["id"], updated["id"])
        self.assertEqual(updated["email"], "c@d.fr")
        self.assertEqual(self.crm.count(), 1)

    def test_delete(self):
        c = self.crm.upsert({"name": "Ephemere"})
        self.assertTrue(self.crm.delete(c["id"]))
        self.assertIsNone(self.crm.get(c["id"]))


class TestMoneyMath(unittest.TestCase):
    def test_decimal_avoids_float_drift(self):
        """3 x 0,10 € doit valoir 0,30 € — pas 0,30000000000000004."""
        totals = compute_totals([LineItem.from_dict(
            {"description": "x", "quantity": 3, "unit_price": "0.10", "vat_rate": 20})])
        self.assertEqual(totals["net_ht"], Decimal("0.30"))
        self.assertEqual(totals["total_ttc"], Decimal("0.36"))

    def test_single_rate(self):
        totals = compute_totals([LineItem.from_dict(
            {"description": "Presta", "quantity": 1, "unit_price": 1000, "vat_rate": 20})])
        self.assertEqual(totals["net_ht"], Decimal("1000.00"))
        self.assertEqual(totals["total_vat"], Decimal("200.00"))
        self.assertEqual(totals["total_ttc"], Decimal("1200.00"))

    def test_mixed_rates_are_grouped(self):
        totals = compute_totals([
            LineItem.from_dict({"description": "a", "unit_price": 1000, "vat_rate": 20}),
            LineItem.from_dict({"description": "b", "unit_price": 500, "vat_rate": 10}),
        ])
        self.assertEqual(totals["vat_by_rate"]["20"], Decimal("200.00"))
        self.assertEqual(totals["vat_by_rate"]["10"], Decimal("50.00"))
        self.assertEqual(totals["total_ttc"], Decimal("1750.00"))

    def test_global_discount_reduces_the_vat_too(self):
        """Une remise appliquée après la TVA donnerait un TTC faux."""
        totals = compute_totals([LineItem.from_dict(
            {"description": "a", "unit_price": 1000, "vat_rate": 20})], Decimal("10"))
        self.assertEqual(totals["net_ht"], Decimal("900.00"))
        self.assertEqual(totals["total_vat"], Decimal("180.00"))
        self.assertEqual(totals["total_ttc"], Decimal("1080.00"))

    def test_global_discount_spread_over_mixed_rates(self):
        totals = compute_totals([
            LineItem.from_dict({"description": "a", "unit_price": 5000, "vat_rate": 20}),
            LineItem.from_dict({"description": "b", "quantity": 3, "unit_price": 500, "vat_rate": 20}),
            LineItem.from_dict({"description": "c", "quantity": 2, "unit_price": 300, "vat_rate": 10}),
        ], Decimal("10"))
        self.assertEqual(totals["subtotal_ht"], Decimal("7100.00"))
        self.assertEqual(totals["base_by_rate"]["20"], Decimal("5850.00"))
        self.assertEqual(totals["base_by_rate"]["10"], Decimal("540.00"))
        self.assertEqual(totals["total_vat"], Decimal("1224.00"))
        self.assertEqual(totals["total_ttc"], Decimal("7614.00"))

    def test_line_discount(self):
        totals = compute_totals([LineItem.from_dict(
            {"description": "a", "unit_price": 200, "discount_pct": 25, "vat_rate": 20})])
        self.assertEqual(totals["net_ht"], Decimal("150.00"))
        self.assertEqual(totals["total_ttc"], Decimal("180.00"))

    def test_totals_are_always_coherent(self):
        for discount in (0, 5, 10, 33, 100):
            totals = compute_totals([
                LineItem.from_dict({"description": "a", "quantity": 7, "unit_price": "19.99", "vat_rate": 20}),
                LineItem.from_dict({"description": "b", "quantity": 3, "unit_price": "5.55", "vat_rate": "5.5"}),
            ], Decimal(str(discount)))
            with self.subTest(discount=discount):
                self.assertEqual(totals["net_ht"] + totals["total_vat"], totals["total_ttc"])

    def test_zero_rate(self):
        totals = compute_totals([LineItem.from_dict(
            {"description": "Export", "unit_price": 1000, "vat_rate": 0})])
        self.assertEqual(totals["total_vat"], Decimal("0.00"))
        self.assertEqual(totals["total_ttc"], Decimal("1000.00"))

    def test_comma_decimals_are_accepted(self):
        self.assertEqual(money("1234,56"), Decimal("1234.56"))

    def test_fmt_money_french(self):
        self.assertEqual(fmt_money(Decimal("1234.50")), "1 234,50")
        self.assertEqual(fmt_money(Decimal("7614.00")), "7 614,00")
        self.assertEqual(fmt_money(Decimal("0.30")), "0,30")


class TestDocument(unittest.TestCase):
    def test_numbering_is_sequential(self):
        first = next_number(INVOICE, [])
        self.assertTrue(first.startswith("F"))
        self.assertTrue(first.endswith("-0001"))
        self.assertEqual(next_number(INVOICE, [first]).split("-")[1], "0002")

    def test_numbering_ignores_foreign_prefixes(self):
        self.assertTrue(next_number(QUOTE, ["F2026-0009"]).endswith("-0001"))

    def test_invoice_has_due_date_quote_has_none_needed(self):
        inv = build_document(INVOICE, {"name": "X"}, [{"description": "a", "unit_price": 10}],
                             payment_terms_days=30)
        self.assertNotEqual(inv.issued_on, inv.due_on)

    def test_html_shows_totals_and_legal_mentions(self):
        doc = build_document(INVOICE, {"name": "Martin Roux", "company": "Pixel Studio"},
                             [{"description": "Presta", "unit_price": 1000, "vat_rate": 20}])
        html = render_html(doc)
        self.assertIn("FACTURE", html)
        self.assertIn("Martin Roux", html)
        self.assertIn("1 200,00", html)          # total TTC
        self.assertIn("indemnité forfaitaire", html)  # mention légale obligatoire
        self.assertIn("pénalités", html)

    def test_quote_html_mentions_validity(self):
        doc = build_document(QUOTE, {"name": "X"}, [{"description": "a", "unit_price": 10}])
        html = render_html(doc)
        self.assertIn("DEVIS", html)
        self.assertIn("valable 30 jours", html)

    def test_html_escapes_user_content(self):
        doc = build_document(QUOTE, {"name": "<script>alert(1)</script>"},
                             [{"description": "<b>x</b>", "unit_price": 1}])
        html = render_html(doc)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_to_dict_carries_the_totals(self):
        doc = build_document(INVOICE, {"name": "X"},
                             [{"description": "a", "unit_price": 1000, "vat_rate": 20}])
        d = doc.to_dict()
        self.assertEqual(d["total_ttc"], "1200.00")
        self.assertEqual(d["kind_label"], "FACTURE")


@unittest.skipUnless(playwright_available(), "Playwright absent : rendu PDF non vérifiable")
class TestPdfRender(unittest.TestCase):
    def test_pdf_is_a_real_pdf(self):
        doc = build_document(INVOICE, {"name": "Martin Roux", "company": "Pixel Studio"},
                             [{"description": "Presta", "unit_price": 1000, "vat_rate": 20}])
        with tempfile.TemporaryDirectory() as tmp:
            out = render_pdf(doc, Path(tmp))
            self.assertTrue(out["ok"], out.get("error"))
            path = Path(out["path"])
            self.assertTrue(path.is_file())
            self.assertGreater(out["bytes"], 1000)
            with path.open("rb") as fh:
                self.assertEqual(fh.read(5), b"%PDF-")
            self.assertEqual(out["total_ttc"], "1200.00")
            self.assertEqual(out["total_ttc_label"], "1 200,00 €")


if __name__ == "__main__":
    unittest.main()
