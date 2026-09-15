"""Tests Idle Learning — identité unique, expertise, migration, sessions.

Couvre les exigences :
- TEST A  migration / déduplication (23+4 -> 27, une seule ligne)
- TEST 2  expertise > 0 cohérente (docs + knowledge + usage + erreurs faibles)
- TEST 3  session terminée -> expertise recalculée immédiatement
- TEST 4  session sans nouvelle connaissance -> expertise inchangée
- TEST 5  taux d'échec élevé -> expertise limitée / pénalisée
- TEST 6  fraîcheur : connaissance vieille -> freshness diminue
- TEST 7  double émission tool.completed -> un seul comptage
- TEST 8  record_tool('Lire un fichier distant') -> clé ssh.read_file
- TEST 9  échecs récurrents -> learning_queue topic auto (error_fix)
- TEST 10 connaissance liée par tools=[tool_id] + validation officielle
"""
import tempfile
import time
import unittest
from pathlib import Path

from jarvis.core import JarvisCore
from jarvis.expertise import compute_expertise
from jarvis.idle_learning import IdleLearningEngine
from jarvis.tool_identity import identity, ensure_identity


class IdleLearningEngineBase(unittest.TestCase):
    def setUp(self) -> None:
        ensure_identity()
        self._tmp = tempfile.TemporaryDirectory()
        self.core = JarvisCore(db_path=Path(self._tmp.name) / "test.db")
        self.engine = self.core.idle_learning

    def tearDown(self) -> None:
        try:
            self.core.db.close()
        except Exception:
            pass
        try:
            self._tmp.cleanup()
        except Exception:
            pass


