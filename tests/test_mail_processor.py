"""Tri de la boîte de réception — règles déterministes, hors-ligne.

Ces tests ne parlent à aucun serveur : c'est précisément pour ça que le
MockMailProvider existe. Le tri doit être reproductible à l'identique, sinon
il n'est pas vérifiable.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.mail import (  # noqa: E402
    ARCHIVE, CATEGORIES, FORWARD, INVOICE, QUOTE, REPLY,
    MailMessage, MailProcessor, MockMailProvider, classify, summarize,
)


def msg(**kw) -> MailMessage:
    base = {"id": "x", "sender": "Test <test@exemple.fr>", "subject": "", "body": ""}
    base.update(kw)
    return MailMessage.from_dict(base)


class TestClassification(unittest.TestCase):
    def test_attachment_wins_over_text(self):
        """Une pièce jointe nommée « facture » est le signal le plus fiable."""
        m = msg(subject="Notre échange de jeudi", body="Rien de particulier.",
                attachments=["facture_2026_004.pdf"])
        v = classify(m)
        self.assertEqual(v.category, INVOICE)
        self.assertIn("facture_2026_004.pdf", v.reason)

    def test_quote_attachment(self):
        v = classify(msg(attachments=["devis_identite.pdf"]))
        self.assertEqual(v.category, QUOTE)

    def test_invoice_from_subject(self):
        self.assertEqual(classify(msg(subject="Relance de paiement")).category, INVOICE)
        self.assertEqual(classify(msg(subject="Facture F2026-004")).category, INVOICE)

    def test_quote_from_subject(self):
        self.assertEqual(classify(msg(subject="Demande de devis")).category, QUOTE)
        self.assertEqual(classify(msg(subject="Proposition commerciale")).category, QUOTE)

    def test_forward_request(self):
        v = classify(msg(subject="TR: dossier technique",
                         body="Merci de transférer au service concerné."))
        self.assertEqual(v.category, FORWARD)

    def test_newsletter_is_archived_with_its_real_reason(self):
        """Le motif affiché doit être le vrai : newsletter, pas « no-reply »."""
        v = classify(msg(sender="Hebdo <newsletter@exemple.fr>",
                         subject="Votre sélection", body="Pour vous désabonner, cliquez ici."))
        self.assertEqual(v.category, ARCHIVE)
        self.assertIn("Newsletter", v.reason)

    def test_automated_sender_is_archived(self):
        v = classify(msg(sender="Notifications <no-reply@exemple.com>",
                         subject="Sauvegarde terminée", body="Message automatique."))
        self.assertEqual(v.category, ARCHIVE)
        self.assertIn("automatique", v.reason)

    def test_automated_sender_does_not_swallow_a_real_invoice(self):
        """Un no-reply qui envoie une facture reste une facture."""
        v = classify(msg(sender="Facturation <no-reply@fournisseur.fr>",
                         subject="Votre facture de septembre"))
        self.assertEqual(v.category, INVOICE)

    def test_direct_question_needs_reply(self):
        v = classify(msg(subject="Délai de livraison",
                         body="Est-ce que vous pensez pouvoir livrer avant vendredi ?"))
        self.assertEqual(v.category, REPLY)

    def test_polite_request_needs_reply(self):
        v = classify(msg(subject="Maquette", body="Pourriez-vous me confirmer la date."))
        self.assertEqual(v.category, REPLY)

    def test_no_signal_is_archived_and_says_so(self):
        v = classify(msg(subject="Compte rendu de réunion",
                         body="Voici le compte rendu pour archivage."))
        self.assertEqual(v.category, ARCHIVE)
        self.assertIn("Aucun signal", v.reason)

    def test_every_verdict_carries_a_reason(self):
        for m in MockMailProvider().fetch(limit=50):
            with self.subTest(message=m.id):
                self.assertTrue(classify(m).reason.strip())

    def test_classification_is_stable(self):
        """Deux passes donnent exactement le même résultat."""
        messages = MockMailProvider().fetch(limit=50)
        first = [classify(m).category for m in messages]
        second = [classify(m).category for m in messages]
        self.assertEqual(first, second)


class TestMockProvider(unittest.TestCase):
    def test_reads_bundled_inbox(self):
        provider = MockMailProvider()
        self.assertTrue(provider.available())
        self.assertTrue(provider.fetch(limit=50))

    def test_unread_filter(self):
        messages = MockMailProvider().fetch(limit=50, unread_only=True)
        self.assertTrue(messages)
        self.assertTrue(all(m.unread for m in messages))

    def test_missing_file_is_not_available(self):
        provider = MockMailProvider(Path(tempfile.gettempdir()) / "inbox-inexistante.json")
        self.assertFalse(provider.available())
        self.assertEqual(provider.fetch(), [])

    def test_snippet_is_flattened_and_capped(self):
        m = msg(body="ligne un\n\n   ligne deux   \n" + "x" * 400)
        self.assertNotIn("\n", m.snippet)
        self.assertLessEqual(len(m.snippet), 180)


class _Bus:
    """Bus minimal : on vérifie l'ordre réel des événements émis."""

    def __init__(self):
        self.events = []

    def emit(self, kind, payload=None, **kw):
        self.events.append((kind, payload or {}))
        return {}


class _Core:
    def __init__(self, bus):
        self.events = bus


class TestProcessor(unittest.TestCase):
    def setUp(self):
        self.bus = _Bus()
        self.processor = MailProcessor(_Core(self.bus))

    def test_process_mock_inbox(self):
        result = self.processor.process(use_mock=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "mock")
        self.assertEqual(sum(result["counts"].values()), result["total"])
        self.assertEqual(set(result["buckets"]), set(CATEGORIES))

    def test_events_are_emitted_while_sorting_not_at_the_end(self):
        result = self.processor.process(use_mock=True)
        kinds = [k for k, _ in self.bus.events]
        self.assertEqual(kinds[0], "mail.inbox.started")
        self.assertEqual(kinds[-1], "mail.inbox.completed")
        self.assertEqual(kinds.count("mail.message.classified"), result["total"])

    def test_each_card_carries_category_and_reason(self):
        result = self.processor.process(use_mock=True)
        for card in result["cards"]:
            with self.subTest(card=card["id"]):
                self.assertIn(card["category"], CATEGORIES)
                self.assertTrue(card["category_label"])
                self.assertTrue(card["reason"])

    def test_cards_never_leak_the_full_body(self):
        """Le Kanban reçoit un extrait, pas le message entier."""
        for card in self.processor.process(use_mock=True)["cards"]:
            self.assertNotIn("body", card)

    def test_custom_inbox_file(self):
        payload = {"messages": [
            {"id": "a", "sender": "Client <c@exemple.fr>", "subject": "Devis urgent", "body": ""},
            {"id": "b", "sender": "no-reply@exemple.fr", "subject": "Ticket clos", "body": ""},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inbox.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            messages = MockMailProvider(path).fetch()
            self.assertEqual([classify(m).category for m in messages], [QUOTE, ARCHIVE])

    def test_no_provider_reports_clearly(self):
        processor = MailProcessor(_Core(self.bus))
        result = processor.process(use_mock=False)
        self.assertFalse(result["ok"])
        self.assertIn("IMAP", result["error"])

    def test_summarize_counts(self):
        text = summarize(self.processor.process(use_mock=True))
        self.assertIn("triés", text)
        self.assertIn("Factures", text)

    def test_summarize_error(self):
        self.assertIn("IMAP", summarize({"ok": False, "error": "Aucun connecteur IMAP."}))


if __name__ == "__main__":
    unittest.main()
