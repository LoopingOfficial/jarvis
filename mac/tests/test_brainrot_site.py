"""brainrot-fortnite.com — analytics, propositions et garde-fous.

Ces tests n'appellent pas la vraie base : ils remplacent l'exécuteur de
requêtes par un double, ce qui permet de vérifier précisément ce qui compte —
qu'une donnée absente est DÉCLARÉE absente, qu'une panne n'est pas confondue
avec un schéma incomplet, et qu'aucune proposition n'apparaît sans le fait
chiffré qui la motive.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from jarvis.brainrot_site import (AnalyticsRepository, Metric, UNAVAILABLE,
                                  VelkoOpportunityEngine)


class _Result:
    def __init__(self, ok: bool, output: str) -> None:
        self.ok, self.output = ok, output


class _Runner:
    """Double de l'exécuteur : rend des réponses tabulées comme le connecteur."""

    def __init__(self, responses: dict[str, str], fail: bool = False) -> None:
        self.responses, self.fail, self.seen = responses, fail, []

    def run(self, tool_id, arguments, **kwargs):
        sql = arguments.get("query", "")
        self.seen.append(sql)
        if self.fail:
            return _Result(False, "Connexion MySQL impossible : (2003)")
        for needle, output in self.responses.items():
            if needle in sql:
                return _Result(True, output)
        return _Result(True, "n\n0")


class _Core:
    def __init__(self, runner): self.runner = runner


COLUMNS_USERS = "column_name\nid\nemail\ncreated_at\nemail_verified_at"
COLUMNS_PRESENCE = "column_name\nuser_id\nlast_seen_at"
COLUMNS_BLOG = "column_name\nid\ntitle\nstatus\npublished_at"


def _repo(responses, fail=False):
    repo = AnalyticsRepository(_Core(_Runner(responses, fail)))
    repo.profile = {"database": {"schema": "test"}, "unavailable_metrics": {}}
    return repo


class ReadOnlyTest(unittest.TestCase):
    """Le dépôt d'analytics ne doit jamais pouvoir écrire."""

    def test_select_accepte(self):
        repo = _repo({"SELECT 1": "n\n1"})
        ok, rows, err = repo.query("SELECT 1 AS n")
        self.assertTrue(ok, err)

    def test_ecritures_refusees(self):
        repo = _repo({})
        for sql in ("UPDATE users SET is_admin = 1",
                    "DELETE FROM users",
                    "DROP TABLE users",
                    "INSERT INTO users VALUES (1)",
                    "SELECT 1; DROP TABLE users"):
            ok, _, err = repo.query(sql)
            self.assertFalse(ok, sql)
            self.assertIn("lecture seule", err)
        self.assertEqual(repo.core.runner.seen, [], "aucune requête ne doit atteindre la base")


class DisponibiliteTest(unittest.TestCase):
    """Une donnée manquante est dite manquante — jamais estimée."""

    def test_colonne_absente_est_declaree(self):
        repo = _repo({"information_schema": "column_name\nid\nemail"})   # pas de created_at
        snap = repo.snapshot()
        membres = next(m for m in snap["metrics"] if m["key"] == "membres")
        self.assertFalse(membres["available"])
        self.assertIn("created_at", membres["reason"])

    def test_panne_nest_pas_un_schema_incomplet(self):
        """Le piège rencontré en production : une coupure faisait conclure
        « colonne absente » alors que les données existent."""
        repo = _repo({}, fail=True)
        snap = repo.snapshot()
        membres = next(m for m in snap["metrics"] if m["key"] == "membres")
        self.assertFalse(membres["available"])
        self.assertIn("injoignable", membres["reason"])
        self.assertTrue(repo.db_error)

    def test_connexions_toujours_declarees_indisponibles(self):
        repo = _repo({"information_schema": COLUMNS_USERS, "COUNT(*)": "n\n317"})
        snap = repo.snapshot()
        conn = next(m for m in snap["metrics"] if m["key"] == "connexions_jour")
        self.assertFalse(conn["available"])
        self.assertIsNone(conn["value"])

    def test_metrique_absente_ne_rend_aucun_nombre(self):
        m = Metric("x", "Test", available=False, reason="")
        self.assertIn(UNAVAILABLE, m.render())
        self.assertIsNone(m.value)


