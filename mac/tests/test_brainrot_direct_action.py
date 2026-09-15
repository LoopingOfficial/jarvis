"""Phase 2 — action directe sur UNE entrée depuis la vue Comparaison.

Le bouton [+ Créer] / [Mettre à jour] n'introduit aucune logique métier : il
appelle le SyncEngine avec une sélection d'un seul élément. Ce test vérifie la
garantie visible par l'utilisateur : après un clic, l'entrée passe VERIFIED puis
NO_CHANGE au recalcul, et les compteurs KPI suivent — le tout sur un fichier
local de test, jamais sur la production.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from jarvis.brainrot_compare import compare, parse_site_php
from jarvis.brainrot_sync import (VERIFIED, SyncService, content_hash,
                                  prepare_plan)

from test_brainrot_sync import (SHEET_MERGES, SHEET_ROWS, SITE_PHP, read_store,
                                service, store_file, workbook)


def comparison_against(path):
    """Recompare le Sheet avec l'état RÉEL du fichier local après écriture."""
    site = {"ok": True, "path": str(path),
            "records": parse_site_php(read_store(path)), "fields": []}
    return compare(workbook(SHEET_ROWS, SHEET_MERGES), site)


def status_of(comp, identity):
    for entry in comp["entries"]:
        if entry["identity"] == identity:
            return entry["status"]
    return None


class DirectActionTests(unittest.TestCase):
    """Une action directe = une sélection d'un seul élément, même moteur."""

    def setUp(self):
        # Le cache d'idempotence de SyncService est un attribut de CLASSE :
        # sans purge, un test rejouerait le resultat d'un autre.
        SyncService._results.clear()
        SyncService._scopes.clear()

    def apply_one(self, path, identity):
        svc, _ = service(path)
        comp = comparison_against(path)
        plan = prepare_plan(comp, source_hash=content_hash(read_store(path)))
        out = svc.apply(
            plan, [identity],
            confirmation={"approved": True, "plan_hash": plan["plan_hash"]},
            request_id="req_direct")
        return out, plan

    # -- CREATE ----------------------------------------------------------
    def test_direct_create_becomes_no_change(self):
        path = store_file()
        before = comparison_against(path)
        self.assertEqual(status_of(before, "Nouveau Brainrot"), "CREATE")

        out, _ = self.apply_one(path, "Nouveau Brainrot")
        self.assertTrue(out["ok"], out)
        self.assertEqual(len(out["results"]), 1, "une seule entrée écrite")
        self.assertEqual(out["results"][0]["status"], VERIFIED)

        after = comparison_against(path)
        self.assertEqual(status_of(after, "Nouveau Brainrot"), "NO_CHANGE")

    def test_direct_create_updates_the_kpi_counts(self):
        path = store_file()
        before = comparison_against(path)["counts"]
        self.apply_one(path, "Nouveau Brainrot")
        after = comparison_against(path)["counts"]
        self.assertEqual(after.get("CREATE", 0), before.get("CREATE", 0) - 1)
        self.assertEqual(after.get("NO_CHANGE", 0), before.get("NO_CHANGE", 0) + 1)

    # -- UPDATE ----------------------------------------------------------
    def test_direct_update_becomes_no_change(self):
        path = store_file()
        self.assertEqual(status_of(comparison_against(path), "Tim Cheese"), "UPDATE")
        out, _ = self.apply_one(path, "Tim Cheese")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["results"][0]["status"], VERIFIED)
        self.assertEqual(status_of(comparison_against(path), "Tim Cheese"), "NO_CHANGE")

    def test_direct_update_leaves_every_other_entry_untouched(self):
        """Une action directe ne touche QUE sa ligne — le reste est inchangé."""
        path = store_file()
        before = {r["raw"]["name"]: dict(r["raw"]) for r in parse_site_php(read_store(path))}
        self.apply_one(path, "Tim Cheese")
        after = {r["raw"]["name"]: dict(r["raw"]) for r in parse_site_php(read_store(path))}
        for name, row in before.items():
            if name == "Tim Cheese":
                continue
            self.assertEqual(after.get(name), row, f"{name} modifié à tort")

    # -- garanties conservées --------------------------------------------
    def test_direct_action_takes_a_backup(self):
        path = store_file()
        out, _ = self.apply_one(path, "Tim Cheese")
        self.assertTrue(Path(out["backup_path"]).is_file())

    def test_direct_action_is_idempotent_on_double_click(self):
        """Un double clic rejoue la même clé : aucune seconde écriture."""
        path = store_file()
        svc, _ = service(path)
        plan = prepare_plan(comparison_against(path),
                            source_hash=content_hash(read_store(path)))
        confirmation = {"approved": True, "plan_hash": plan["plan_hash"]}
        first = svc.apply(plan, ["Tim Cheese"], confirmation=confirmation,
                          idempotency_key="direct:dbl")
        content_after_first = read_store(path)
        second = svc.apply(plan, ["Tim Cheese"], confirmation=confirmation,
                           idempotency_key="direct:dbl")
        self.assertTrue(first["ok"])
        self.assertTrue(second.get("replayed"), "le rejeu doit être signalé")
        self.assertEqual(read_store(path), content_after_first,
                         "le second clic ne doit rien réécrire")

    def test_direct_action_still_requires_confirmation(self):
        """Aucune écriture ne part d'un clic sans confirmation explicite."""
        path = store_file()
        svc, _ = service(path)
        plan = prepare_plan(comparison_against(path),
                            source_hash=content_hash(read_store(path)))
        untouched = read_store(path)
        out = svc.apply(plan, ["Tim Cheese"], confirmation={"approved": False})
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "CONFIRMATION_REQUIRED_USER")
        self.assertEqual(read_store(path), untouched)

    def test_batch_still_works_alongside_direct_actions(self):
        """L'action directe n'a pas remplacé le lot multi-entrées."""
        path = store_file()
        svc, _ = service(path)
        plan = prepare_plan(comparison_against(path),
                            source_hash=content_hash(read_store(path)))
        out = svc.apply(plan, ["Tim Cheese", "Nouveau Brainrot"],
                        confirmation={"approved": True, "plan_hash": plan["plan_hash"]},
                        idempotency_key="batch:two")
        self.assertTrue(out["ok"], out)
        self.assertEqual(len(out["results"]), 2)
        comp = comparison_against(path)
        self.assertEqual(status_of(comp, "Tim Cheese"), "NO_CHANGE")
        self.assertEqual(status_of(comp, "Nouveau Brainrot"), "NO_CHANGE")


if __name__ == "__main__":
    unittest.main()
