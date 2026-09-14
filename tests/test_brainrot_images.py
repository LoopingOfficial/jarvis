"""BUILD JARVIS_BRAINROT_IMAGES_V1 : mapping image fiable, sans invention."""
import unittest

from jarvis.brainrot_compare import (SITE_PUBLIC_BASE, attach_images, build_image_index,
                                     compare, parse_site_php)
from tests.test_brainrot_compare import SHEET_MERGES, SHEET_ROWS, SITE_PHP, workbook

# Catalogue calqué sur la réponse réelle de ajax/brainrots-ajax.php.
CATALOG = {"ok": True, "items": [
    {"id": 1, "name": "Fishini Bossini", "slug": "fishini-bossini",
     "img_path": "/img/brainrots/fishini.png"},
    {"id": 2, "name": "Tim Cheese", "slug": "tim-cheese",
     "img_path": "img/brainrots/tim-cheese-main-2026.png"},
    # Le site renvoie ce placeholder quand le fichier manque : ce n'est pas
    # un visuel de Brainrot, il ne doit jamais être associé.
    {"id": 3, "name": "Bone Throne", "slug": "bone-throne",
     "img_path": "/img/placeholder.svg"},
    # Nom proche mais Brainrot différent : aucun rapprochement flou permis.
    {"id": 4, "name": "Fishini Mechinini", "slug": "fishini-mechinini",
     "img_path": "/img/brainrots/fishini-mechinini.png"},
    {"id": 5, "name": "14", "slug": "14", "img_path": "/img/brainrots/14.png"},
]}


def compared():
    site = {"ok": True, "path": "data/wiki/x.php", "records": parse_site_php(SITE_PHP),
            "fields": []}
    return compare(workbook(SHEET_ROWS, SHEET_MERGES), site)


def by_name(result, name):
    return next(e for e in result["entries"] if e["identity"] == name)


class IndexTests(unittest.TestCase):
    def test_paths_become_absolute_urls(self):
        index = build_image_index(CATALOG)
        self.assertEqual(index["slug"]["fishini-bossini"],
                         SITE_PUBLIC_BASE + "/img/brainrots/fishini.png")
        # Chemin sans slash initial : une seule barre dans l'URL finale.
        self.assertEqual(index["slug"]["tim-cheese"],
                         SITE_PUBLIC_BASE + "/img/brainrots/tim-cheese-main-2026.png")

    def test_placeholder_is_never_indexed(self):
        index = build_image_index(CATALOG)
        self.assertNotIn("bone-throne", index["slug"])
        self.assertFalse(any("placeholder" in u for u in index["slug"].values()))

    def test_absolute_urls_are_kept_as_is(self):
        index = build_image_index({"ok": True, "items": [
            {"id": 9, "name": "X", "slug": "x", "img_path": "https://cdn.example/x.png"}]})
        self.assertEqual(index["slug"]["x"], "https://cdn.example/x.png")

    def test_a_key_claimed_by_two_visuals_is_dropped(self):
        index = build_image_index({"ok": True, "items": [
            {"id": 1, "name": "Doublon", "slug": "doublon", "img_path": "/img/brainrots/a.png"},
            {"id": 2, "name": "Doublon", "slug": "autre", "img_path": "/img/brainrots/b.png"}]})
        # Le slug reste distinct, mais le nom normalisé est ambigu : retiré.
        self.assertNotIn("doublon", index["name"])
        self.assertIn("doublon", index["slug"])


class AttachTests(unittest.TestCase):
    def test_exact_slug_gets_the_real_image(self):
        result = attach_images(compared(), build_image_index(CATALOG))
        entry = by_name(result, "Fishini Bossini")
        self.assertEqual(entry["image_url"], SITE_PUBLIC_BASE + "/img/brainrots/fishini.png")
        self.assertEqual(entry["image_match"], "slug")

    def test_no_fuzzy_match_ever(self):
        # « Nouveau Brainrot » n'existe pas au catalogue ; « Fishini Mechinini »
        # ne doit jamais lui être attribué par ressemblance.
        result = attach_images(compared(), build_image_index(CATALOG))
        entry = by_name(result, "Nouveau Brainrot")
        self.assertEqual(entry["image_url"], "")
        self.assertEqual(entry["image_match"], "")

    def test_numeric_name_matches_its_canonical_slug(self):
        result = attach_images(compared(), build_image_index(CATALOG))
        entry = next(e for e in result["entries"] if e["slug"] == "14")
        self.assertEqual(entry["image_url"], SITE_PUBLIC_BASE + "/img/brainrots/14.png")

    def test_every_entry_carries_the_field(self):
        result = attach_images(compared(), build_image_index(CATALOG))
        for entry in result["entries"]:
            self.assertIn("image_url", entry)
            self.assertIsInstance(entry["image_url"], str)

    def test_counters_report_real_coverage(self):
        result = attach_images(compared(), build_image_index(CATALOG))
        matched = sum(1 for e in result["entries"] if e["image_url"])
        self.assertEqual(result["images"]["matched"], matched)
        self.assertEqual(result["images"]["catalog_size"], 4)

    def test_unreachable_catalog_leaves_the_comparison_intact(self):
        result = compared()
        before = result["counts"].copy()
        result = attach_images(result, build_image_index({"ok": False, "items": []}))
        self.assertEqual(result["counts"], before)
        self.assertTrue(all(e["image_url"] == "" for e in result["entries"]))

    def test_comparison_result_is_untouched_otherwise(self):
        plain = compared()
        withimg = attach_images(compared(), build_image_index(CATALOG))
        self.assertEqual(plain["counts"], withimg["counts"])
        self.assertFalse(withimg["write_performed"])
        for a, b in zip(plain["entries"], withimg["entries"]):
            self.assertEqual(a["status"], b["status"])
            self.assertEqual(a["changed_fields"], b["changed_fields"])


if __name__ == "__main__":
    unittest.main()
