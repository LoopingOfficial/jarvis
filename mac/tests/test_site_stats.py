"""Statistiques d'inscription : calcul, honnêteté des taux, choix du tunnel SSH.

Aucun test ne touche le serveur : `_query` est remplacé par des lignes figées,
telles que `mysql --batch` les renvoie (en-tête puis valeurs séparées par des
tabulations).

Le point réellement défendu ici est le double taux. La vérification d'e-mail
étant récente, le taux brut donne « 2,9 % » et ferait conclure à tort à une
panne d'envoi ; c'est le taux depuis l'activation qui décrit la réalité.
"""
import unittest
from unittest import mock

from jarvis import site_stats


def _rows(**values):
    return [dict(values)]


USERS = _rows(total="315", confirmes="9", non_confirmes="306", premium="3", admins="1",
              nouveaux_7j="5", nouveaux_30j="21", depuis_verif="7",
              depuis_verif_confirmes="6", anterieurs="308",
              premier_compte="2026-04-18 07:32:59", dernier_compte="2026-09-15 00:40:21")
LIENS = _rows(emis="203", utilises="88", actifs="0", expires="115")


def _fake_query(_core, sql, **_kw):
    return LIENS if "user_email_verifications" in sql else USERS


class CollectTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(site_stats, "_query", _fake_query)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.stats = site_stats.collect(object())

    def test_chiffres_bruts(self):
        self.assertEqual(self.stats["total"], 315)
        self.assertEqual(self.stats["confirmes"], 9)
        self.assertEqual(self.stats["non_confirmes"], 306)
        self.assertEqual(self.stats["nouveaux_7j"], 5)
        self.assertEqual(self.stats["liens_emis"], 203)

    def test_les_deux_taux_sont_calcules(self):
        """Le taux brut seul serait trompeur : les deux doivent être présents."""
        self.assertEqual(self.stats["taux_brut"], 2.9)
        self.assertEqual(self.stats["taux_depuis_activation"], 85.7)

    def test_le_resume_cite_la_date_de_bascule(self):
        texte = site_stats.summary(self.stats)
        self.assertIn(site_stats.VERIFICATION_LIVE_SINCE, texte)
        self.assertIn("85.7", texte)

    def test_description_discord_explique_l_ecart(self):
        texte = site_stats.discord_description(self.stats)
        self.assertIn("jamais eu", texte)
        self.assertIn("85.7", texte)

    def test_champs_discord_complets(self):
        noms = [f["name"] for f in site_stats.discord_fields(self.stats)]
        self.assertEqual(len(noms), 9)
        self.assertTrue(any("Total" in n for n in noms))

    def test_base_vide_ne_divise_pas_par_zero(self):
        vide = _rows(total="0", confirmes="0", non_confirmes="0", premium="0", admins="0",
                     nouveaux_7j="0", nouveaux_30j="0", depuis_verif="0",
                     depuis_verif_confirmes="0", anterieurs="0",
                     premier_compte="NULL", dernier_compte="NULL")
        with mock.patch.object(site_stats, "_query",
                               lambda _c, sql, **_k: [] if "verifications" in sql else vide):
            stats = site_stats.collect(object())
        self.assertEqual(stats["taux_brut"], 0.0)
        self.assertEqual(stats["taux_depuis_activation"], 0.0)

    def test_table_absente_leve_une_erreur_explicite(self):
        """Pas de chiffres plutôt que des zéros présentés comme une vérité."""
        with mock.patch.object(site_stats, "_query", lambda *_a, **_k: []):
            with self.assertRaises(site_stats.SiteStatsError):
                site_stats.collect(object())


class _Connectors:
    """Registre de connecteurs factice, au format de `core.connectors`."""

    def __init__(self, items):
        self._items = items

    def list(self):
        return self._items

    def find(self, query, ctype=""):
        for item in self._items:
            if item["id"] == query and (not ctype or item["type"] == ctype):
                return item
        return None


def _core(items):
    return type("Core", (), {"connectors": _Connectors(items)})()


MYSQL = {"id": "mysql-mariadb", "type": "mysql", "enabled": True,
         "config": {"host": "brainrot-fortnite.com", "username": "brainrotfortnite_com",
                    "database": "brainrotfortnite_com"}}


