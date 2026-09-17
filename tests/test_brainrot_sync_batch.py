"""BUILD JARVIS_BRAINROT_SYNC_AUDIT_REFRESH_BATCH_V2_3 — T1 à T15.

Aucune de ces épreuves ne touche la production : le magasin est un fichier
temporaire piloté par le même contrat d'écrivain que le vrai.
"""
import unittest
from pathlib import Path

from jarvis.brainrot_compare import CREATE, UPDATE, compare, parse_site_php
from jarvis.brainrot_sync import (APPLYING, CONFIRMATION_ALREADY_USED,
                                  CONFIRMATION_SCOPE_MISMATCH, ConfirmationScope, FAILED,
                                  PENDING, ROLLED_BACK, SKIPPED, STALE_COMPARISON,
                                  SyncService, VERIFIED, WRITE_SUCCEEDED_AUDIT_FAILED,
                                  content_hash, prepare_plan, selection_hash,
                                  serialize_store)
from jarvis.sync_audit import AuditWriteFailed
from tests.test_brainrot_compare import SHEET_MERGES, SITE_PHP, workbook
from tests.test_brainrot_sync import (FakeCore, read_store, service, store_file,
                                      write_store)

# Trois CREATE et deux UPDATE, de quoi éprouver un vrai lot mixte.
ROWS = [
    ["Common", "Fishini Bossini", 1, 50],        # identique
    ["", "Tim Cheese", 9, 250],                   # UPDATE income
    ["Epic", "Vieux Modele", 10, 100],            # UPDATE rarity
    ["Secret", "Alpha Rot", 100, 1000],           # CREATE
    ["Secret", "Beta Rot", 200, 2000],            # CREATE
    ["Secret", "Gamma Rot", 300, 3000],           # CREATE
]
CREATES = ["Alpha Rot", "Beta Rot", "Gamma Rot"]
UPDATES = ["Tim Cheese", "Vieux Modele"]


def comparison(rows=None):
    site = {"ok": True, "path": "data/wiki/x.php", "records": parse_site_php(SITE_PHP),
            "fields": []}
    return compare(workbook(rows or ROWS, SHEET_MERGES), site)


def plan_for(path, rows=None):
    return prepare_plan(comparison(rows), source_hash=content_hash(read_store(path)))


def approve(plan):
    return {"approved": True, "plan_hash": plan["plan_hash"]}


def names(result):
    return [r["identity"] for r in result["results"]]


class T1_MultiCreate(unittest.TestCase):
    def test_several_creates_are_applied_in_order(self):
        path = store_file()
        svc, core = service(path)
        plan = plan_for(path)
        out = svc.apply(plan, CREATES, confirmation=approve(plan))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["verified_count"], 3)
        self.assertEqual(names(out), CREATES)
        self.assertEqual(core.runner.writes, 3)
        stored = [r["raw"]["name"] for r in parse_site_php(read_store(path))]
        self.assertEqual(stored[-3:], CREATES)


class T2_MixedBatch(unittest.TestCase):
    def test_create_and_update_in_the_same_batch(self):
        path = store_file()
        svc, _ = service(path)
        plan = plan_for(path)
        out = svc.apply(plan, ["Alpha Rot", "Tim Cheese"], confirmation=approve(plan))
        self.assertTrue(out["ok"], out)
        actions = {r["identity"]: r["action"] for r in out["results"]}
        self.assertEqual(actions, {"Alpha Rot": CREATE, "Tim Cheese": UPDATE})
        records = {r["raw"]["name"]: r["raw"] for r in parse_site_php(read_store(path))}
        self.assertEqual(records["Tim Cheese"]["income"], 9)
        self.assertIn("Alpha Rot", records)


class T3_SelectionBound(unittest.TestCase):
    def test_only_the_selected_entries_are_written(self):
        path = store_file()
        svc, core = service(path)
        plan = plan_for(path)
        self.assertEqual(len(plan["entries"]), 5)
        out = svc.apply(plan, CREATES, confirmation=approve(plan))
        self.assertEqual(core.runner.writes, 3)
        self.assertTrue(out["ok"])
        # Les entrées non cochées restent intactes.
        records = {r["raw"]["name"]: r["raw"] for r in parse_site_php(read_store(path))}
        self.assertEqual(records["Tim Cheese"]["income"], 5)


class T4_ScopeMismatch(unittest.TestCase):
    def test_changing_the_selection_after_confirmation_is_refused(self):
        path = store_file()
        svc, core = service(path)
        plan = plan_for(path)
        opened = svc.open_confirmation(plan, ["Alpha Rot"])
        scope_id = opened["scope"]["confirmation_id"]
        out = svc.apply(plan, ["Alpha Rot", "Beta Rot"], confirmation=approve(plan),
                        scope_id=scope_id)
        self.assertEqual(out["error"], CONFIRMATION_SCOPE_MISMATCH)
        self.assertEqual(core.runner.writes, 0)

    def test_changing_the_proposed_values_invalidates_the_scope(self):
        path = store_file()
        svc, core = service(path)
        plan = plan_for(path)
        opened = svc.open_confirmation(plan, ["Tim Cheese"])
        entry = next(e for e in plan["entries"] if e["identity"] == "Tim Cheese")
        entry["proposed_values"]["income"] = 999999
        out = svc.apply(plan, ["Tim Cheese"], confirmation=approve(plan),
                        scope_id=opened["scope"]["confirmation_id"])
        self.assertEqual(out["error"], CONFIRMATION_SCOPE_MISMATCH)
        self.assertEqual(core.runner.writes, 0)


