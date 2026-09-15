"""Appel vocal matinal : interprétation des réponses, collecte, planification.

Aucun test ne touche Discord ni le réseau. Les trois parties vérifiables sans
bot le sont ici : la compréhension de ce qui est dit, la fidélité du rapport à
l'état réel du système, et le calcul de l'échéance quotidienne.
"""
import time
import unittest
import wave
from io import BytesIO

from jarvis.discord_call import (CHANNELS, SAMPLE_RATE, SAMPLE_WIDTH, MorningCallManager,
                                 MorningReportCollector, interpret_answer, pcm_to_wav,
                                 plural, shorten, wants_to_end)


class _Bus:
    def __init__(self):
        self.events = []
        self.items = []

    def emit(self, kind, payload=None):
        self.events.append((kind, payload))

    def feed(self, *a, **k):
        pass

    def feed_items(self, limit=40, unread_only=False):
        return list(self.items)


class _Settings:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, section, key, default=None):
        return self.values.get((section, key), default)


class _Tasks:
    def __init__(self, stats):
        self._stats = stats

    def stats(self):
        return dict(self._stats)


class _ProjectStatus:
    def __init__(self, data):
        self._data = data

    def collect(self, **kwargs):
        return dict(self._data)


class _Monitor:
    def __init__(self, snap):
        self._snap = snap

    def snapshot(self, max_age=2.0):
        return dict(self._snap)


