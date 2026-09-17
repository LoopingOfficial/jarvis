"""BUILD JARVIS_BRAINROT_SYNC_V2 : plan, garde-fous, écriture vérifiée, rollback.

Aucune de ces épreuves ne touche le serveur : le magasin vit dans un fichier
temporaire piloté par un écrivain de test au même contrat que le vrai.
"""
import tempfile
import unittest
from pathlib import Path

from jarvis.brainrot_compare import CREATE, UPDATE, compare, parse_site_php
from jarvis.brainrot_sync import (BLOCKED, NEEDS_REVIEW, READY, READY_WITHOUT_IMAGE,
                                  ROLLED_BACK, STALE_COMPARISON, SyncService, VERIFIED,
                                  apply_entry, content_hash, prepare_plan, serialize_store,
                                  validate_plan, verify_entry)
from tests.test_brainrot_compare import SHEET_MERGES, SITE_PHP, workbook

SHEET_ROWS = [
    ["Common", "Fishini Bossini", 1, 50],       # identique au site
    ["", "Tim Cheese", 9, 250],                  # UPDATE sur income
    ["Secret", "Nouveau Brainrot", 999, 9990],   # CREATE
]


class FakeResult:
    def __init__(self, ok, output):
        self.ok, self.output, self.data = ok, output, None


class FakeRunner:
    """Magasin sur disque local. Seules read_file / write_file existent."""

    def __init__(self, path: Path):
        self.path = path
        self.writes = 0
        self.fail_next_write = False
        # Simule un serveur qui n'enregistre pas exactement ce qu'on lui envoie.
        self.corrupt_next_write = False

    def run(self, tool_id, arguments, **kwargs):
        if tool_id == "ssh.read_file":
            with self.path.open(encoding="utf-8", newline="") as handle:
                return FakeResult(True, handle.read())
        if tool_id == "ssh.write_file":
            if self.fail_next_write:
                self.fail_next_write = False
                return FakeResult(False, "disque plein")
            content = arguments["content"]
            if self.corrupt_next_write:
                self.corrupt_next_write = False
                content = content.replace("'income'=>999", "'income'=>1")
            with self.path.open("w", encoding="utf-8", newline="") as handle:
                handle.write(content)
            self.writes += 1
            return FakeResult(True, "OK")
        raise AssertionError(f"outil inattendu : {tool_id}")


class FakeAudit:
    def __init__(self):
        self.events = []

    def record(self, **kwargs):
        self.events.append(kwargs)


class FakeCore:
    def __init__(self, path):
        self.runner = FakeRunner(path)
        self.audit = FakeAudit()


def read_store(path):
    """Lecture sans traduction des fins de ligne (Path.read_text(newline=) est 3.13+)."""
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return handle.read()


def write_store(path, content):
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        handle.write(content)


def store_file(php=SITE_PHP):
    tmp = Path(tempfile.mkdtemp()) / "all_brainrots_sheet_data.php"
    write_store(tmp, serialize_store(parse_site_php(php)))
    return tmp


def comparison(rows=None):
    site = {"ok": True, "path": "data/wiki/x.php", "records": parse_site_php(SITE_PHP),
            "fields": []}
    return compare(workbook(rows or SHEET_ROWS, SHEET_MERGES), site)


def service(path):
    from jarvis.brainrot_sync import StoreWriter
    core = FakeCore(path)
    writer = StoreWriter(core, path=str(path), backup_dir=path.parent / "backups")
    return SyncService(core, writer), core


class SerialisationTests(unittest.TestCase):
    def test_round_trip_is_byte_exact(self):
        records = parse_site_php(SITE_PHP)
        self.assertEqual(parse_site_php(serialize_store(records))[0]["raw"],
                         records[0]["raw"])

    def test_apostrophes_are_escaped_not_dropped(self):
        text = serialize_store([{"raw": {"name": "L'Orso", "rarity": "Epic",
                                         "income": 5, "cost": 50}}])
        self.assertIn("\\'", text)
        self.assertEqual(parse_site_php(text)[0]["raw"]["name"], "L'Orso")

    def test_non_numeric_value_is_refused_at_serialisation(self):
        with self.assertRaises(ValueError):
            serialize_store([{"raw": {"name": "X", "rarity": "Epic",
                                      "income": "bientôt", "cost": 1}}])


