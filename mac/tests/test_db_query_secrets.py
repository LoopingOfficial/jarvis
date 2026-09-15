"""`db.query` ne doit jamais placer un mot de passe sur une ligne de commande.

Pourquoi ce test existe
-----------------------
Sur la branche SSH, la commande construite ici est exécutée telle quelle sur le
serveur distant. Une ligne de commande y est lisible par TOUS les comptes de la
machine (`ps aux`), ce qui, sur un hébergement mutualisé, expose le mot de passe
de la base à des tiers pendant la durée de la requête. Le secret doit donc
transiter par l'environnement du processus : `MYSQL_PWD` pour MySQL,
`PGPASSWORD` pour PostgreSQL.

Cette protection est invisible à l'usage — la requête fonctionne exactement
pareil dans les deux cas — donc rien ne la rappellerait lors d'une réécriture.
D'où ce verrou.
"""
import unittest
from unittest import mock

from jarvis.tools import remote_tools
from jarvis.tools.base import ToolContext, registry

SECRET = "MotDePasseTresSecret42"


class _Vault:
    def get(self, _cid, field, default=""):
        return SECRET if field == "password" else default

    def scrub(self, text):
        return str(text).replace(SECRET, "[masqué]")


class _Connectors:
    def __init__(self, ssh):
        self._ssh = ssh

    def find(self, query, ctype=""):
        if query == "ssh-prod" and ctype in ("", "ssh"):
            return self._ssh
        return None


def _context(ctype="mysql", via_ssh="ssh-prod"):
    connector = {"id": "db", "type": ctype, "name": "Base",
                 "config": {"host": "db.example", "port": 3306, "username": "compte_db",
                            "database": "compte_db", "via_ssh": via_ssh}}
    ssh = {"id": "ssh-prod", "type": "ssh", "config": {"host": "srv.example", "username": "compte"}}
    core = type("Core", (), {"connectors": _Connectors(ssh), "vault": _Vault()})()
    return ToolContext(core=core, agent="jarvis", connector=connector,
                       arguments={"query": "SELECT COUNT(*) FROM users"})


class DbQuerySecretTest(unittest.TestCase):
    def setUp(self):
        self.commands = []

        def fake_ssh(_config, _secrets, command, timeout=120):
            self.commands.append(command)
            return True, "COUNT(*)\n315"

        patcher = mock.patch.object(remote_tools, "ssh_exec", fake_ssh)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tool = registry.get("db.query")

    def _run(self, **kwargs):
        result = self.tool.handler(_context(**kwargs))
        self.assertTrue(result.ok, result.output)
        return self.commands[-1]

    def test_mysql_passe_par_l_environnement(self):
        command = self._run()
        self.assertIn(f"MYSQL_PWD='{SECRET}'", command)
        self.assertNotIn(f"-p{SECRET}", command)
        self.assertNotIn(f"-p'{SECRET}'", command)

    def test_postgres_passe_par_l_environnement(self):
        command = self._run(ctype="postgres")
        self.assertIn(f"PGPASSWORD='{SECRET}'", command)

    def test_la_requete_reste_fonctionnelle(self):
        """La protection ne doit rien changer au comportement observable."""
        result = self.tool.handler(_context())
        self.assertTrue(result.ok)
        self.assertIn("315", result.output)

    def test_le_secret_ne_ressort_jamais_dans_la_sortie(self):
        with mock.patch.object(remote_tools, "ssh_exec",
                               lambda *_a, **_k: (False, f"ERROR using {SECRET}")):
            result = self.tool.handler(_context())
        self.assertNotIn(SECRET, result.output)
        self.assertIn("[masqué]", result.output)

    def test_tunnel_introuvable_refuse_proprement(self):
        result = self.tool.handler(_context(via_ssh="inexistant"))
        self.assertFalse(result.ok)
        self.assertNotIn(SECRET, result.output)


if __name__ == "__main__":
    unittest.main()