class TestMigration(IdleLearningEngineBase):
    def test_dedup_merge(self):
        now = time.time()
        rows = [
            ("Lire un fichier distant", 23, 23, 0, now - 4000, now - 10),
            ("ssh.read_file", 4, 4, 0, now - 2000, now),
            ("Lister un dossier distant", 15, 15, 0, now - 3000, now - 8),
            ("ssh.list", 15, 15, 0, now - 1000, now - 5),
            ("Lister les connecteurs", 1, 1, 0, now - 500, now - 2),
            ("connector.list", 1, 1, 0, now - 400, now - 1),
        ]
        for tool_id, calls, ok, fail, first, last in rows:
            self.core.db.execute(
                "INSERT INTO tool_usage(tool_id,total_calls,success_calls,failed_calls,"
                "first_used_at,last_used_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (tool_id, calls, ok, fail, first, last, now))

        result = self.engine.migrate()

        self.assertGreaterEqual(result["removed_duplicates"], 3)

        def row(tool_id):
            return self.core.db.one("SELECT * FROM tool_usage WHERE tool_id=?", (tool_id,))

        self.assertEqual(row("ssh.read_file")["total_calls"], 27)
        self.assertEqual(row("ssh.read_file")["success_calls"], 27)
        self.assertEqual(row("ssh.read_file")["display_name"], "Lire un fichier distant")
        self.assertEqual(row("ssh.list")["total_calls"], 30)
        self.assertEqual(row("connector.list")["total_calls"], 2)

        for alias in ("Lire un fichier distant", "Lister un dossier distant", "Lister les connecteurs"):
            self.assertIsNone(self.core.db.one(
                "SELECT 1 FROM tool_usage WHERE tool_id=?", (alias,)))

    def test_merge_preserves_extrema(self):
        now = time.time()
        self.core.db.execute(
            "INSERT INTO tool_usage(tool_id,total_calls,success_calls,failed_calls,"
            "first_used_at,last_used_at,total_duration_ms,last_failure_at,last_failure_error,"
            "error_categories,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("Lire un fichier distant", 23, 23, 0, now - 4000, now - 10, 23000, None, "",
             '{}', now))
        self.core.db.execute(
            "INSERT INTO tool_usage(tool_id,total_calls,success_calls,failed_calls,"
            "first_used_at,last_used_at,total_duration_ms,last_failure_at,last_failure_error,"
            "error_categories,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("ssh.read_file", 4, 3, 1, now - 2000, now, 5000, now - 50, "permission denied",
             '{"permission denied": 2}', now))
        self.engine.migrate()
        r = self.core.db.one("SELECT * FROM tool_usage WHERE tool_id='ssh.read_file'")
        self.assertEqual(r["total_calls"], 27)
        self.assertEqual(r["success_calls"], 26)
        self.assertEqual(r["failed_calls"], 1)
        self.assertEqual(r["total_duration_ms"], 28000)
        self.assertEqual(r["avg_duration_ms"], 28000 // 27)
        self.assertEqual(r["first_used_at"], now - 4000)   # plus ancien
        self.assertEqual(r["last_used_at"], now)           # plus récent
        self.assertEqual(r["last_failure_at"], now - 50)   # plus récent échec conservé
        self.assertIn("permission denied", self.core.db.scalar(
            "SELECT error_categories FROM tool_usage WHERE tool_id='ssh.read_file'"))


class TestCanonicalRecording(IdleLearningEngineBase):
    def test_record_uses_canonical_id(self):
        self.engine.record_tool("Lire un fichier distant", True, 1200)
        self.engine.record_tool("ssh.read_file", True, 900, "")
        row = self.core.db.one("SELECT * FROM tool_usage WHERE tool_id='ssh.read_file'")
        self.assertIsNotNone(row)
        self.assertEqual(row["total_calls"], 2)
        self.assertEqual(row["display_name"], "Lire un fichier distant")

    def test_unknown_tool_keeps_key(self):
        self.engine.record_tool("outil.futur", True, 10)
        row = self.core.db.one("SELECT * FROM tool_usage WHERE tool_id='outil.futur'")
        self.assertIsNotNone(row)

    def test_double_emission_counted_once(self):
        e = self.engine._event
        e({"type": "tool.started", "data": {"tool_id": "ssh.read_file", "name": "Lire un fichier distant"}})
        # runner
        e({"type": "tool.completed", "data": {"tool_id": "ssh.read_file", "tool": "ssh.read_file",
                                              "name": "Lire un fichier distant",
                                              "preview": "SAME", "duration_ms": 1000}})
        # orchestrateur (même appel, événement dédoublé)
        e({"type": "tool.completed", "data": {"tool_id": "ssh.read_file", "preview": "SAME"}})
        row = self.core.db.one("SELECT * FROM tool_usage WHERE tool_id='ssh.read_file'")
        self.assertEqual(row["total_calls"], 1)


class TestExpertise(IdleLearningEngineBase):
    def test_exp_positive_and_consistent(self):
        now = time.time()
        usage = {"total_calls": 27, "success_calls": 27, "failed_calls": 0,
                 "docs_checked_at": now, "docs_version": "latest", "last_used_at": now,
                 "last_learning_at": now, "coverage_total": 10, "coverage_mastered": 7}
        knowledge = [
            {"kind": "API_REFERENCE", "status": "active", "verification_method": "official_source",
             "confidence_score": 0.85, "validation_count": 2, "updated_at": now},
            {"kind": "HOW_TO", "status": "active", "verification_method": "tested",
             "confidence_score": 0.9, "validation_count": 1, "updated_at": now},
            {"kind": "ERROR_FIX", "status": "active", "verification_method": "official_source",
             "confidence_score": 0.7, "validation_count": 0, "updated_at": now},
        ]
        score, comps = compute_expertise(usage=usage, knowledge=knowledge, now=now)
        self.assertGreater(score, 50)
        self.assertLessEqual(score, 100)
        for name in ("documentation", "knowledge", "usage", "error_recovery",
                     "freshness", "workflows"):
            self.assertIn(name, comps)

    def test_failure_rate_penalizes(self):
        now = time.time()
        good = compute_expertise(usage={"total_calls": 100, "success_calls": 100,
                                        "failed_calls": 0, "last_used_at": now},
                                 knowledge=[], now=now)[0]
        bad = compute_expertise(usage={"total_calls": 100, "success_calls": 50,
                                       "failed_calls": 50, "last_used_at": now},
                                knowledge=[], now=now)[0]
        self.assertGreater(good, bad)
        self.assertLess(bad, 40)  # 50 % d'échecs : jamais un haut score.

    def test_freshness_decays(self):
        now = time.time()
        fresh = compute_expertise(
            usage={"total_calls": 30, "success_calls": 30, "failed_calls": 0,
                   "docs_checked_at": now, "last_used_at": now, "last_learning_at": now},
            knowledge=[{"kind": "API_REFERENCE", "status": "active",
                        "verification_method": "official_source", "confidence_score": 0.9,
                        "validation_count": 1, "updated_at": now}], now=now)[1]["freshness"]["score"]
        stale = compute_expertise(
            usage={"total_calls": 30, "success_calls": 30, "failed_calls": 0,
                   "docs_checked_at": now - 200 * 86400, "last_used_at": now - 200 * 86400,
                   "last_learning_at": now - 200 * 86400},
            knowledge=[{"kind": "API_REFERENCE", "status": "active",
                        "verification_method": "official_source", "confidence_score": 0.9,
                        "validation_count": 1, "updated_at": now - 200 * 86400}], now=now)[1]["freshness"]["score"]
        self.assertGreater(fresh, stale)

    def test_unverified_knowledge_weighs_less(self):
        now = time.time()
        verified = compute_expertise(
            usage={"total_calls": 10, "success_calls": 10, "failed_calls": 0,
                   "last_used_at": now, "docs_checked_at": now},
            knowledge=[{"kind": "API_REFERENCE", "status": "active",
                        "verification_method": "official_source", "confidence_score": 0.9,
                        "validation_count": 1, "updated_at": now}], now=now)[0]
        unverified = compute_expertise(
            usage={"total_calls": 10, "success_calls": 10, "failed_calls": 0,
                   "last_used_at": now, "docs_checked_at": now},
            knowledge=[{"kind": "API_REFERENCE", "status": "active",
                        "verification_method": "", "confidence_score": 0.9,
                        "validation_count": 0, "updated_at": now}], now=now)[0]
        self.assertGreater(verified, unverified)


class TestSessionFlow(IdleLearningEngineBase):
    def _seed_usage(self, tool_id: str, calls: int = 12):
        self.core.db.execute(
            "INSERT INTO tool_usage(tool_id,total_calls,success_calls,failed_calls,"
            "total_duration_ms,first_used_at,last_used_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (tool_id, calls, calls, 0, calls * 1000, time.time() - 1000, time.time(), time.time()))

    def test_session_recomputes_expertise(self):
        self._seed_usage("ssh.read_file", 12)
        self.engine._read_source = lambda url: "Paramiko SFTPClient.open() lit un fichier distant."
        res = self.engine.run_once()
        self.assertTrue(res["ok"])
        self.assertEqual(res["result"]["knowledge_created"], 1)
        self.assertGreater(res["result"]["expertise_after"], res["result"]["expertise_before"])
        # Écrit immédiatement en base.
        sess = self.core.db.one("SELECT * FROM learning_sessions ORDER BY started_at DESC LIMIT 1")
        self.assertEqual(sess["status"], "completed")
        self.assertEqual(sess["progress"], 1.0)
        self.assertGreater(sess["expertise_after"], 0)
        # Connaissance liée par tool_id canonique.
        kb = self.core.db.query("SELECT * FROM knowledge ORDER BY updated_at DESC LIMIT 1")[0]
        self.assertIn("ssh.read_file", self.core.memory.knowledge_get(kb["id"])["tools"])
        self.assertEqual(kb["verification_method"], "official_source")
        self.assertGreater(kb["confidence_score"], 0.5)
        g = self.core.brain.graph()
        ids = {n["id"] for n in g["nodes"]}
        self.assertIn("tool:ssh.read_file", ids)
        self.assertIn(f"kb:{kb['id']}", ids)
        kinds = {(e["source"], e["target"], e["kind"]) for e in g["edges"]}
        self.assertIn(("tool:ssh.read_file", f"kb:{kb['id']}", "teaches"), kinds)

    def test_session_no_new_learning_keeps_expertise(self):
        self._seed_usage("ssh.read_file", 12)
        self.engine._read_source = lambda url: ""
        before = self.core.db.scalar(
            "SELECT expertise_score FROM tool_usage WHERE tool_id='ssh.read_file'")
        res = self.engine.run_once()
        self.assertTrue(res["result"]["no_new_knowledge"])
        self.assertEqual(res["result"]["note"], "No new validated knowledge")
        self.assertEqual(res["result"]["expertise_after"], res["result"]["expertise_before"])
        after = self.core.db.scalar(
            "SELECT expertise_score FROM tool_usage WHERE tool_id='ssh.read_file'")
        self.assertEqual(before, after)

    def test_learning_registered_tool_without_usage_gets_expertise_row(self):
        """Une session documentaire ne doit plus finir avec une expertise fantôme à 0 %."""
        self.engine._read_source = lambda url: "SSH read_file working_directory permissions"
        res = self.engine.run_once()
        self.assertTrue(res["ok"])
        row = self.core.db.one("SELECT * FROM tool_usage WHERE tool_id=?", (res["tool_id"],))
        self.assertIsNotNone(row)
        self.assertGreater(row["expertise_score"], 0)
        self.assertGreater(res["result"]["expertise_after"], 0)

    def test_session_score_includes_freshness_metadata(self):
        self._seed_usage("ssh.read_file", 12)
        self.engine._read_source = lambda url: "Paramiko working_directory permissions"
        res = self.engine.run_once()
        components = self.core.db.scalar(
            "SELECT expertise_components FROM tool_usage WHERE tool_id='ssh.read_file'")
        self.assertIn('"freshness"', components)
        self.assertGreater(res["result"]["expertise_after"], res["result"]["expertise_before"])


class TestCoverage(IdleLearningEngineBase):
    """Critère 26/27 — la couverture doit être atteignable et exigeante."""

    DOCS = ("SFTPClient lit un fichier distant (cat). Le connector resolve la cible. "
            "Permission denied si chmod. Gros fichier : head -c. Encoding utf-8. "
            "Timeout si délai dépassé. Erreur not found. Security : injection scrub.")

    def _seed(self, calls=12, fails=0):
        self.core.db.execute(
            "INSERT INTO tool_usage(tool_id,total_calls,success_calls,failed_calls,"
            "total_duration_ms,first_used_at,last_used_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            ("ssh.read_file", calls, calls - fails, fails, calls * 1000,
             time.time() - 1000, time.time(), time.time()))

    def test_documented_topics_become_mastered_with_real_usage(self):
        """Doc validée + knowledge + usage réel réussi -> sujets maîtrisés (>0)."""
        self._seed()
        self.engine._read_source = lambda url: self.DOCS
        self.engine.run_once()
        row = self.core.db.one(
            "SELECT * FROM tool_usage WHERE tool_id='ssh.read_file'")
        self.assertGreater(row["coverage_mastered"], 0)
        self.assertEqual(row["coverage_total"], 10)
        self.assertGreater(row["expertise_score"], 0)

    def test_testable_topics_need_a_real_test(self):
        """La doc seule ne maîtrise jamais un sujet couvert par un auto-test."""
        self._seed()
        self.engine._read_source = lambda url: self.DOCS + " working_directory relative path"
        self.engine.run_once()
        _, _, mastered = self.engine._coverage(
            "ssh.read_file",
            dict(self.core.db.one("SELECT * FROM tool_usage WHERE tool_id='ssh.read_file'")),
            self.engine._knowledge_for("ssh.read_file"))
        for topic in ("working_directory", "chemins relatifs", "sécurité"):
            self.assertNotIn(topic, mastered)

    def test_no_real_usage_blocks_mastery(self):
        """Sans usage réel réussi, la documentation ne suffit pas."""
        self._seed(calls=12, fails=11)  # taux de succès trop faible
        self.engine._read_source = lambda url: self.DOCS
        self.engine.run_once()
        row = self.core.db.one("SELECT * FROM tool_usage WHERE tool_id='ssh.read_file'")
        self.assertEqual(row["coverage_mastered"], 0)


class TestLearningSources(IdleLearningEngineBase):
    def test_source_is_recorded(self):
        """Régression : l'INSERT learning_sources avait 10 place-holders pour
        9 colonnes -> toute session documentaire levait et laissait 0 %."""
        self.core.db.execute(
            "INSERT INTO tool_usage(tool_id,total_calls,success_calls,updated_at) "
            "VALUES(?,?,?,?)", ("ssh.read_file", 12, 12, time.time()))
        self.engine._read_source = lambda url: "Paramiko SFTPClient.open()"
        res = self.engine.run_once()
        self.assertTrue(res["ok"], res)
        row = self.core.db.one("SELECT * FROM learning_sources WHERE session_id=?",
                               (res["session_id"],))
        self.assertIsNotNone(row)
        self.assertEqual(row["tool_id"], "ssh.read_file")
        self.assertEqual(row["status"], "read")
        self.assertTrue(row["content_hash"])


class TestErrorTopics(IdleLearningEngineBase):
    def test_repeated_failures_create_queue_topic(self):
        for i in range(3):
            self.engine.record_tool("ssh.read_file", False, 900,
                                    "permission denied (publickey)")
        q = self.core.db.one(
            "SELECT * FROM learning_queue WHERE kind='error_fix' AND status='queued'")
        self.assertIsNotNone(q)
        self.assertEqual(q["tool_id"], "ssh.read_file")
        self.assertIn("permission denied", q["topic"])
        # Pas de doublon si on rejoue.
        self.engine.record_tool("ssh.read_file", False, 900, "permission denied")
        count = self.core.db.scalar(
            "SELECT COUNT(*) FROM learning_queue WHERE kind='error_fix' AND status='queued'")
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