class PlanTests(unittest.TestCase):
    def test_plan_only_holds_actionable_entries(self):
        plan = prepare_plan(comparison())
        actions = sorted(e["action"] for e in plan["entries"])
        self.assertNotIn("NO_CHANGE", actions)
        self.assertEqual(plan["counts"]["creates"], 1)
        self.assertEqual(plan["counts"]["updates"], 1)
        self.assertEqual(plan["counts"]["deletes"], 0)

    def test_every_entry_carries_the_full_contract(self):
        for entry in prepare_plan(comparison())["entries"]:
            for key in ("identity", "action", "sheet_values", "current_site_values",
                        "proposed_values", "changed_fields", "missing_fields",
                        "sheet_evidence", "site_evidence", "image_status",
                        "readiness_status"):
                self.assertIn(key, entry)

    def test_update_proposes_only_the_changed_field(self):
        entry = next(e for e in prepare_plan(comparison())["entries"] if e["action"] == UPDATE)
        self.assertEqual(entry["identity"], "Tim Cheese")
        self.assertEqual(entry["proposed_values"]["income"], 9)
        # Les autres champs gardent la valeur du site.
        self.assertEqual(entry["proposed_values"]["cost"], 250.0)
        self.assertEqual(entry["proposed_values"]["rarity"], "Common")

    def test_create_without_official_image_is_ready_without_image(self):
        entry = next(e for e in prepare_plan(comparison())["entries"] if e["action"] == CREATE)
        self.assertEqual(entry["image_status"], "NONE")
        self.assertEqual(entry["readiness_status"], READY_WITHOUT_IMAGE)

    def test_create_with_official_image_is_ready(self):
        result = comparison()
        for entry in result["entries"]:
            if entry["status"] == CREATE:
                entry["image_url"] = "https://brainrot-fortnite.com/img/brainrots/x.png"
        entry = next(e for e in prepare_plan(result)["entries"] if e["action"] == CREATE)
        self.assertEqual(entry["image_status"], "OFFICIAL")
        self.assertEqual(entry["readiness_status"], READY)

    def test_plan_hash_is_stable_and_sensitive(self):
        first = prepare_plan(comparison())["plan_hash"]
        self.assertEqual(first, prepare_plan(comparison())["plan_hash"])
        other = prepare_plan(comparison([["Common", "Fishini Bossini", 1, 50],
                                         ["", "Tim Cheese", 11, 250],
                                         ["Secret", "Nouveau Brainrot", 999, 9990]]))
        self.assertNotEqual(first, other["plan_hash"])


