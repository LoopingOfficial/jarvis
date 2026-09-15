"""Résolution du connecteur pour les outils multi-types (`connector_types`).

Un outil comme email.read accepte un connecteur `imap` OU le connecteur
unifié `email`. Le runner doit choisir l'un d'eux, et pointer vers le bon
connecteur quand `connector_id` est fourni.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.tools.base import Tool  # noqa: E402
from jarvis.tools.runner import SecureToolRunner, ToolDenied  # noqa: E402


class _FakeConns:
    def __init__(self, conns):
        self._conns = conns

    def raw(self, connector_id):
        return next((c for c in self._conns if c["id"] == connector_id), None)

    def active(self, ctype):
        return [c for c in self._conns
                if c.get("enabled") and c.get("type") == ctype and c.get("status") == "connected"]


class _FakeCore:
    def __init__(self, conns):
        self.connectors = _FakeConns(conns)


def _handler(ctx):
    return None


class TestMultiTypeResolution(unittest.TestCase):
    def setUp(self):
        self.core = _FakeCore([
            {"id": "e1", "type": "email", "name": "Gmail", "enabled": True,
             "status": "connected", "config": {"provider": "gmail"}},
            {"id": "m1", "type": "imap", "name": "IMAP pro", "enabled": True,
             "status": "connected", "config": {"host": "imap.exemple.fr"}},
        ])
        self.runner = SecureToolRunner(self.core)
        self.tool = Tool(
            id="email.read", name="Lire", description="d", category="c",
            handler=_handler, connector_type="imap", connector_types=("imap", "email"),
        )

    def test_resolves_unified_connector_when_imap_missing(self):
        core = _FakeCore([
            {"id": "e1", "type": "email", "name": "Gmail", "enabled": True,
             "status": "connected", "config": {"provider": "gmail"}},
        ])
        runner = SecureToolRunner(core)
        resolved = runner._resolve_connector(self.tool, {})
        self.assertEqual(resolved["id"], "e1")

    def test_resolves_by_id_across_types(self):
        resolved = self.runner._resolve_connector(self.tool, {"connector_id": "e1"})
        self.assertEqual(resolved["id"], "e1")

    def test_wrong_type_by_id_is_denied(self):
        core = _FakeCore([
            {"id": "s1", "type": "smtp", "name": "SMTP", "enabled": True,
             "status": "connected", "config": {"host": "smtp.exemple.fr"}},
        ])
        runner = SecureToolRunner(core)
        with self.assertRaises(ToolDenied):
            runner._resolve_connector(self.tool, {"connector_id": "s1"})

    def test_no_connector_any_type_is_denied(self):
        runner = SecureToolRunner(_FakeCore([]))
        with self.assertRaises(ToolDenied):
            runner._resolve_connector(self.tool, {})

    def test_single_type_tools_unchanged(self):
        tool = Tool(id="ssh.ls", name="ls", description="d", category="c",
                    handler=_handler, connector_type="ssh")
        core = _FakeCore([
            {"id": "srv", "type": "ssh", "name": "Serveur", "enabled": True,
             "status": "connected", "config": {"host": "x.fr"}},
        ])
        resolved = SecureToolRunner(core)._resolve_connector(tool, {})
        self.assertEqual(resolved["id"], "srv")


if __name__ == "__main__":
    unittest.main()