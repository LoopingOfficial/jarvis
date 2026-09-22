"""Vue lecture seule des moniteurs de VELKO.

Ce que ces tests protègent : les écrans ne montrent QUE des faits réels, et la
politique de sécurité des outils s'applique aussi à l'affichage.
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from jarvis import workspace_view as w


class _Settings:
    def __init__(self, roots): self._roots = roots
    def get(self, *args, **kwargs): return self._roots


class _Core:
    def __init__(self, roots): self.settings = _Settings(roots)


class WorkspaceViewTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.core = _Core([str(self.root)])
        self.project = self.root / "projet"
        self.project.mkdir()
        (self.project / "calc.py").write_text('"""Doc."""\n\n\ndef add(a, b):\n    return a - b\n')
        (self.project / "test_calc.py").write_text("import unittest\n")
        (self.project / "requirements.txt").write_text("")

    def tearDown(self):
        self._tmp.cleanup()

    # -- contenu réel ------------------------------------------------------
    def test_lit_le_vrai_fichier(self):
        d = w.read_file(self.core, str(self.project / "calc.py"))
        self.assertTrue(d["ok"])
        self.assertIn("return a - b", d["content"])
        self.assertEqual(d["language"], "python")
        self.assertEqual(d["project_name"], "projet")

    def test_arborescence_reelle(self):
        t = w.tree(self.core, str(self.project / "calc.py"))
        self.assertTrue(t["ok"])
        self.assertIn("test_calc.py", [e["name"] for e in t["entries"]])

    # -- refus honnêtes ----------------------------------------------------
    def test_refuse_hors_racines(self):
        d = w.read_file(self.core, "/etc/hosts")
        self.assertFalse(d["ok"])
        self.assertIn("hors des dossiers autorisés", d["error"])

    def test_refuse_chemin_relatif(self):
        self.assertFalse(w.read_file(self.core, "calc.py")["ok"])

    def test_signale_fichier_absent(self):
        d = w.read_file(self.core, str(self.project / "absent.py"))
        self.assertFalse(d["ok"])
        self.assertIn("introuvable", d["error"])

    # -- diff réel ---------------------------------------------------------
    def test_diff_vient_de_git(self):
        run = lambda *a: subprocess.run(a, cwd=str(self.project), capture_output=True, check=True)
        run("git", "init", "-q")
        run("git", "add", "-A")
        run("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "initial")
        (self.project / "calc.py").write_text('"""Doc."""\n\n\ndef add(a, b):\n    return a + b\n')
        d = w.git_diff(self.core, str(self.project / "calc.py"))
        self.assertTrue(d["ok"])
        self.assertIn("+    return a + b", d["diff"])
        self.assertIn("-    return a - b", d["diff"])

    def test_hors_depot_le_dit(self):
        d = w.git_diff(self.core, str(self.project / "calc.py"))
        self.assertTrue(d["ok"])
        self.assertEqual(d["diff"], "")
        self.assertIn("pas un dépôt git", d["detail"])

    # -- masquage d'affichage ---------------------------------------------
    def test_masque_les_secrets(self):
        for secret in ['DB_PASSWORD=hunter2000', 'API_KEY = "abcd1234efgh5678"',
                       '"bot_token": "abcdefgh12345678"', 'Authorization: Bearer abcdefghij',
                       'sk-abcdefghijklmnopqrst']:
            self.assertNotIn("hunter2000", w.redact(secret))
            self.assertIn(w.MASK, w.redact(secret), secret)

    def test_ne_deforme_pas_le_code(self):
        code = "def add(a, b):\n    return a + b\nconst url = 'https://x.test/a?b=1'\n"
        self.assertEqual(w.redact(code), code)

    def test_le_fichier_sur_disque_nest_pas_modifie(self):
        cible = self.project / "conf.env"
        contenu = "DB_PASSWORD=hunter2000\n"
        cible.write_text(contenu)
        affiche = w.read_file(self.core, str(cible))
        self.assertNotIn("hunter2000", affiche["content"])   # masqué à l'écran
        self.assertEqual(cible.read_text(), contenu)         # intact sur disque


if __name__ == "__main__":
    unittest.main()
