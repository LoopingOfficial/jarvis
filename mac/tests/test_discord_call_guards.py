"""Garde-fous de l'appel matinal : pas de coroutine zombie, pas de reports sans fin.

Les deux comportements vérifiés ici sont invisibles en usage normal — un appel
qui se déroule bien ne les déclenche jamais. Ils ne protègent que des cas
dégradés : un appel qui se fige, une transcription qui entend « attends » dans
un bruit de fond. C'est exactement pour ça qu'ils ont besoin d'un test : rien
ne les rappellerait lors d'une réécriture.

Aucun test ne contacte Discord.
"""
import asyncio
import threading
import time
import unittest

from jarvis import discord_call
from jarvis.discord_call import MAX_SNOOZES, CallOutcome, MorningCallManager


class _Events:
    def __init__(self):
        self.emitted = []

    def emit(self, kind, payload):
        self.emitted.append((kind, payload))


class _Settings:
    def __init__(self, values):
        self.values = values

    def get(self, _section, key, default=None):
        return self.values.get(key, default)


class _Engine:
    """Moteur Discord factice, avec une VRAIE boucle asyncio dans un thread.

    Une boucle réelle est indispensable ici : le correctif repose sur la
    propagation de `Future.cancel()` jusqu'à la tâche asyncio, ce qu'un double
    simplifié ne prouverait pas.
    """

    available = True
    connected = True

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.audits = []
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.call_soon(self._ready.set)
        self.loop.run_forever()

    def close(self):
        # Laisser les tâches en cours d'annulation se terminer avant d'arrêter
        # la boucle, sinon asyncio crie « Task was destroyed but it is pending ».
        async def drain():
            pending = [t for t in asyncio.all_tasks(self.loop)
                       if t is not asyncio.current_task()]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        try:
            asyncio.run_coroutine_threadsafe(drain(), self.loop).result(timeout=5)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)
        self.loop.close()

    def spawn(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def start(self):
        return {"ok": True}

    def audit(self, action, status="ok", detail=""):
        self.audits.append((action, status, detail))

    def redact(self, text):
        return str(text)


def _manager(**overrides):
    # `wait_user_s` doit rester non nul : le module fait `valeur or 300`, donc
    # un zéro retomberait sur la valeur par défaut et le test attendrait 5 min.
    values = {"enabled": True, "wait_user_s": 1, "snooze_minutes": 5,
              "max_snoozes": MAX_SNOOZES}
    values.update(overrides)
    core = type("Core", (), {"settings": _Settings(values), "events": _Events(),
                             "automations": None})()
    manager = MorningCallManager(core)
    return manager


class CancelZombieTest(unittest.TestCase):
    """Un appel figé doit être ANNULÉ, pas simplement abandonné."""

    def setUp(self):
        self.engine = _Engine()
        self.addCleanup(self.engine.close)
        self.manager = _manager()
        # `engine` est une property : on force le moteur factice sur le cœur.
        self.manager.core.discord = self.engine
        # Budget ramené à une seconde : on teste l'annulation, pas la patience.
        marge = discord_call.CALL_BUDGET_MARGIN_S
        discord_call.CALL_BUDGET_MARGIN_S = 1.0
        self.addCleanup(lambda: setattr(discord_call, "CALL_BUDGET_MARGIN_S", marge))
        self.cancelled = threading.Event()
        self.cleaned = threading.Event()

    def _install(self, coro_factory):
        self.manager.collector = type("C", (), {"collect": lambda _s: None})()
        discord_call.MorningCallSession = lambda *a, **k: type(
            "S", (), {"run": lambda _s: coro_factory()})()

    def test_le_futur_est_annule_et_la_session_nettoie(self):
        async def qui_se_fige():
            try:
                await asyncio.sleep(3600)          # appel bloqué
            except asyncio.CancelledError:
                self.cancelled.set()
                self.cleaned.set()                 # équivaut au `finally: _disconnect()`
                raise

        original = discord_call.MorningCallSession
        self.addCleanup(lambda: setattr(discord_call, "MorningCallSession", original))
        self._install(qui_se_fige)

        result = self.manager.run_now(reason="test")

        self.assertFalse(result["ok"])
        self.assertEqual(result["outcome"], CallOutcome.FAILED)
        self.assertIn("interrompu", result["detail"])
        # Le point du correctif : la coroutine a bien reçu l'annulation.
        self.assertTrue(self.cancelled.wait(timeout=5),
                        "la coroutine n'a pas été annulée : zombie dans la boucle du bot")
        self.assertTrue(self.cleaned.wait(timeout=5),
                        "le nettoyage de la session n'a pas eu lieu")

    def test_le_verrou_est_relache_apres_un_appel_fige(self):
        """Un appel figé ne doit pas bloquer tous les suivants."""
        original = discord_call.MorningCallSession
        self.addCleanup(lambda: setattr(discord_call, "MorningCallSession", original))
        self._install(lambda: asyncio.sleep(3600))
        self.manager.run_now(reason="test")
        self.assertFalse(self.manager._in_call)


class SnoozeCapTest(unittest.TestCase):
    """Le nombre de reports consécutifs est borné."""

    def setUp(self):
        self.manager = _manager()
        self.manager.core.discord = type("E", (), {"audit": lambda *a, **k: None})()

    def test_trois_reports_puis_refus(self):
        for n in range(MAX_SNOOZES):
            self.assertTrue(self.manager.snooze(5), f"report {n + 1} refusé à tort")
        self.assertEqual(self.manager.snooze(5), 0.0,
                         "le quatrième report aurait dû être refusé")

    def test_le_refus_est_trace(self):
        for _ in range(MAX_SNOOZES + 1):
            self.manager.snooze(5)
        kinds = [k for k, _ in self.manager.core.events.emitted]
        self.assertIn("discord.call.snooze_refused", kinds)

    def test_un_nouveau_matin_remet_le_compteur_a_zero(self):
        for _ in range(MAX_SNOOZES):
            self.manager.snooze(5)
        self.assertEqual(self.manager.snooze(5), 0.0)

        # Un appel planifié (et non un rappel) rouvre une série.
        self.manager._in_call = False
        try:
            self.manager.run_now(reason="planifié")
        except Exception:
            pass                                   # le moteur factice échoue : sans importance
        self.assertEqual(self.manager._snooze_count, 0)
        self.assertTrue(self.manager.snooze(5))

    def test_un_rappel_ne_remet_pas_le_compteur_a_zero(self):
        """Sinon le plafond ne serait jamais atteint : chaque rappel l'effacerait."""
        for _ in range(MAX_SNOOZES):
            self.manager.snooze(5)
        self.manager._in_call = False
        try:
            self.manager.run_now(reason="rappel")
        except Exception:
            pass
        self.assertEqual(self.manager._snooze_count, MAX_SNOOZES)

    def test_plafond_a_zero_interdit_tout_report(self):
        manager = _manager(max_snoozes=0)
        manager.core.discord = type("E", (), {"audit": lambda *a, **k: None})()
        self.assertEqual(manager.snooze(5), 0.0)

    def test_le_temps_de_report_est_coherent(self):
        before = time.time()
        at = self.manager.snooze(5)
        self.assertGreaterEqual(at, before + 299)
        self.assertLessEqual(at, before + 301)


class PluralTest(unittest.TestCase):
    """Le rapport est LU : « 3 foiss » s'entendrait."""

    def test_formes_irregulieres(self):
        self.assertEqual(discord_call.plural(3, "fois", "fois"), "3 fois")
        self.assertEqual(discord_call.plural(1, "fois", "fois"), "1 fois")
        self.assertEqual(discord_call.plural(3, "report"), "3 reports")
        self.assertEqual(discord_call.plural(1, "report"), "1 report")


if __name__ == "__main__":
    unittest.main()
