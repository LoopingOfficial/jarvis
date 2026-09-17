"""Résolution d'un salon Discord par son nom, et garde-fous de la publication.

Le modèle reçoit « publie dans #commandes-staff », jamais un identifiant
numérique. Tant que l'outil exigeait un identifiant, il n'était jamais appelé et
JARVIS se contentait d'annoncer un envoi qui n'avait pas eu lieu. Ces tests
verrouillent la résolution par nom et les messages d'erreur explicites.

Aucun test ne contacte Discord : le bot est remplacé par un double minimal.
"""
import asyncio
import unittest

from jarvis.discord_engine import DiscordEngine


class _Channel:
    def __init__(self, cid, name):
        self.id, self.name, self.guild = cid, name, None

    def permissions_for(self, _member):
        return type("P", (), {"send_messages": True})()

    def __str__(self):
        return self.name


class _Guild:
    name = "Serveur de test"

    def __init__(self, channels):
        self.text_channels = channels
        self.me = object()
        for channel in channels:
            channel.guild = self


def _engine():
    channels = [_Channel(111, "🤖｜commandes-staff"),
                _Channel(222, "général"),
                _Channel(333, "logs-admin")]
    guild = _Guild(channels)
    engine = DiscordEngine(object())
    engine._bot = type("Bot", (), {
        "guilds": [guild],
        "get_channel": staticmethod(
            lambda cid: next((c for c in channels if c.id == int(cid)), None)),
    })()
    return engine


class ChannelResolutionTest(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()

    def test_nom_simple_ignore_emoji_et_decorations(self):
        """« commandes-staff » doit atteindre « 🤖｜commandes-staff »."""
        for ref in ("commandes-staff", "#commandes-staff", "COMMANDES STAFF",
                    "🤖｜commandes-staff", "commandes"):
            with self.subTest(ref=ref):
                self.assertEqual(self.engine._channel(ref).id, 111)

    def test_identifiant_et_mention(self):
        self.assertEqual(self.engine._channel("111").id, 111)
        self.assertEqual(self.engine._channel("<#111>").id, 111)

    def test_salon_inconnu_liste_les_salons_disponibles(self):
        """L'erreur doit être actionnable : le modèle doit pouvoir se corriger."""
        with self.assertRaises(ValueError) as err:
            self.engine._channel("salon-inexistant")
        self.assertIn("logs-admin", str(err.exception))

    def test_nom_ambigu_refuse_plutot_que_de_deviner(self):
        with self.assertRaises(ValueError) as err:
            self.engine._channel("s")
        self.assertIn("Plusieurs salons", str(err.exception))

    def test_identifiant_numerique_inconnu(self):
        with self.assertRaises(ValueError):
            self.engine._channel("999")

    def test_salon_vide(self):
        with self.assertRaises(ValueError):
            self.engine._channel("")

    def test_list_channels_filtre_et_expose_les_identifiants(self):
        data = asyncio.run(self.engine.list_channels("staff"))
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["channels"][0]["id"], "111")
        self.assertTrue(data["channels"][0]["can_send"])


class ToolArgumentsTest(unittest.TestCase):
    """Le modèle nomme l'argument de façons variées : toutes doivent passer."""

    def test_alias_du_salon(self):
        from jarvis.tools.discord_tools import _channel_ref
        for key in ("channel", "channel_id", "channel_name", "salon"):
            with self.subTest(key=key):
                self.assertEqual(_channel_ref({key: " #staff "}), "#staff")
        self.assertEqual(_channel_ref({}), "")
        self.assertEqual(_channel_ref({"channel": ""}), "")


if __name__ == "__main__":
    unittest.main()