class T5_ScopeSingleUse(unittest.TestCase):
    def test_a_confirmation_cannot_be_replayed(self):
        path = store_file()
        svc, core = service(path)
        plan = plan_for(path)
        opened = svc.open_confirmation(plan, ["Alpha Rot"])
        scope_id = opened["scope"]["confirmation_id"]
        first = svc.apply(plan, ["Alpha Rot"], confirmation=approve(plan), scope_id=scope_id)
        self.assertTrue(first["ok"], first)
        second = svc.apply(plan, ["Alpha Rot"], confirmation=approve(plan), scope_id=scope_id)
        self.assertEqual(second["error"], CONFIRMATION_ALREADY_USED)
        self.assertEqual(core.runner.writes, 1)


class T6_Idempotency(unittest.TestCase):
    def test_the_same_key_never_writes_twice(self):
        path = store_file()
        svc, core = service(path)
        plan = plan_for(path)
        key = "idem-test-1"
        first = svc.apply(plan, ["Alpha Rot"], confirmation=approve(plan), idempotency_key=key)
        self.assertTrue(first["ok"], first)
        second = svc.apply(plan, ["Alpha Rot"], confirmation=approve(plan), idempotency_key=key)
        self.assertTrue(second.get("replayed"))
        self.assertEqual(core.runner.writes, 1)
        self.assertEqual(second["results"], first["results"])


class T7_BatchSuccess(unittest.TestCase):
    def test_three_entries_all_verified(self):
        path = store_file()
        svc, _ = service(path)
        plan = plan_for(path)
        out = svc.apply(plan, CREATES, confirmation=approve(plan))
        self.assertEqual([r["status"] for r in out["results"]], [VERIFIED] * 3)
        self.assertEqual(out["statuses"], {name: VERIFIED for name in CREATES})


class T8_StopOnFailure(unittest.TestCase):
    def test_failure_on_the_second_entry_skips_the_third(self):
        path = store_file()
        svc, core = service(path)
        plan = plan_for(path)
        original = read_store(path)

        # La deuxième écriture est corrompue : la vérification doit la rejeter.
        real_write = core.runner.run
        state = {"writes": 0}

        def flaky(tool_id, arguments, **kwargs):
            if tool_id == "ssh.write_file":
                state["writes"] += 1
                if state["writes"] == 2:
                    arguments = dict(arguments)
                    arguments["content"] = arguments["content"].replace(
                        "'name'=>'Beta Rot'", "'name'=>'Beta Rot Corrompu'")
            return real_write(tool_id, arguments, **kwargs)

        core.runner.run = flaky
        out = svc.apply(plan, CREATES, confirmation=approve(plan))
        self.assertFalse(out["ok"])
        self.assertEqual(out["statuses"]["Alpha Rot"], VERIFIED)
        self.assertEqual(out["statuses"]["Beta Rot"], ROLLED_BACK)
        self.assertEqual(out["statuses"]["Gamma Rot"], SKIPPED)
        self.assertEqual(out["verified_count"], 1)
        self.assertEqual(out["skipped_count"], 1)
        # Le rollback ramène au contenu d'avant l'entrée fautive.
        self.assertIn("Alpha Rot", read_store(path))
        self.assertNotIn("Beta Rot", read_store(path))
        self.assertNotEqual(read_store(path), original)


class T9_RefreshResolves(unittest.TestCase):
    def test_after_a_create_the_diff_loses_it(self):
        path = store_file()
        svc, _ = service(path)
        plan = plan_for(path)
        before = comparison()["counts"]
        self.assertEqual(before[CREATE], 3)
        svc.apply(plan, ["Alpha Rot"], confirmation=approve(plan))
        site = {"ok": True, "path": "x", "fields": [],
                "records": parse_site_php(read_store(path))}
        after = compare(workbook(ROWS, SHEET_MERGES), site)["counts"]
        self.assertEqual(after[CREATE], before[CREATE] - 1)
        self.assertEqual(after["NO_CHANGE"], before["NO_CHANGE"] + 1)


class T11_StaleSelection(unittest.TestCase):
    def test_an_external_change_blocks_the_confirmed_selection(self):
        path = store_file()
        svc, core = service(path)
        plan = plan_for(path)
        opened = svc.open_confirmation(plan, ["Alpha Rot"])
        # Modification externe du magasin entre la confirmation et l'application.
        write_store(path, read_store(path).replace("'income'=>1,", "'income'=>2,"))
        out = svc.apply(plan, ["Alpha Rot"], confirmation=approve(plan),
                        scope_id=opened["scope"]["confirmation_id"])
        self.assertEqual(out["error"], STALE_COMPARISON)
        self.assertEqual(core.runner.writes, 0)