class TunnelTest(unittest.TestCase):
    def test_via_ssh_declare_gagne(self):
        mysql = {**MYSQL, "config": {**MYSQL["config"], "via_ssh": "prod"}}
        core = _core([mysql,
                      {"id": "prod", "type": "ssh", "enabled": True, "config": {"username": "x"}},
                      {"id": "autre", "type": "ssh", "enabled": True, "config": {"username": "y"}}])
        self.assertEqual(site_stats._ssh_connector(core, mysql)["id"], "prod")

    def test_via_ssh_declare_mais_absent(self):
        mysql = {**MYSQL, "config": {**MYSQL["config"], "via_ssh": "fantome"}}
        core = _core([mysql])
        with self.assertRaises(site_stats.SiteStatsError):
            site_stats._ssh_connector(core, mysql)

    def test_repli_sur_le_meme_hote(self):
        core = _core([MYSQL,
                      {"id": "ssh", "type": "ssh", "enabled": True,
                       "config": {"host": "brainrot-fortnite.com", "username": "peu-importe"}},
                      {"id": "autre", "type": "ssh", "enabled": True, "config": {"host": "ailleurs"}}])
        self.assertEqual(site_stats._ssh_connector(core, MYSQL)["id"], "ssh")

    def test_repli_sur_le_compte_cpanel(self):
        """`brainrotfortnite_com` appartient au compte `brainrotfortnite`."""
        core = _core([MYSQL,
                      {"id": "ssh", "type": "ssh", "enabled": True,
                       "config": {"host": "cpanel.example", "username": "brainrotfortnite"}},
                      {"id": "test", "type": "ssh", "enabled": True,
                       "config": {"host": "autre.example", "username": "sansrapport"}}])
        self.assertEqual(site_stats._ssh_connector(core, MYSQL)["id"], "ssh")

    def test_ambiguite_refusee(self):
        """Deux serveurs plausibles : on demande une configuration explicite."""
        core = _core([MYSQL,
                      {"id": "a", "type": "ssh", "enabled": True, "config": {"username": "nope"}},
                      {"id": "b", "type": "ssh", "enabled": True, "config": {"username": "nada"}}])
        with self.assertRaises(site_stats.SiteStatsError) as err:
            site_stats._ssh_connector(core, MYSQL)
        self.assertIn("Via connecteur SSH", str(err.exception))

    def test_aucun_connecteur_mysql(self):
        with self.assertRaises(site_stats.SiteStatsError):
            site_stats._mysql_connector(_core([]))


class _Vault:
    def get(self, _cid, field, default=""):
        return {"password": "s3cr3t"}.get(field, default)

    def scrub(self, text):
        return str(text).replace("s3cr3t", "[masqué]")


class QueryTest(unittest.TestCase):
    """Construction de la commande distante et lecture de la sortie."""

    def setUp(self):
        self.captured = {}

        def fake_ssh(config, secrets, command, timeout=60):
            self.captured["command"] = command
            return True, self.response

        patcher = mock.patch.object(site_stats, "ssh_exec", fake_ssh)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.response = "a\tb\n1\t2"
        self.core = type("Core", (), {
            "connectors": _Connectors([MYSQL, {"id": "ssh", "type": "ssh", "enabled": True,
                                               "config": {"host": "brainrot-fortnite.com"}}]),
            "vault": _Vault()})()

    def test_mot_de_passe_hors_ligne_de_commande(self):
        """Un `-p<motdepasse>` serait lisible par tout le serveur dans `ps`."""
        site_stats._query(self.core, "SELECT 1")
        command = self.captured["command"]
        self.assertIn("MYSQL_PWD=", command)
        self.assertNotIn("-ps3cr3t", command)
        self.assertNotIn("-p 's3cr3t'", command)

    def test_lignes_nommees(self):
        self.assertEqual(site_stats._query(self.core, "SELECT 1"), [{"a": "1", "b": "2"}])

    def test_erreur_mysql_remontee(self):
        self.response = "ERROR 1045 (28000): Access denied"
        with self.assertRaises(site_stats.SiteStatsError):
            site_stats._query(self.core, "SELECT 1")

    def test_sortie_parasite_refusee(self):
        """Une ligne avant l'en-tête décalerait toutes les colonnes."""
        self.response = "avertissement quelconque"
        with self.assertRaises(site_stats.SiteStatsError):
            site_stats._query(self.core, "SELECT 1")


class BannerCleanupTest(unittest.TestCase):
    """Les bannieres publiees chaque semaine ne doivent pas s'accumuler."""

    def test_les_anciennes_bannieres_sont_purgees(self):
        import os
        import tempfile
        from pathlib import Path

        from jarvis.tools import site_stats_tools

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(site_stats_tools.tempfile, "gettempdir", lambda: tmp):
                directory = Path(tmp) / "jarvis-site-stats"
                directory.mkdir()
                vieille = directory / "inscriptions-20200101-000000.gif"
                recente = directory / "inscriptions-20991231-235959.gif"
                for f in (vieille, recente):
                    f.write_bytes(b"GIF")
                old = site_stats_tools.BANNER_RETENTION_S + 60
                os.utime(vieille, (0, __import__("time").time() - old))

                site_stats_tools._banner_path()
                self.assertFalse(vieille.exists())
                self.assertTrue(recente.exists())


class BannerTest(unittest.TestCase):
    def test_banniere_generee_si_pillow_disponible(self):
        from jarvis import site_stats_banner
        if not site_stats_banner.HAVE_PIL:
            self.skipTest("Pillow absent")
        import tempfile
        from pathlib import Path
        with mock.patch.object(site_stats, "_query", _fake_query):
            stats = site_stats.collect(object())
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "b.gif"
            site_stats_banner.build(stats, out)
            self.assertTrue(out.is_file())
            # Le plafond de piece jointe Discord est de 8 Mo.
            self.assertLess(out.stat().st_size, 8 * 1024 * 1024)
            from PIL import Image
            with Image.open(out) as img:
                self.assertTrue(getattr(img, "is_animated", False))


if __name__ == "__main__":
    unittest.main()
