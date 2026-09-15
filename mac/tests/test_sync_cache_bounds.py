"""Les caches de classe de SyncService sont bornés.

`_results` et `_scopes` sont portés par la classe, donc partagés par tout le
processus. Sans purge ils grossissent indéfiniment : ces tests vérifient que la
fenêtre de rejeu, le plafond de taille et l'expiration des portées tiennent.
"""
from __future__ import annotations

import time
import unittest

from jarvis.brainrot_sync import (MAX_CACHED_RESULTS, RESULT_TTL_SECONDS,
                                  SCOPE_GRACE_SECONDS, SyncService)


class _Scope:
    def __init__(self, expires_at: float, used: bool = False,
                 used_at: float = 0.0) -> None:
        self.expires_at = expires_at
        self.used = used
        self.used_at = used_at


class CacheBoundsTests(unittest.TestCase):
    def setUp(self):
        SyncService._results.clear()
        SyncService._scopes.clear()

    tearDown = setUp

    # -- portées ---------------------------------------------------------
    def test_long_expired_scopes_are_dropped(self):
        now = time.time()
        SyncService._scopes["alive"] = _Scope(now + 300)
        SyncService._scopes["ancient"] = _Scope(now - SCOPE_GRACE_SECONDS - 60)
        SyncService._sweep_caches()
        self.assertIn("alive", SyncService._scopes)
        self.assertNotIn("ancient", SyncService._scopes)

    def test_recently_expired_scope_is_kept_for_the_error_message(self):
        """Pendant la grâce, la portée reste pour répondre CONFIRMATION_EXPIRED.

        La supprimer aussitôt ferait répondre « portée inconnue » à un
        utilisateur qui a simplement laissé la modale ouverte trop longtemps.
        """
        SyncService._scopes["just_expired"] = _Scope(time.time() - 5)
        SyncService._sweep_caches()
        self.assertIn("just_expired", SyncService._scopes)

    def test_used_scope_is_kept_during_grace_then_dropped(self):
        now = time.time()
        SyncService._scopes["fresh"] = _Scope(now + 300, used=True, used_at=now)
        SyncService._scopes["old"] = _Scope(
            now + 300, used=True, used_at=now - SCOPE_GRACE_SECONDS - 60)
        SyncService._sweep_caches()
        self.assertIn("fresh", SyncService._scopes,
                      "doit encore pouvoir répondre CONFIRMATION_ALREADY_USED")
        self.assertNotIn("old", SyncService._scopes)

    # -- résultats -------------------------------------------------------
    def test_results_outside_the_replay_window_are_dropped(self):
        now = time.time()
        SyncService._results["recent"] = {"at": now, "payload": {"ok": True}}
        SyncService._results["stale"] = {"at": now - RESULT_TTL_SECONDS - 60,
                                         "payload": {"ok": True}}
        SyncService._sweep_caches()
        self.assertIn("recent", SyncService._results)
        self.assertNotIn("stale", SyncService._results)

    def test_results_never_exceed_the_hard_cap(self):
        """Même dans la fenêtre de rejeu, le cache ne croît pas sans limite."""
        now = time.time()
        for i in range(MAX_CACHED_RESULTS + 120):
            SyncService._results[f"k{i}"] = {"at": now + i, "payload": {"i": i}}
        SyncService._sweep_caches()
        self.assertLessEqual(len(SyncService._results), MAX_CACHED_RESULTS)
        # Les plus récents survivent, les plus anciens partent.
        self.assertIn(f"k{MAX_CACHED_RESULTS + 119}", SyncService._results)
        self.assertNotIn("k0", SyncService._results)

    def test_sweep_reports_the_remaining_sizes(self):
        SyncService._results["a"] = {"at": time.time(), "payload": {}}
        SyncService._scopes["s"] = _Scope(time.time() + 300)
        sizes = SyncService._sweep_caches()
        self.assertEqual(sizes, {"scopes": 1, "results": 1})

    def test_sweep_is_safe_on_empty_caches(self):
        self.assertEqual(SyncService._sweep_caches(), {"scopes": 0, "results": 0})


if __name__ == "__main__":
    unittest.main()