class T12_AuditContract(unittest.TestCase):
    def test_every_lifecycle_event_is_emitted_in_order(self):
        path = store_file()
        svc, core = service(path)
        plan = plan_for(path)
        opened = svc.open_confirmation(plan, ["Alpha Rot"])
        out = svc.apply(plan, ["Alpha Rot"], confirmation=approve(plan),
                        scope_id=opened["scope"]["confirmation_id"],
                        request_id="req_T12")
        self.assertTrue(out["ok"], out)
        events = [e["action"].split(".", 1)[1] for e in core.audit.events]
        for expected in ("confirmed", "backup_created", "write_started", "applied", "verified"):
            self.assertIn(expected, events, expected)
        self.assertLess(events.index("write_started"), events.index("applied"))
        self.assertLess(events.index("applied"), events.index("verified"))

    def test_events_carry_the_contract_fields_and_no_secret(self):
        path = store_file()
        svc, core = service(path)
        plan = plan_for(path)
        svc.apply(plan, ["Tim Cheese"], confirmation=approve(plan), request_id="req_fields")
        applied = next(e for e in core.audit.events if e["action"].endswith(".applied"))
        detail = applied["detail"]
        for field in ("sync_id", "plan_id", "entry_identity", "action", "changed_fields",
                      "before_hash", "after_hash", "request_id"):
            self.assertIn(field, detail, field)
        for secret in ("password", "private_key", "passphrase", "token"):
            self.assertNotIn(secret, detail.lower())

    def test_applied_is_never_emitted_when_the_write_fails(self):
        path = store_file()
        svc, core = service(path)
        core.runner.fail_next_write = True
        plan = plan_for(path)
        svc.apply(plan, ["Alpha Rot"], confirmation=approve(plan))
        events = [e["action"] for e in core.audit.events]
        self.assertNotIn("brainrot_sync.applied", events)
        self.assertIn("brainrot_sync.failed", events)


class T13_AuditFailure(unittest.TestCase):
    def test_write_succeeded_audit_failed_is_reported_without_replay(self):
        path = store_file()
        svc, core = service(path)
        plan = plan_for(path)

        # Le journal tombe après la sauvegarde : l'écriture, elle, réussit.
        def broken(**kwargs):
            if kwargs.get("action", "").endswith(".applied"):
                raise RuntimeError("journal indisponible")
            core.audit.events.append(kwargs)

        core.audit.record = broken
        out = svc.apply(plan, ["Alpha Rot"], confirmation=approve(plan))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], WRITE_SUCCEEDED_AUDIT_FAILED)
        self.assertFalse(out["audit_complete"])
        # L'écriture a bien eu lieu, une seule fois : aucun rejeu.
        self.assertEqual(core.runner.writes, 1)
        self.assertIn("Alpha Rot", read_store(path))
        self.assertEqual(out["verified_count"], 1)


class ScopeContract(unittest.TestCase):
    def test_scope_exposes_the_full_contract(self):
        path = store_file()
        svc, _ = service(path)
        plan = plan_for(path)
        scope = svc.open_confirmation(plan, ["Tim Cheese"])["scope"]
        for key in ("confirmation_id", "plan_id", "expected_sha256", "selected_entry_ids",
                    "actions", "changed_fields", "proposed_values", "selection_hash",
                    "expires_at"):
            self.assertIn(key, scope)
        self.assertEqual(scope["actions"], {"Tim Cheese": UPDATE})
        # La portee decrit ce qui sera ecrit COTE SITE : la cle du site,
        # pas le libelle de la colonne Sheet.
        self.assertEqual(scope["changed_fields"], {"Tim Cheese": ["income"]})

    def test_selection_hash_is_order_independent_but_content_sensitive(self):
        path = store_file()
        plan = plan_for(path)
        self.assertEqual(selection_hash(plan, ["Alpha Rot", "Beta Rot"]),
                         selection_hash(plan, ["Beta Rot", "Alpha Rot"]))
        self.assertNotEqual(selection_hash(plan, ["Alpha Rot"]),
                            selection_hash(plan, ["Alpha Rot", "Beta Rot"]))

    def test_expired_scope_is_refused(self):
        path = store_file()
        svc, core = service(path)
        plan = plan_for(path)
        scope = ConfirmationScope(plan, ["Alpha Rot"],
                                  expected_sha256=content_hash(read_store(path)), ttl=-1)
        SyncService._scopes[scope.id] = scope
        out = svc.apply(plan, ["Alpha Rot"], confirmation=approve(plan), scope_id=scope.id)
        self.assertEqual(out["error"], "CONFIRMATION_EXPIRED")
        self.assertEqual(core.runner.writes, 0)


class NoDeletion(unittest.TestCase):
    def test_a_batch_never_shrinks_the_store(self):
        path = store_file()
        svc, _ = service(path)
        before = len(parse_site_php(read_store(path)))
        plan = plan_for(path)
        svc.apply(plan, CREATES + UPDATES, confirmation=approve(plan))
        self.assertEqual(len(parse_site_php(read_store(path))), before + 3)


if __name__ == "__main__":
    unittest.main()