class ComparaisonTest(unittest.TestCase):
    def test_evolution_calculee_uniquement_si_les_deux_periodes_existent(self):
        self.assertEqual(Metric("a", "A", 12, True, "", 8).change_pct, 50.0)
        self.assertIsNone(Metric("a", "A", 12, True, "", None).change_pct)
        self.assertIsNone(Metric("a", "A", 12, True, "", 0).change_pct)
        self.assertIsNone(Metric("a", "A", None, False, "absente", 8).change_pct)

    def test_fenetres_en_heure_de_paris(self):
        repo = _repo({})
        w = repo.windows()
        for key in ("today", "yesterday", "d7", "d7_prev", "d30", "d30_prev"):
            self.assertIn(key, w)
        self.assertLess(w["yesterday"][1], w["today"][1])
        self.assertLess(w["d7_prev"][0], w["d7"][0])


class OpportunitesTest(unittest.TestCase):
    """Aucune proposition sans le fait chiffré qui la motive."""

    @staticmethod
    def _snapshot(**overrides):
        base = {
            "metrics": [
                {"key": "membres", "label": "Membres", "value": 317, "available": True,
                 "reason": "", "previous": None, "change_pct": None},
                {"key": "emails_non_confirmes", "label": "Emails non confirmés", "value": 307,
                 "available": True, "reason": "", "previous": None, "change_pct": None},
                {"key": "inscriptions_7j", "label": "Inscriptions 7 jours", "value": 3,
                 "available": True, "reason": "", "previous": 4, "change_pct": -25.0},
                {"key": "actifs_7j", "label": "Actifs 7 jours", "value": 4,
                 "available": True, "reason": "", "previous": None, "change_pct": None},
            ],
            "rendered": [], "last_published_days": 122,
            "generated_at": "2026-09-21T23:00:00", "timezone": "Europe/Paris",
        }
        base.update(overrides)
        return base

    def _engine(self):
        return VelkoOpportunityEngine(_repo({}))

    def test_propositions_fondees_sur_les_donnees(self):
        found = {o.key for o in self._engine().detect(self._snapshot())}
        self.assertIn("relance_emails", found)
        self.assertIn("baisse_inscriptions", found)
        self.assertIn("blog_silencieux", found)
        self.assertIn("faible_activite", found)

    def test_chaque_proposition_porte_une_action_executable(self):
        for opportunity in self._engine().detect(self._snapshot()):
            self.assertTrue(opportunity.tool, opportunity.key)
            self.assertTrue(opportunity.action_label, opportunity.key)
            self.assertTrue(opportunity.observation, opportunity.key)
            self.assertTrue(opportunity.why, opportunity.key)

    def test_le_chiffre_de_la_proposition_vient_des_donnees(self):
        relance = next(o for o in self._engine().detect(self._snapshot())
                       if o.key == "relance_emails")
        self.assertEqual(relance.count, 307)
        self.assertIn("307", relance.observation)

    def test_aucune_proposition_sans_donnee(self):
        vide = self._snapshot(metrics=[
            {"key": "membres", "label": "Membres", "value": None, "available": False,
             "reason": "base injoignable", "previous": None, "change_pct": None}],
            last_published_days=None)
        self.assertEqual(self._engine().detect(vide), [])

    def test_pas_de_relance_si_tout_est_confirme(self):
        propre = self._snapshot(metrics=[
            {"key": "membres", "label": "Membres", "value": 317, "available": True,
             "reason": "", "previous": None, "change_pct": None},
            {"key": "emails_non_confirmes", "label": "Emails non confirmés", "value": 0,
             "available": True, "reason": "", "previous": None, "change_pct": None}],
            last_published_days=1)
        self.assertEqual([o.key for o in self._engine().detect(propre)], [])

    def test_briefing_annonce_une_panne_au_lieu_de_lister_des_absences(self):
        repo = _repo({}, fail=True)
        text = VelkoOpportunityEngine(repo).briefing()["text"]
        self.assertIn("BASE DE DONNÉES INJOIGNABLE", text)


class PolitiqueTest(unittest.TestCase):
    """La politique d'autonomie doit rester explicite dans le profil."""

    def test_actions_sensibles_exigent_une_confirmation(self):
        from jarvis.brainrot_site import load_profile
        policy = (load_profile().get("autonomy_policy") or {})
        confirm = " ".join(policy.get("requires_confirmation") or [])
        for action in ("blog.publish", "email.send_campaign", "sync_sheet", "site.deploy"):
            self.assertIn(action, confirm, action)

    def test_les_lectures_sont_autonomes(self):
        from jarvis.brainrot_site import load_profile
        policy = (load_profile().get("autonomy_policy") or {})
        autonomous = " ".join(policy.get("autonomous") or [])
        for action in ("fs.read", "git.status", "brainrot.analytics", "browser.read_page"):
            self.assertIn(action, autonomous, action)


if __name__ == "__main__":
    unittest.main()