class _Automations:
    """Reprend le calcul réel : `daily` = prochaine occurrence, jamais le passé."""

    @staticmethod
    def next_run(trigger, after=None):
        from datetime import datetime, timedelta

        now = datetime.fromtimestamp(after or time.time())
        target = now.replace(hour=int(trigger["hour"]), minute=int(trigger["minute"]),
                             second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target.timestamp()


class _Absent:
    """Moteur optionnel non installé."""

    @staticmethod
    def available():
        return False


class _Core:
    def __init__(self, *, settings=None, tasks=None, projects=None, monitor=None):
        self.settings = settings or _Settings()
        self.events = _Bus()
        self.tasks = _Tasks(tasks or {})
        self.project_status = _ProjectStatus(projects or {"projects": [], "counts": {}})
        self.monitor = _Monitor(monitor or {})
        self.automations = _Automations()
        # Moteurs vocaux absents par défaut : c'est l'état d'une machine nue,
        # et c'est ce que `status()` doit savoir rapporter.
        self.tts_fallback = _Absent()
        self.stt = _Absent()


class InterpretAnswerTest(unittest.TestCase):
    def test_accepts_immediate_delivery(self):
        for said in ("Maintenant", "vas-y", "oui je t'écoute", "go", "d'accord"):
            self.assertEqual(interpret_answer(said), "now", said)

    def test_accepts_postponement(self):
        for said in ("dans 5 minutes", "Dans cinq minutes", "plus tard", "tout à l'heure",
                     "rappelle-moi"):
            self.assertEqual(interpret_answer(said), "later", said)

    def test_negation_wins_over_the_word_it_contains(self):
        # « pas maintenant » contient « maintenant » : c'est le piège que
        # l'ordre des motifs doit désamorcer.
        self.assertEqual(interpret_answer("pas maintenant"), "later")

    def test_cancellation(self):
        self.assertEqual(interpret_answer("annule, pas aujourd'hui"), "cancel")

    def test_unclear_stays_unclear(self):
        for said in ("", "   ", "euh", "le chat est sur le toit"):
            self.assertEqual(interpret_answer(said), "unclear", said)

    def test_accents_are_optional(self):
        # Whisper rend parfois « ecoute » sans accent : les deux doivent passer.
        self.assertEqual(interpret_answer("je t'ecoute"), "now")
        self.assertEqual(interpret_answer("je t'écoute"), "now")

    def test_end_of_conversation(self):
        self.assertTrue(wants_to_end("merci, c'est tout"))
        self.assertTrue(wants_to_end("arrête"))
        self.assertFalse(wants_to_end("et les mails ?"))
        self.assertFalse(wants_to_end(""))


class SpeakableRenderingTest(unittest.TestCase):
    """Le rapport est lu à voix haute : « (s) » et pavés de texte sont proscrits."""

    def test_plural_never_emits_parentheses(self):
        self.assertEqual(plural(1, "tâche"), "1 tâche")
        self.assertEqual(plural(33, "tâche"), "33 tâches")
        self.assertEqual(plural(0, "tâche"), "0 tâche")
        self.assertEqual(plural(2, "message non lu", "messages non lus"), "2 messages non lus")

    def test_shorten_cuts_on_a_word_boundary(self):
        long = "compare ce googlesheet à mon site pour voir s'il ne me manque pas de brainrot"
        cut = shorten(long, 40)
        self.assertLessEqual(len(cut), 41)
        self.assertTrue(cut.endswith("…"))
        self.assertFalse(cut[:-1].endswith(" "))
        self.assertEqual(shorten("court", 40), "court")

    def test_no_parenthesised_plural_anywhere_in_the_report(self):
        report = MorningReportCollector(_Core(
            tasks={"active": 2, "failed": 1, "waiting_confirmation": 3},
            projects={"projects": [], "counts": {"open_errors": 2}},
        )).collect()
        for section in report.sections:
            for line in section.lines:
                self.assertNotIn("(s)", line)
                self.assertNotIn("(e)", line)

    def test_long_project_progress_is_truncated(self):
        report = MorningReportCollector(_Core(projects={"projects": [
            {"project": "sheets", "state": "bloqué", "last_progress": "x" * 400}],
            "counts": {}})).collect()
        projects = next(s for s in report.sections if s.key == "projects")
        self.assertTrue(all(len(line) < 200 for line in projects.lines), projects.lines)


class PcmToWavTest(unittest.TestCase):
    def test_header_matches_discord_stream_format(self):
        pcm = b"\x00\x01" * 4800
        buffer = BytesIO(pcm_to_wav(pcm))
        with wave.open(buffer, "rb") as handle:
            self.assertEqual(handle.getframerate(), SAMPLE_RATE)
            self.assertEqual(handle.getnchannels(), CHANNELS)
            self.assertEqual(handle.getsampwidth(), SAMPLE_WIDTH)
            self.assertEqual(handle.readframes(handle.getnframes()), pcm)


class CollectorTest(unittest.TestCase):
    def _core(self):
        return _Core(
            tasks={"active": 2, "failed": 1, "waiting_confirmation": 0},
            projects={"projects": [{"project": "Blog", "state": "bloqué",
                                    "last_progress": "article en relecture"}],
                      "counts": {"open_errors": 3}},
            monitor={"cpu": {"percent": 12.0}, "memory": {"percent": 61.0},
                     "disk": {"percent": 91.0}},
        )

    def test_sections_reflect_the_real_state(self):
        report = MorningReportCollector(self._core()).collect()
        keys = [s.key for s in report.sections]
        self.assertIn("tasks", keys)
        self.assertIn("projects", keys)
        self.assertIn("system", keys)

        tasks = next(s for s in report.sections if s.key == "tasks")
        self.assertTrue(any("2 tâches encore en cours" in line for line in tasks.lines))
        self.assertTrue(any("1 tâche en échec" in line for line in tasks.lines))

        projects = next(s for s in report.sections if s.key == "projects")
        self.assertTrue(any("Blog : bloqué" in line for line in projects.lines))
        self.assertTrue(any("3 erreurs encore ouvertes" in line for line in projects.lines))
        # Le projet est déjà détaillé ci-dessus : le relister serait redondant.
        self.assertFalse(any("Également bloqués" in line for line in projects.lines))

        system = next(s for s in report.sections if s.key == "system")
        self.assertTrue(any("Disque presque plein" in line for line in system.lines))

    def test_absent_mailbox_is_announced_not_invented(self):
        report = MorningReportCollector(self._core()).collect()
        mail = next(s for s in report.sections if s.key == "mail")
        self.assertTrue(any("non consultée" in line for line in mail.lines), mail.lines)

    def test_a_broken_source_does_not_lose_the_others(self):
        core = self._core()

        class _Boom:
            def stats(self):
                raise RuntimeError("base verrouillée")

        core.tasks = _Boom()
        report = MorningReportCollector(core).collect()
        self.assertIn("erreur", [s.key for s in report.sections])
        self.assertIn("projects", [s.key for s in report.sections])

    def test_text_rendering_carries_every_line(self):
        report = MorningReportCollector(self._core()).collect()
        rendered = report.as_text()
        for section in report.sections:
            for line in section.lines:
                self.assertIn(line, rendered)


class ScheduleTest(unittest.TestCase):
    def test_next_run_is_always_in_the_future(self):
        core = _Core(settings=_Settings({("discord_call", "hour"): 6,
                                         ("discord_call", "minute"): 0}))
        manager = MorningCallManager(core)
        nxt = manager.next_run()
        self.assertGreater(nxt, time.time())
        self.assertLessEqual(nxt - time.time(), 86400 + 60)
        self.assertEqual(time.localtime(nxt).tm_hour, 6)

    def test_snooze_pushes_a_one_off_reminder(self):
        manager = MorningCallManager(_Core())
        at = manager.snooze(5)
        self.assertAlmostEqual(at - time.time(), 300, delta=5)
        self.assertTrue(manager.status()["snoozed"])

    def test_disabled_by_default_and_status_lists_what_is_missing(self):
        core = _Core()
        status = MorningCallManager(core).status()
        self.assertFalse(status["enabled"])
        self.assertTrue(status["hints"])
        self.assertTrue(any("edge-tts" in h for h in status["hints"]))
        self.assertTrue(any("faster-whisper" in h for h in status["hints"]))

    def test_tick_does_nothing_while_disabled(self):
        core = _Core()
        manager = MorningCallManager(core)
        manager._tick()
        self.assertGreater(manager._next_at, time.time())
        self.assertEqual([k for k, _ in core.events.events], [])


if __name__ == "__main__":
    unittest.main()
