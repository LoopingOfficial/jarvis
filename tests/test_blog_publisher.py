"""JARVIS_BLOG_PUBLISHER_V1 — T1..T15.

Les tests ne touchent jamais le site réel : ``FakeSite`` rejoue le contrat du
pont PHP (mêmes clés, mêmes statuts) et ``verify_public`` est piloté pour
couvrir les cas d'échec, qui sont l'essentiel du sujet : ce build se juge
surtout sur ce qu'il REFUSE de faire.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis import blog_discord
from jarvis.blog_grounding import Fact, validate_article
from jarvis.blog_editorial import parse_command
from jarvis.blog_publisher import (INTENT_DRAFT, INTENT_PUBLISH, UPDATE_EXISTING,
                                   Article, BlogPublisherService)
from jarvis.blog_seo import InternalLinkResolver, validate_seo
from jarvis.blog_site import DRAFT, FAILED, NEEDS_REVIEW, PUBLISHED, PUBLISHED_VERIFIED, slugify
from jarvis.db import Database


CATALOG = [
    {"id": 1, "name": "Kitsunara", "slug": "kitsunara",
     "image_url": "https://brainrot-fortnite.com/img/brainrots/kitsunara.png",
     "detail_url": "https://brainrot-fortnite.com/brainrots/kitsunara"},
    {"id": 2, "name": "Skibidi Toilet", "slug": "skibidi-toilet",
     "image_url": "https://brainrot-fortnite.com/img/brainrots/skidibi.png",
     "detail_url": "https://brainrot-fortnite.com/brainrots/skibidi-toilet"},
]

FACTS = [
    Fact(text="Kitsunara — rareté SSS, revenu/s 350000000", source_type="brainrots",
         source_ref="brainrots#1", url="https://brainrot-fortnite.com/brainrots/kitsunara",
         values=["Kitsunara", "SSS", "350000000"]),
    Fact(text="Mise à jour annoncée le 12 septembre 2026", source_type="web",
         source_ref="fortnite.com", url="https://www.fortnite.com/news",
         values=["12 septembre 2026"]),
]

CONTENT = (
    "<p>Kitsunara arrive enfin dans Steal the Brainrot, et il change la donne pour "
    "les joueurs qui visent le haut du classement.</p>"
    "<h2>Ce que change Kitsunara</h2>"
    "<p>Avec une rareté SSS, la fiche affiche un revenu confortable. Les Traits et le "
    "Rebirth restent les leviers les plus rentables autour de lui.</p>"
    "<p>Le reste du roster ne bouge pas, mais la hiérarchie du milieu de tableau se "
    "resserre nettement depuis la mise à jour du 12 septembre 2026.</p>"
    "<h2>Comment en profiter rapidement</h2>"
    "<ul><li>Sécuriser les machines les plus rentables avant de monter</li>"
    "<li>Garder une réserve pour encaisser un vol</li>"
    "<li>Surveiller les codes pour les bonus ponctuels</li></ul>"
    "<p>Rien d'obligatoire ici : la progression reste jouable sans dépenser, simplement "
    "plus lente si vous ignorez complètement les bonus disponibles sur le jeu.</p>"
    "<p>Concrètement, la marche à suivre ne change pas énormément pour un joueur "
    "installé : on sécurise ce qui rapporte, on évite de laisser traîner une somme "
    "importante sans protection, et on garde un œil sur ce que font les autres "
    "joueurs de la partie avant de lancer une montée coûteuse qui peut mal tourner.</p>"
    "<p>Les joueurs les plus avancés verront surtout un intérêt de confort : le rythme "
    "de progression se tend un peu, sans rendre les stratégies existantes obsolètes. "
    "Il n'y a donc aucune urgence à tout réorganiser dès aujourd'hui, et mieux vaut "
    "prendre le temps de regarder ce que donnent les premières parties avant de "
    "changer une installation qui fonctionne déjà correctement au quotidien.</p>"
    "<h2>Ce qu'il faut retenir</h2>"
    "<p>Kitsunara vaut l'investissement si vous jouez régulièrement. Pour les autres, "
    "les Brainrots déjà en place suffisent largement à tenir le rythme actuel du jeu "
    "sans se forcer à courir après la nouveauté du moment. La vraie différence se "
    "joue sur la régularité plus que sur la possession d'une pièce rare, et c'est "
    "sans doute le point le plus utile à garder en tête pour la suite de la saison.</p>"
)


class FakeSite:
    """Contrat identique au pont PHP réel, en mémoire."""

    def __init__(self) -> None:
        self.rows: dict[int, dict] = {}
        self._next = 1
        self.public_ok = True
        self.publish_raises = False
        self.verify_calls = 0

    def category_id(self, wanted, *, default_slug="actualites"):
        return 1

    def free_slug(self, base, *, ignore_id=0):
        root = slugify(base)
        candidate, n = root, 2
        while any(r["slug"] == candidate and r["id"] != ignore_id for r in self.rows.values()):
            candidate, n = f"{root}-{n}", n + 1
        return candidate

    def get(self, *, slug="", post_id=0):
        for row in self.rows.values():
            if (slug and row["slug"] == slug) or (post_id and row["id"] == post_id):
                return dict(row)
        return None

    def posts(self, limit=50):
        return [dict(r) for r in sorted(self.rows.values(), key=lambda r: -r["id"])][:limit]

    def search(self, terms):
        out = []
        for row in self.rows.values():
            haystack = f"{row['title']} {row['slug']}".lower()
            if any(str(t).lower() in haystack for t in terms):
                out.append(dict(row))
        return out

    def save(self, fields, *, post_id=0):
        if post_id:
            self.rows[post_id].update(fields)
            return {"action": "updated", "post": dict(self.rows[post_id])}
        row = {"id": self._next, **fields}
        self.rows[self._next] = row
        self._next += 1
        return {"action": "created", "post": dict(row)}

    def publish(self, post_id, *, published_at=""):
        if self.publish_raises:
            from jarvis.blog_site import BlogSiteError
            raise BlogSiteError("MySQL indisponible")
        self.rows[post_id]["status"] = PUBLISHED
        self.rows[post_id]["published_at"] = "2026-09-15 12:00:00"
        return dict(self.rows[post_id])

    def verify_public(self, slug, *, expected_title="", timeout=25):
        self.verify_calls += 1
        url = f"https://brainrot-fortnite.com/blog/{slug}"
        if self.public_ok:
            return {"url": url, "http_status": 200, "title_found": True, "canonical_ok": True,
                    "canonical": url, "passed": True, "reason": ""}
        return {"url": url, "http_status": 404, "title_found": False, "canonical_ok": False,
                "canonical": "", "passed": False, "reason": "HTTP 404"}


class FakeSettings:
    """Même signature que SettingsStore : get(section, key, default)."""

    def __init__(self, values=None):
        self._values = {"blog": dict(values or {})}

    def get(self, section, key, default=""):
        return self._values.get(section, {}).get(key, default)


class FakeCore:
    def __init__(self, db):
        self.db = db
        self.settings = FakeSettings({blog_discord.SETTING_CHANNEL: "123456789"})


def _publisher(site=None):
    tmp = tempfile.TemporaryDirectory()
    db = Database(Path(tmp.name) / "blog.db")
    core = FakeCore(db)
    service = BlogPublisherService(core, site=site or FakeSite())
    service._resolver = InternalLinkResolver(CATALOG)
    service._tmp = tmp
    return service


def _article(**overrides):
    data = {"title": "Kitsunara débarque dans Steal the Brainrot", "content": CONTENT,
            "facts": list(FACTS), "cover_image": CATALOG[0]["image_url"]}
    data.update(overrides)
    return Article(**data)


class BlogPublisherTests(unittest.TestCase):

    # T1 -------------------------------------------------------------------
    def test_t1_creation_draft(self):
        service = _publisher()
        prepared = service.prepare(_article())
        self.assertEqual(prepared.status, "READY", prepared.blockers)
        result = service.create_draft(prepared)
        self.assertEqual(result["post"]["status"], DRAFT)
        self.assertTrue(result["post"]["id"])
        events = service.events(article_id=result["post"]["id"])
        self.assertEqual(events[0]["kind"], "draft")

    # T2 -------------------------------------------------------------------
    def test_t2_publication(self):
        service = _publisher()
        result = service.publish(service.prepare(_article()))
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], PUBLISHED_VERIFIED)

    # T3 -------------------------------------------------------------------
    def test_t3_verify_public_runs_after_publish(self):
        site = FakeSite()
        service = _publisher(site)
        service.publish(service.prepare(_article()))
        self.assertGreaterEqual(site.verify_calls, 1)

    # T4 -------------------------------------------------------------------
    def test_t4_discord_notification_sent_after_verification(self):
        service = _publisher()
        result = service.publish(service.prepare(_article()))
        sent = {}

        def fake_send(core, channel, payload):
            sent.update({"channel": channel, "payload": payload})
            return {"message_id": "999", "channel": channel}

        blog_discord._send, original = fake_send, blog_discord._send
        try:
            outcome = blog_discord.notify(service._core, service, result["article"],
                                          verification=result["verification"])
        finally:
            blog_discord._send = original
        self.assertTrue(outcome["sent"])
        self.assertEqual(outcome["message_id"], "999")
        self.assertIn("Lire l'article", sent["payload"]["description"])

    # T5 -------------------------------------------------------------------
    def test_t5_discord_never_before_verification(self):
        service = _publisher()
        prepared = service.prepare(_article())
        draft = service.create_draft(prepared)
        outcome = blog_discord.notify(service._core, service, draft["article"],
                                      verification=None)
        self.assertFalse(outcome["sent"])
        self.assertTrue(outcome["blocked"])

        failed = {"passed": False, "reason": "HTTP 404"}
        outcome = blog_discord.notify(service._core, service, draft["article"],
                                      verification=failed)
        self.assertFalse(outcome["sent"])
        self.assertEqual(outcome["reason"], "HTTP 404")

    # T6 -------------------------------------------------------------------
    def test_t6_duplicate_article_detected(self):
        service = _publisher()
        service.create_draft(service.prepare(_article()))
        duplicate = service.find_duplicate("Kitsunara débarque dans Steal the Brainrot")
        self.assertIsNotNone(duplicate)
        self.assertEqual(duplicate["recommendation"], UPDATE_EXISTING)

        second = service.prepare(_article())
        self.assertEqual(second.status, NEEDS_REVIEW)
        self.assertTrue(any(UPDATE_EXISTING in b for b in second.blockers))

    # T7 -------------------------------------------------------------------
    def test_t7_update_existing_keeps_slug(self):
        site = FakeSite()
        service = _publisher(site)
        published = service.publish(service.prepare(_article()))
        post_id = published["article"]["post_id"]
        slug = published["article"]["slug"]

        updated = _article(title="Kitsunara débarque dans Steal the Brainrot",
                           content=CONTENT + "<p>Mise à jour des informations disponibles.</p>")
        prepared = service.prepare(updated, allow_duplicate=True)
        result = service.update_existing(post_id, prepared)
        self.assertEqual(result["post"]["slug"], slug)
        self.assertTrue(result["was_published"])
        self.assertEqual(site.rows[post_id]["status"], PUBLISHED)

    # T8 -------------------------------------------------------------------
    def test_t8_grounding_fail_blocks_auto_publish(self):
        service = _publisher()
        invented = CONTENT.replace("12 septembre 2026", "3 octobre 2031")
        prepared = service.prepare(_article(content=invented))
        self.assertEqual(prepared.grounding.status, "FAIL")
        self.assertFalse(prepared.publishable)
        result = service.publish(prepared)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], NEEDS_REVIEW)

    # T9 -------------------------------------------------------------------
    def test_t9_insufficient_sources_needs_review(self):
        report = validate_article(CONTENT, [FACTS[0]], kind="news")
        self.assertEqual(report.status, "FAIL")
        self.assertTrue(any("insuffisantes" in r for r in report.reasons))

    # T10 ------------------------------------------------------------------
    def test_t10_discord_duplicate_blocked(self):
        service = _publisher()
        result = service.publish(service.prepare(_article()))
        blog_discord._send, original = (lambda c, ch, p: {"message_id": "1", "channel": ch}), \
            blog_discord._send
        try:
            first = blog_discord.notify(service._core, service, result["article"],
                                        verification=result["verification"])
            second = blog_discord.notify(service._core, service, result["article"],
                                         verification=result["verification"])
        finally:
            blog_discord._send = original
        self.assertTrue(first["sent"])
        self.assertFalse(second["sent"])
        self.assertEqual(second["reason"], "Notification déjà envoyée.")

    # T11 ------------------------------------------------------------------
    def test_t11_internal_links(self):
        resolver = InternalLinkResolver(CATALOG)
        linked, applied = resolver.apply(CONTENT)
        paths = {link.path for link in applied}
        self.assertIn("/brainrots/kitsunara", paths)
        self.assertIn('<a href="/brainrots/kitsunara">', linked)
        self.assertLessEqual(len(applied), 6)
        # Une cible n'est liée qu'une fois, même si elle apparaît plusieurs fois.
        self.assertEqual(linked.count('href="/brainrots/kitsunara"'), 1)

    def test_t11b_unknown_mention_is_never_linked(self):
        resolver = InternalLinkResolver(CATALOG)
        linked, applied = resolver.apply("<p>Le Brainrot Fantomikaze est arrivé.</p>")
        self.assertEqual(applied, [])
        self.assertNotIn("<a", linked)

    # T12 ------------------------------------------------------------------
    def test_t12_seo(self):
        service = _publisher()
        prepared = service.prepare(_article())
        self.assertEqual(prepared.seo.status, "PASS", prepared.seo.issues)
        article = prepared.article
        self.assertTrue(article.meta_title and article.meta_desc and article.excerpt)
        self.assertEqual(article.slug, slugify(article.slug))

        short = validate_seo({"title": "T", "slug": "t", "content": "<p>court</p>",
                              "meta_title": "T", "meta_desc": "x", "excerpt": "x",
                              "category_id": 1})
        self.assertEqual(short.status, "FAIL")

    # T13 ------------------------------------------------------------------
    def test_t13_image_required_and_from_site(self):
        service = _publisher()
        prepared = service.prepare(_article(cover_image=""))
        self.assertFalse(prepared.publishable)
        self.assertTrue(any("Image" in b for b in prepared.blockers))

        ok = service.prepare(_article())
        self.assertTrue(ok.article.cover_image.startswith("https://brainrot-fortnite.com/img/"))

    # T14 ------------------------------------------------------------------
    def test_t14_full_auto_requires_clean_verdict(self):
        from jarvis.blog_editorial import (MODE_FULL_AUTO, SETTING_AUTO_ENABLED,
                                           SETTING_AUTO_MODE, SETTING_AUTO_PUBLISH,
                                           EditorialAgent)
        service = _publisher()
        agent = EditorialAgent.__new__(EditorialAgent)
        agent._core = service._core
        agent.site = service.site
        agent.publisher = service
        agent.trace = []

        clean = service.prepare(_article())
        service._core.settings = FakeSettings({SETTING_AUTO_ENABLED: "0"})
        self.assertFalse(agent.auto_allows_publish(clean)[0])

        service._core.settings = FakeSettings({SETTING_AUTO_ENABLED: "1",
                                               SETTING_AUTO_MODE: MODE_FULL_AUTO,
                                               SETTING_AUTO_PUBLISH: "1"})
        allowed, reason = agent.auto_allows_publish(clean)
        self.assertTrue(allowed, reason)

        dirty = service.prepare(_article(content=CONTENT.replace("12 septembre 2026", "9 mai 2044"),
                                         title="Autre sujet Kitsunara inédit"))
        self.assertFalse(agent.auto_allows_publish(dirty)[0])

    # T15 ------------------------------------------------------------------
    def test_t15_publish_failure_blocks_discord(self):
        site = FakeSite()
        site.publish_raises = True
        service = _publisher(site)
        result = service.publish(service.prepare(_article()))
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], FAILED)

        outcome = blog_discord.notify(service._core, service, result["article"],
                                      verification=result.get("verification"))
        self.assertFalse(outcome["sent"])

    def test_t15b_published_but_unreachable_blocks_discord(self):
        site = FakeSite()
        site.public_ok = False
        service = _publisher(site)
        result = service.publish(service.prepare(_article()))
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], FAILED)
        self.assertFalse(result["verification"]["passed"])
        events = service.events()
        self.assertTrue(any(e["kind"] == "verify" and e["status"] == FAILED for e in events))


class CommandParsingTests(unittest.TestCase):

    def test_publish_intent(self):
        parsed = parse_command("Publie un article sur les nouveaux codes")
        self.assertEqual(parsed["intent"], INTENT_PUBLISH)
        self.assertFalse(parsed["notify_discord"])

    def test_publish_with_discord(self):
        parsed = parse_command("Publie l'article et préviens Discord.")
        self.assertEqual(parsed["intent"], INTENT_PUBLISH)
        self.assertTrue(parsed["notify_discord"])

    def test_negation_wins_over_publish(self):
        parsed = parse_command("Prépare un article mais ne le publie pas.")
        self.assertEqual(parsed["intent"], INTENT_DRAFT)
        self.assertFalse(parsed["notify_discord"])

    def test_draft_default(self):
        self.assertEqual(parse_command("Rédige un article sur Eternal BoxRot")["intent"],
                         INTENT_DRAFT)

    def test_suggestion(self):
        self.assertEqual(parse_command("Quels articles pourrais-je publier aujourd'hui ?")["intent"],
                         "SUGGEST")


class GroundingTests(unittest.TestCase):

    def test_invented_code_is_flagged(self):
        report = validate_article("<p>Utilisez le code FREEGEMS2026 dès maintenant.</p>", FACTS)
        self.assertTrue(any(c["kind"] == "code" for c in report.claims))

    def test_real_code_passes(self):
        facts = FACTS + [Fact(text="Code RELEASE50 actif", source_type="codes",
                              source_ref="codes#RELEASE50", values=["RELEASE50"])]
        claims = validate_article("<p>Le code RELEASE50 est actif.</p>", facts).claims
        self.assertFalse(any(c["claim"] == "RELEASE50" for c in claims))

    def test_invented_statistic_is_flagged(self):
        report = validate_article("<p>Le revenu grimpe à 987654321 par seconde.</p>", FACTS)
        self.assertTrue(any(c["kind"] == "nombre" for c in report.claims))


if __name__ == "__main__":
    unittest.main(verbosity=2)
