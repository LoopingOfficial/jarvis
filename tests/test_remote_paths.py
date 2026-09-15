"""Tests de la résolution centrale des chemins distants + build + routage.

Exigences couvertes :
- resolveRemotePath produit /home/brainrotfortnite/public_html/blog.php pour
  « blog.php » avec le vrai working_directory du connecteur SSH.
- Aucun fallback générique (/var/www/html, /var/www, /home/user, server_web).
- Racine manquante -> MissingRemoteWorkingDirectoryError.
- Chemin absolu atterrissant sous une racine interdite -> INVALID_REMOTE_ROOT.
- Traversée hors racine -> rejetée.
- scrub_forbidden_roots nettoie l'historique avant injection au modèle.
- FileCommandRouter capture les sous-dossiers (includes/config.php).
- routing_candidates reste stable même si le connecteur a un statut 'error'.
"""
import tempfile
import unittest
from pathlib import Path

from jarvis.build import JARVIS_BUILD_ID, is_forbidden_remote_default, scrub_forbidden_roots
from jarvis.core import JarvisCore
from jarvis.file_router import _FILE_PATTERN, _LOCAL_HINT
from jarvis.remote_paths import (InvalidRemoteRootError,
                                 MissingRemoteWorkingDirectoryError,
                                 active_remote_root, resolveRemotePath)

CONNECTOR = {
    "id": "ssh",
    "type": "ssh",
    "config": {
        "host": "cpanel.vpcloud.fr",
        "username": "brainrotfortnite",
        "working_directory": "/home/brainrotfortnite/public_html",
        "remote_path": "/home/brainrotfortnite/public_html",
    },
}

RACINE = "/home/brainrotfortnite/public_html"


class TestBuild(unittest.TestCase):
    def test_build_id(self):
        """Le marqueur de build change à chaque correction de fond.

        Figer sa valeur littérale garantissait un échec à chaque incrément —
        ce test vérifie donc le contrat réel : un identifiant non vide,
        exploitable tel quel dans les traces et /api/status.
        """
        self.assertTrue(JARVIS_BUILD_ID)
        self.assertRegex(JARVIS_BUILD_ID, r"^[A-Z0-9][A-Z0-9_]{5,63}$")
        self.assertEqual(JARVIS_BUILD_ID, JARVIS_BUILD_ID.strip())

    def test_forbidden_defaults(self):
        for bad in ("/var/www/html", "/var/www", "/home/user",
                    "/var/www/html/uploads", "/var/www/site"):
            self.assertTrue(is_forbidden_remote_default(bad))
        self.assertFalse(is_forbidden_remote_default(RACINE))
        self.assertFalse(is_forbidden_remote_default("/home/user2/site"))

    def test_scrub(self):
        txt = ("Le fichier est dans /var/www/html/blog.php, la copie "
               "/var/www/site était ancienne et /home/user aussi.")
        out = scrub_forbidden_roots(txt, RACINE)
        self.assertNotIn("/var/www/html", out)
        self.assertNotIn("/var/www/site", out)
        self.assertNotIn("/home/user", out)
        self.assertIn(RACINE, out)


class TestResolve(unittest.TestCase):
    def test_blog_php(self):
        self.assertEqual(resolveRemotePath(CONNECTOR, "blog.php"),
                         f"{RACINE}/blog.php")

    def test_subdirectory(self):
        self.assertEqual(resolveRemotePath(CONNECTOR, "includes/config.php"),
                         f"{RACINE}/includes/config.php")

    def test_absolute_in_root(self):
        self.assertEqual(resolveRemotePath(CONNECTOR, f"{RACINE}/forums.php"),
                         f"{RACINE}/forums.php")

    def test_absolute_forbidden_blocked(self):
        with self.assertRaises(InvalidRemoteRootError) as cm:
            resolveRemotePath(CONNECTOR, "/var/www/html/blog.php")
        self.assertIn("INVALID_REMOTE_ROOT", str(cm.exception))

    def test_absolute_other_root_allowed(self):
        # Chemin absolu valide autre que la racine : autorisé tel quel
        # (le blocage ne concerne que les racines génériques interdites).
        self.assertEqual(resolveRemotePath(CONNECTOR, "/opt/logs/app.log"),
                         "/opt/logs/app.log")

    def test_traversal_blocked(self):
        with self.assertRaises(InvalidRemoteRootError):
            resolveRemotePath(CONNECTOR, "../../etc/passwd")

    def test_missing_working_directory(self):
        with self.assertRaises(MissingRemoteWorkingDirectoryError):
            resolveRemotePath({}, "blog.php")

    def test_active_remote_root(self):
        self.assertEqual(active_remote_root(CONNECTOR), RACINE)
        self.assertEqual(active_remote_root({}), "")

    def test_config_root_forbidden_rejected(self):
        bad = {"config": {"working_directory": "/var/www/html"}}
        with self.assertRaises(InvalidRemoteRootError):
            resolveRemotePath(bad, "blog.php")


class TestRouterPatterns(unittest.TestCase):
    def test_flat_file(self):
        m = _FILE_PATTERN.search("Affiche moi le contenu de blog.php")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "blog.php")

    def test_subdirectory_file(self):
        m = _FILE_PATTERN.search("Affiche moi le contenu de includes/config.php")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "includes/config.php")

    def test_local_hint(self):
        self.assertIsNotNone(_LOCAL_HINT.search("ouvre login.php en local"))
        self.assertIsNone(_LOCAL_HINT.search("ouvre login.php sur le serveur"))


class TestRegistryWithCore(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.core = JarvisCore(db_path=Path(self._tmp.name) / "test.db")
        self.core.connectors.create({
            "type": "ssh", "id": "ssh", "name": "SSH", "enabled": True,
            "permissions": ["read", "write", "execute"],
            "config": {"host": "cpanel.vpcloud.fr", "username": "brainrotfortnite",
                       "working_directory": RACINE},
        })
        self.core.connectors._db.execute(
            "UPDATE connectors SET status='connected' WHERE id=?", ("ssh",))

    def tearDown(self) -> None:
        try:
            self.core.db.close()
        except Exception:
            pass
        try:
            self._tmp.cleanup()
        except Exception:
            pass

    def test_connector_registry_get_exact(self):
        c = self.core.connectors.get("ssh")
        self.assertIsNotNone(c)
        self.assertEqual(c["id"], "ssh")
        self.assertEqual(c["type"], "ssh")
        self.assertEqual(c["config"]["working_directory"], RACINE)

    def test_routing_candidates_connected(self):
        cands = self.core.connectors.routing_candidates("ssh")
        self.assertEqual([c["id"] for c in cands], ["ssh"])

    def test_routing_candidates_survive_error_status(self):
        self.core.connectors._db.execute(
            "UPDATE connectors SET status='error', status_detail='ancien échec' WHERE id=?",
            ("ssh",))
        self.assertEqual(self.core.connectors.active("ssh"), [])
        cands = self.core.connectors.routing_candidates("ssh")
        self.assertEqual([c["id"] for c in cands], ["ssh"])

    def test_llm_context_has_working_directory(self):
        ctx = self.core.connectors.llm_context()
        for c in ctx:
            if c["id"] == "ssh":
                self.assertEqual(c["working_directory"], RACINE)
                self.assertNotIn("username", c)
                break
        else:
            self.fail("ssh manquant dans llm_context")


if __name__ == "__main__":
    unittest.main()