class GuardTests(unittest.TestCase):
    def test_conflict_is_blocked_and_never_selected(self):
        rows = [["Common", "Tim Cheese", 1, 50], ["Common", "tim  cheese", 7, 70],
                ["Common", "Fishini Bossini", 1, 50]]
        plan = prepare_plan(comparison(rows))
        conflicts = [e for e in plan["entries"] if e["action"] == "CONFLICT"]
        self.assertTrue(conflicts)
        self.assertTrue(all(e["readiness_status"] == BLOCKED for e in conflicts))
        self.assertEqual(validate_plan(plan)["selected"], [])

    def test_empty_sheet_cell_never_erases_a_site_value(self):
        path = store_file()
        records = parse_site_php(read_store(path))
        entry = {"identity": "Tim Cheese", "action": UPDATE,
                 "proposed_values": {"rarity": "Common", "income": 5, "cost": 250},
                 "changed_fields": [{"site_field": "rarity", "sheet": "", "site": "Common"}]}
        staged = apply_entry(records, entry)
        target = next(r for r in staged["records"] if r["raw"]["name"] == "Tim Cheese")
        self.assertEqual(target["raw"]["rarity"], "Common")

    def test_apply_refuses_without_confirmation(self):
        path = store_file()
        svc, _ = service(path)
        plan = prepare_plan(comparison(), source_hash=content_hash(
            read_store(path)))
        out = svc.apply(plan, ["Nouveau Brainrot"], confirmation={"approved": False})
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "CONFIRMATION_REQUIRED_USER")

    def test_apply_refuses_a_confirmation_for_another_plan(self):
        path = store_file()
        svc, _ = service(path)
        plan = prepare_plan(comparison())
        out = svc.apply(plan, ["Nouveau Brainrot"],
                        confirmation={"approved": True, "plan_hash": "autre"})
        self.assertEqual(out["error"], "CONFIRMATION_PLAN_MISMATCH")

    def test_batch_is_now_allowed_and_verified_entry_by_entry(self):
        # V2_3 : le lot est ouvert. La surete ne vient plus d'une limite a une
        # entree mais de la verification apres CHAQUE ecriture.
        path = store_file()
        svc, core = service(path)
        plan = prepare_plan(comparison(), source_hash=content_hash(
            read_store(path)))
        self.assertTrue(plan["batch_enabled"])
        out = svc.apply(plan, ["Nouveau Brainrot", "Tim Cheese"],
                        confirmation={"approved": True, "plan_hash": plan["plan_hash"]})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["verified_count"], 2)
        self.assertEqual(core.runner.writes, 2)

    def test_stale_store_stops_everything(self):
        path = store_file()
        svc, core = service(path)
        plan = prepare_plan(comparison(), source_hash="empreinte-obsolete")
        out = svc.apply(plan, ["Nouveau Brainrot"],
                        confirmation={"approved": True, "plan_hash": plan["plan_hash"]})
        self.assertEqual(out["error"], STALE_COMPARISON)
        self.assertEqual(core.runner.writes, 0)


class ApplyTests(unittest.TestCase):
    def prepared(self, path):
        return prepare_plan(comparison(), source_hash=content_hash(
            read_store(path)))

    def test_single_create_is_applied_and_verified(self):
        path = store_file()
        svc, core = service(path)
        plan = self.prepared(path)
        out = svc.apply(plan, ["Nouveau Brainrot"],
                        confirmation={"approved": True, "plan_hash": plan["plan_hash"]},
                        request_id="req_test")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["results"][0]["status"], VERIFIED)
        records = parse_site_php(read_store(path))
        self.assertEqual(len(records), 5)
        created = records[-1]["raw"]
        self.assertEqual(created["name"], "Nouveau Brainrot")
        self.assertEqual(created["income"], 999)
        self.assertTrue(Path(out["backup_path"]).is_file())

    def test_single_update_touches_only_the_changed_field(self):
        path = store_file()
        svc, _ = service(path)
        plan = self.prepared(path)
        before = parse_site_php(read_store(path))
        out = svc.apply(plan, ["Tim Cheese"],
                        confirmation={"approved": True, "plan_hash": plan["plan_hash"]})
        self.assertTrue(out["ok"], out)
        after = parse_site_php(read_store(path))
        self.assertEqual(len(after), len(before))
        target = next(r for r in after if r["raw"]["name"] == "Tim Cheese")
        self.assertEqual(target["raw"]["income"], 9)
        self.assertEqual(target["raw"]["cost"], 250)
        others_before = [r["raw"] for r in before if r["raw"]["name"] != "Tim Cheese"]
        others_after = [r["raw"] for r in after if r["raw"]["name"] != "Tim Cheese"]
        self.assertEqual(others_before, others_after)

    def test_no_entry_is_ever_deleted(self):
        path = store_file()
        svc, _ = service(path)
        plan = self.prepared(path)
        before = len(parse_site_php(read_store(path)))
        svc.apply(plan, ["Tim Cheese"],
                  confirmation={"approved": True, "plan_hash": plan["plan_hash"]})
        self.assertGreaterEqual(
            len(parse_site_php(read_store(path))), before)

    def test_failed_write_leaves_the_store_untouched(self):
        path = store_file()
        svc, core = service(path)
        original = read_store(path)
        core.runner.fail_next_write = True
        plan = self.prepared(path)
        out = svc.apply(plan, ["Nouveau Brainrot"],
                        confirmation={"approved": True, "plan_hash": plan["plan_hash"]})
        self.assertFalse(out["ok"])
        self.assertEqual(read_store(path), original)

    def test_audit_records_the_application_without_secrets(self):
        path = store_file()
        svc, core = service(path)
        plan = self.prepared(path)
        svc.apply(plan, ["Nouveau Brainrot"],
                  confirmation={"approved": True, "plan_hash": plan["plan_hash"]},
                  request_id="req_audit")
        actions = [e["action"] for e in core.audit.events]
        for event in ("backup_created", "write_started", "applied", "verified"):
            self.assertIn(f"brainrot_sync.{event}", actions, event)
        blob = " ".join(str(e) for e in core.audit.events).lower()
        for secret in ("password", "private_key", "passphrase"):
            self.assertNotIn(secret, blob)


