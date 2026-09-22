"""Session navigateur de VELKO : contrat réel, sans simulation.

Ces tests ne lancent pas Chromium : ils vérifient le contrat que l'interface
et l'agent utilisent — vocabulaire d'événements, profil persistant, politique
de lecture seule et absence de repli simulé quand Playwright manque.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from jarvis import browser_manager
from jarvis.tools import browser_tools  # noqa: F401  (enregistre les outils browser.*)
from jarvis.security_analysis import READ_ONLY_ALLOWED_TOOLS, READ_ONLY_DENIED_TOOLS
from jarvis.tools.base import registry


class BrowserToolsContractTest(unittest.TestCase):
    """L'agent doit disposer de toute l'API décrite, et d'elle seule."""

    EXPECTED = {
        "browser.navigate", "browser.click", "browser.type", "browser.scroll",
        "browser.back", "browser.forward", "browser.reload", "browser.wait",
        "browser.read_page", "browser.status", "browser.pause", "browser.close",
    }

    def test_tous_les_outils_existent(self):
        for tool_id in self.EXPECTED:
            self.assertIsNotNone(registry.get(tool_id), f"{tool_id} absent du registre")

    def test_le_developpeur_peut_tester_dans_le_navigateur(self):
        # Le cycle « modifie le bot puis teste-le » exige que l'agent de
        # développement atteigne réellement la session navigateur.
        for tool_id in ("browser.navigate", "browser.read_page", "browser.click"):
            self.assertIn("coding", registry.get(tool_id).agents, tool_id)

    def test_lecture_autorisee_en_mode_lecture_seule(self):
        # Sans cela, « ouvre cette page et dis-moi ce qu'elle affiche » ouvrait
        # la page mais ne pouvait jamais la lire (WRITE_DENIED_READ_ONLY).
        for tool_id in ("browser.navigate", "browser.read_page", "browser.status",
                        "browser.scroll", "browser.reload"):
            self.assertIn(tool_id, READ_ONLY_ALLOWED_TOOLS, tool_id)

    def test_les_ecritures_restent_interdites_en_lecture_seule(self):
        for tool_id in ("browser.click", "browser.type"):
            self.assertNotIn(tool_id, READ_ONLY_ALLOWED_TOOLS, tool_id)
        self.assertIn("fs.write", READ_ONLY_DENIED_TOOLS)


class BrowserSessionTest(unittest.TestCase):
    def setUp(self):
        self.mgr = browser_manager.BrowserManager(core=object())

    def test_profil_persistant(self):
        """La session doit survivre au redémarrage : profil sur disque."""
        self.assertIsInstance(self.mgr._profile_dir, Path)
        self.assertEqual(self.mgr._profile_dir.name, "browser-profile")

    def test_sans_playwright_aucun_repli_simule(self):
        """Playwright absent : erreur honnête, jamais une fausse page."""
        out = self.mgr._run_op("navigate", {"url": "https://example.com"})
        if out.get("ok"):
            self.skipTest("Playwright est installé : ce cas ne s'applique pas.")
        self.assertFalse(out["ok"])
        self.assertIn("playwright", str(out.get("error", "")).lower())

    def test_commande_inconnue_refusee(self):
        out = self.mgr._run_op("mode_demo", {})
        self.assertFalse(out["ok"])

    def test_etat_initial_honnete(self):
        s = self.mgr.status()
        self.assertFalse(s["active"])
        self.assertEqual(s["url"], "")
        self.assertEqual(s["state"], "idle")


class RoutingTest(unittest.TestCase):
    """Une demande de navigation doit atteindre les outils navigateur."""

    def test_navigation_vers_agent_navigateur(self):
        from jarvis.agents import route_request
        for demande in ("Ouvre https://example.com et lis la page",
                        "va sur le site example.com et vérifie le titre",
                        "consulte cette page web"):
            self.assertEqual(route_request(demande)[0], "browser", demande)

    def test_le_developpement_reste_au_coding_agent(self):
        from jarvis.agents import route_request
        for demande in ("Corrige le bot Discord puis teste-le",
                        "analyse le projet et corrige les erreurs"):
            self.assertEqual(route_request(demande)[0], "coding", demande)


if __name__ == "__main__":
    unittest.main()


class WaitUnitTest(unittest.TestCase):
    """L'attente est en millisecondes.

    Elle était passée telle quelle à time.sleep(), qui attend des secondes :
    un « wait 2500 ms » gelait le thread Playwright 41 minutes et bloquait
    toute la session (les lectures suivantes revenaient vides).
    """

    def test_wait_est_en_millisecondes(self):
        import time
        mgr = browser_manager.BrowserManager(core=object())
        started = time.monotonic()
        out = mgr._run_op("wait", {"ms": 300})
        elapsed = time.monotonic() - started
        self.assertTrue(out["ok"])
        self.assertLess(elapsed, 3.0, "wait a dormi en secondes au lieu de millisecondes")
        self.assertGreaterEqual(elapsed, 0.25)

    def test_wait_est_plafonne(self):
        mgr = browser_manager.BrowserManager(core=object())
        out = mgr._run_op("wait", {"ms": 10_000_000})
        self.assertEqual(out["waited"], 15000)


class DiscordWebTest(unittest.TestCase):
    """L'authentification web ne doit jamais être supposée."""

    def test_reperes_de_connexion(self):
        from jarvis.tools.browser_tools import _LOGIN_SIGNS, _APP_SIGNS
        page_connexion = ("Welcome back!\nEmail or Phone Number*\nPassword*\n"
                          "Log In\nNeed an account?")
        self.assertTrue(any(s in page_connexion for s in _LOGIN_SIGNS))
        self.assertFalse(any(s in page_connexion for s in _APP_SIGNS))

    def test_page_vide_nest_pas_une_session_ouverte(self):
        # Une page encore en chargement ne contient aucun repère : elle ne doit
        # surtout pas être lue comme « authentifié ».
        from jarvis.tools.browser_tools import _LOGIN_SIGNS, _APP_SIGNS
        self.assertFalse(any(s in "" for s in _LOGIN_SIGNS))
        self.assertFalse(any(s in "" for s in _APP_SIGNS))
