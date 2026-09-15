"""Connecteur email unifié — presets, détection et normalisation.

Vérifie que :
- le fournisseur est détecté à partir d'une adresse (gmail, hotmail→outlook, icloud…) ;
- `mail_settings` normalise les trois formes de connecteur (email / imap / smtp) ;
- le catalogue expose le type « email » avec les libellés des fournisseurs.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.mail_providers import (  # noqa: E402
    PROVIDER_KEYS, detect_provider, mail_settings, provider_for,
)


class TestDetection(unittest.TestCase):
    def test_known_domains(self):
        self.assertEqual(detect_provider("vous@gmail.com"), "gmail")
        self.assertEqual(detect_provider("vous@googlemail.com"), "gmail")
        self.assertEqual(detect_provider("x@hotmail.fr"), "outlook")
        self.assertEqual(detect_provider("x@outlook.com"), "outlook")
        self.assertEqual(detect_provider("x@live.fr"), "outlook")
        self.assertEqual(detect_provider("x@icloud.com"), "icloud")
        self.assertEqual(detect_provider("x@me.com"), "icloud")
        self.assertEqual(detect_provider("x@yahoo.fr"), "yahoo")

    def test_unknown_is_custom(self):
        self.assertEqual(detect_provider("chef@monserveur.io"), "custom")
        self.assertEqual(detect_provider(""), "custom")

    def test_provider_for_accepts_key_or_address(self):
        self.assertEqual(provider_for("gmail").key, "gmail")
        self.assertEqual(provider_for("vous@icloud.com").key, "icloud")


class TestMailSettings(unittest.TestCase):
    def test_unified_email_provider(self):
        s = mail_settings({"provider": "gmail", "email": "vous@gmail.com"}, "email")
        self.assertEqual(s["imap_host"], "imap.gmail.com")
        self.assertEqual(s["imap_port"], 993)
        self.assertTrue(s["imap_ssl"])
        self.assertEqual(s["smtp_host"], "smtp.gmail.com")
        self.assertEqual(s["smtp_port"], 587)
        self.assertTrue(s["smtp_tls"])
        self.assertEqual(s["username"], "vous@gmail.com")
        self.assertEqual(s["from_address"], "vous@gmail.com")

    def test_unified_email_detects_provider_from_address(self):
        s = mail_settings({"email": "x@outlook.fr"}, "email")
        self.assertEqual(s["provider"], "outlook")
        self.assertEqual(s["imap_host"], "outlook.office365.com")

    def test_overrides_win_over_presets(self):
        s = mail_settings({"provider": "gmail", "smtp_host": "relais.monentreprise.fr",
                           "smtp_port": 2500}, "email")
        self.assertEqual(s["smtp_host"], "relais.monentreprise.fr")
        self.assertEqual(s["smtp_port"], 2500)

    def test_legacy_imap_shape(self):
        s = mail_settings({"host": "imap.exemple.fr", "port": 143, "ssl": False,
                           "username": "bob"}, "imap")
        self.assertEqual(s["imap_host"], "imap.exemple.fr")
        self.assertEqual(s["imap_port"], 143)
        self.assertFalse(s["imap_ssl"])
        self.assertEqual(s["username"], "bob")
        self.assertEqual(s["smtp_host"], "")

    def test_legacy_smtp_shape(self):
        s = mail_settings({"host": "smtp.exemple.fr", "port": 465, "tls": False,
                           "username": "bob", "from_address": "bob@exemple.fr"}, "smtp")
        self.assertEqual(s["smtp_host"], "smtp.exemple.fr")
        self.assertEqual(s["smtp_port"], 465)
        self.assertFalse(s["smtp_tls"])
        self.assertEqual(s["from_address"], "bob@exemple.fr")
        self.assertEqual(s["imap_host"], "")


class TestUnifiedConnectorInCatalog(unittest.TestCase):
    def test_email_connector_is_registered(self):
        from jarvis.connectors import CONNECTOR_TYPES, type_catalog

        spec = CONNECTOR_TYPES["email"]
        # read + write pour lire, execute requis pour l'envoi (risque SENSITIVE).
        self.assertEqual(spec.default_permissions, ("read", "write", "execute"))
        self.assertEqual(spec.label, "Email (SMTP + IMAP)")
        field_keys = [f.key for f in spec.fields]
        self.assertIn("provider", field_keys)
        self.assertIn("email", field_keys)
        self.assertIn("imap_host", field_keys)
        self.assertIn("smtp_host", field_keys)

    def test_provider_select_has_friendly_labels(self):
        from jarvis.connectors import type_catalog

        cat = next(t for t in type_catalog() if t["type"] == "email")
        provider_field = next(f for f in cat["fields"] if f["key"] == "provider")
        self.assertEqual(list(provider_field["options"]), list(PROVIDER_KEYS))
        self.assertEqual(provider_field["options_labels"][0], "Gmail")
        self.assertIn("Autre", provider_field["options_labels"][-1])

    def test_legacy_types_still_registered(self):
        from jarvis.connectors import CONNECTOR_TYPES

        self.assertIn("smtp", CONNECTOR_TYPES)
        self.assertIn("imap", CONNECTOR_TYPES)


class _Conns:
    def __init__(self, conns):
        self._conns = conns

    def raw(self, connector_id):
        return next((c for c in self._conns if c["id"] == connector_id), None)

    def active(self, ctype):
        return [c for c in self._conns if c.get("enabled") and c.get("type") == ctype]


class _Core:
    def __init__(self, conns):
        self.connectors = _Conns(conns)
        self.events = None


class TestUnifiedProviderSelection(unittest.TestCase):
    """`MailProcessor` doit choisir le connecteur `email` unifié quand c'est le
    seul, sans retomber silencieusement sur le mock."""

    def test_email_connector_wins_over_mock(self):
        from jarvis.mail import ImapMailProvider, MailProcessor

        core = _Core([
            {"id": "g1", "type": "email", "name": "Gmail", "enabled": True,
             "status": "connected", "config": {"provider": "gmail", "email": "vous@gmail.com"}},
        ])
        provider = MailProcessor(core).provider()
        self.assertIsInstance(provider, ImapMailProvider)
        self.assertTrue(provider.available())

    def test_no_mail_connector_falls_back_to_mock_when_allowed(self):
        from jarvis.mail import MailProcessor, MockMailProvider

        core = _Core([])
        provider = MailProcessor(core).provider()
        self.assertIsInstance(provider, MockMailProvider)

    def test_forcing_no_mock_returns_none_without_connector(self):
        from jarvis.mail import MailProcessor

        core = _Core([])
        self.assertIsNone(MailProcessor(core).provider(use_mock=False))


if __name__ == "__main__":
    unittest.main()