class RollbackTests(unittest.TestCase):
    def test_rollback_restores_the_exact_previous_bytes(self):
        path = store_file()
        svc, _ = service(path)
        original = read_store(path)
        plan = prepare_plan(comparison(), source_hash=content_hash(original))
        out = svc.apply(plan, ["Nouveau Brainrot"],
                        confirmation={"approved": True, "plan_hash": plan["plan_hash"]})
        self.assertNotEqual(read_store(path), original)
        restored = svc.rollback(out["backup_path"])
        self.assertTrue(restored["ok"])
        self.assertEqual(restored["status"], ROLLED_BACK)
        self.assertEqual(read_store(path), original)

    def test_verification_failure_triggers_a_rollback(self):
        path = store_file()
        svc, _ = service(path)
        plan = prepare_plan(comparison(), source_hash=content_hash(
            read_store(path)))
        original = read_store(path)
        entry = next(e for e in plan["entries"] if e["action"] == CREATE)
        # Le serveur enregistre autre chose que ce qui a été envoyé : la
        # relecture doit le détecter et déclencher le retour arrière.
        svc.writer.core.runner.corrupt_next_write = True
        out = svc.apply(plan, [entry["identity"]],
                        confirmation={"approved": True, "plan_hash": plan["plan_hash"]})
        self.assertFalse(out["ok"])
        self.assertEqual(out["results"][0]["status"], ROLLED_BACK)
        self.assertEqual(read_store(path), original)


class VerifyTests(unittest.TestCase):
    def test_collateral_change_is_detected(self):
        records = parse_site_php(SITE_PHP)
        entry = {"identity": "Tim Cheese", "action": UPDATE,
                 "proposed_values": {"income": 5}, "changed_fields": []}
        after = [dict(r, raw=dict(r["raw"])) for r in records]
        after[0]["raw"]["income"] = 4242  # une autre entrée bouge
        check = verify_entry(after, entry, records)
        self.assertFalse(check["ok"])
        self.assertEqual(check["reason"], "entrées collatérales modifiées")


class IdempotenceTests(unittest.TestCase):
    def test_a_second_compare_after_sync_shows_no_change(self):
        path = store_file()
        svc, _ = service(path)
        plan = prepare_plan(comparison(), source_hash=content_hash(
            read_store(path)))
        for identity in ("Nouveau Brainrot", "Tim Cheese"):
            fresh = prepare_plan(
                comparison(), source_hash=content_hash(read_store(path)))
            out = svc.apply(fresh, [identity],
                            confirmation={"approved": True, "plan_hash": fresh["plan_hash"]})
            self.assertTrue(out["ok"], out)
        site = {"ok": True, "path": "x", "fields": [],
                "records": parse_site_php(read_store(path))}
        again = compare(workbook(SHEET_ROWS, SHEET_MERGES), site)
        self.assertEqual(again["counts"]["CREATE"], 0)
        self.assertEqual(again["counts"]["UPDATE"], 0)
        self.assertEqual(again["counts"]["NO_CHANGE"], 3)
        self.assertEqual(prepare_plan(again)["counts"]["applicable"], 0)


if __name__ == "__main__":
    unittest.main()
