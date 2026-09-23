"""BlogPublisherService : prepare → validate → create_draft → publish → verify_public.

Règle centrale du build, non contournable :

    Aucune notification Discord n'est possible tant que ``verify_public`` n'a
    pas renvoyé un PASS sur l'URL publique réelle. Un brouillon ne notifie
    jamais ; une publication échouée ne notifie jamais.

Le journal ``blog_publication_events`` (base JARVIS) porte l'idempotence : il
enregistre ce qui a été réellement publié et réellement notifié.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from .blog_grounding import Fact, GroundingReport, validate_article
from .blog_seo import (InternalLinkResolver, SeoReport, build_excerpt,
                       build_meta_description, build_meta_title, validate_seo)
from .blog_site import (DRAFT, FAILED, NEEDS_REVIEW, PUBLISHED, PUBLISHED_VERIFIED,
                        BlogSite, BlogSiteError, public_url, slugify)

BUILD_ID = "JARVIS_BLOG_PUBLISHER_V1"

# Modes d'automatisation exposés dans Settings > Blog.
MODE_DRAFTS_ONLY = "AUTO_DRAFTS"          # MODE 1
MODE_DRAFT_THEN_VALIDATE = "AUTO_REVIEW"  # MODE 2
MODE_FULL_AUTO = "FULL_AUTO"              # MODE 3
AUTO_MODES = (MODE_DRAFTS_ONLY, MODE_DRAFT_THEN_VALIDATE, MODE_FULL_AUTO)

# Intentions comprises des commandes manuelles.
INTENT_DRAFT, INTENT_PREVIEW, INTENT_PUBLISH = "DRAFT", "PREVIEW", "PUBLISH"

UPDATE_EXISTING = "UPDATE_EXISTING"

# Similarité de titre au-delà de laquelle on considère le sujet déjà traité.
DUPLICATE_THRESHOLD = 0.72


@dataclass
class Article:
    """Article en cours de fabrication. `post_id` ≠ 0 dès qu'il existe sur le site."""
    title: str
    content: str
    facts: list[Fact] = field(default_factory=list)
    slug: str = ""
    excerpt: str = ""
    meta_title: str = ""
    meta_desc: str = ""
    category: str = "actualites"
    category_id: int | None = None
    cover_image: str = ""
    kind: str = "news"
    tags: list[str] = field(default_factory=list)
    post_id: int = 0
    internal_links: list[dict[str, str]] = field(default_factory=list)

    @property
    def content_hash(self) -> str:
        blob = f"{self.title}\n{self.content}".encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    @property
    def url(self) -> str:
        return public_url(self.slug) if self.slug else ""

    def site_fields(self, *, status: str) -> dict[str, Any]:
        """Champs écrits dans ``blog_posts``. Strictement le schéma du site."""
        fields: dict[str, Any] = {
            "title": self.title, "slug": self.slug, "status": status,
            "excerpt": self.excerpt, "content": self.content,
            "meta_title": self.meta_title, "meta_desc": self.meta_desc,
            "cover_image": self.cover_image or "",
        }
        if self.category_id:
            fields["category_id"] = int(self.category_id)
        return fields

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "slug": self.slug, "excerpt": self.excerpt,
                "meta_title": self.meta_title, "meta_desc": self.meta_desc,
                "category": self.category, "category_id": self.category_id,
                "cover_image": self.cover_image, "kind": self.kind,
                "tags": list(self.tags), "post_id": self.post_id,
                "content": self.content, "url": self.url,
                "content_hash": self.content_hash,
                "internal_links": list(self.internal_links),
                "sources": [f.to_dict() for f in self.facts]}


@dataclass
class PrepareResult:
    article: Article
    grounding: GroundingReport
    seo: SeoReport
    duplicate: dict[str, Any] | None
    status: str
    blockers: list[str] = field(default_factory=list)

    @property
    def publishable(self) -> bool:
        """Vrai seulement si une publication AUTOMATIQUE est légitime."""
        return self.status == "READY"

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "article": self.article.to_dict(),
                "grounding": self.grounding.to_dict(), "seo": self.seo.to_dict(),
                "duplicate": self.duplicate, "blockers": list(self.blockers)}


def _similarity(left: str, right: str) -> float:
    """Jaccard sur les mots significatifs. Déterministe, sans dépendance."""
    stop = {"les", "la", "le", "des", "de", "du", "un", "une", "et", "sur", "pour",
            "dans", "avec", "au", "aux", "en", "the", "a", "of"}
    def tokens(text: str) -> set[str]:
        words = {w for w in slugify(text).split("-") if len(w) > 2 and w not in stop}
        return words
    left_set, right_set = tokens(left), tokens(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


class BlogPublisherService:
    """Seul chemin d'écriture du blog. Rien ne publie en dehors d'ici."""

    def __init__(self, core: Any, *, site: BlogSite | None = None,
                 connector_id: str = "") -> None:
        self._core = core
        self.site = site or BlogSite(core, connector_id=connector_id)
        self._resolver: InternalLinkResolver | None = None

    # --- Journal ------------------------------------------------------------
    def record_event(self, *, article_id: int, slug: str, status: str, kind: str = "publish",
                     title: str = "", url: str = "", sources: list[dict[str, Any]] | None = None,
                     content_hash: str = "", verification: dict[str, Any] | None = None,
                     discord_message_id: str = "", discord_channel_id: str = "",
                     detail: str = "") -> int:
        now = time.time()
        self._core.db.execute(
            "INSERT INTO blog_publication_events(article_id, slug, title, url, status, kind,"
            " published_at, discord_message_id, discord_channel_id, sources, content_hash,"
            " verification, detail, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (int(article_id), slug, title, url, status, kind, now, discord_message_id,
             discord_channel_id, json.dumps(sources or [], ensure_ascii=False), content_hash,
             json.dumps(verification or {}, ensure_ascii=False), detail[:500], now))
        row = self._core.db.one("SELECT last_insert_rowid() AS id")
        return int(row["id"]) if row else 0

    def events(self, *, article_id: int = 0, limit: int = 50) -> list[dict[str, Any]]:
        # `db.query` renvoie des `sqlite3.Row` : on les convertit ici une fois
        # pour toutes, pour que les appelants manipulent de vrais dictionnaires.
        if article_id:
            rows = self._core.db.query(
                "SELECT * FROM blog_publication_events WHERE article_id=? ORDER BY id DESC LIMIT ?",
                (int(article_id), int(limit)))
        else:
            rows = self._core.db.query(
                "SELECT * FROM blog_publication_events ORDER BY id DESC LIMIT ?", (int(limit),))
        return [dict(row) for row in rows]

    def already_notified(self, article_id: int, *, content_hash: str = "") -> dict[str, Any] | None:
        """Idempotence Discord : une notification émise ne se rejoue jamais.

        Une mise à jour d'article ré-ouvre le droit de notifier uniquement si
        le contenu a réellement changé (hash différent), ce qui est le cas
        prévu par « ARTICLE MIS À JOUR ».
        """
        rows = [dict(r) for r in self._core.db.query(
            "SELECT * FROM blog_publication_events WHERE article_id=? AND kind='discord'"
            " AND status='SENT' ORDER BY id DESC", (int(article_id),))]
        for row in rows:
            if not content_hash or str(row.get("content_hash") or "") == content_hash:
                return row
        return None

    # --- Maillage -----------------------------------------------------------
    def resolver(self, *, refresh: bool = False) -> InternalLinkResolver:
        if self._resolver is None or refresh:
            self._resolver = InternalLinkResolver(self._brainrot_catalog())
        return self._resolver

    def _brainrot_catalog(self) -> list[dict[str, Any]]:
        """Catalogue réel des fiches Brainrot (table ``brainrots`` du site)."""
        try:
            from .blog_sources import fetch_brainrot_catalog
            return fetch_brainrot_catalog(self._core)
        except Exception:
            return []

    # --- Étapes -------------------------------------------------------------
    def find_duplicate(self, title: str, *, ignore_id: int = 0) -> dict[str, Any] | None:
        """Cherche un article existant traitant déjà exactement le sujet."""
        terms = [w for w in slugify(title).split("-") if len(w) > 3][:8]
        best: dict[str, Any] | None = None
        best_score = 0.0
        for match in self.site.search(terms):
            if int(match.get("id") or 0) == int(ignore_id or 0):
                continue
            score = _similarity(title, str(match.get("title") or ""))
            if score > best_score:
                best, best_score = dict(match), score
        if best and best_score >= DUPLICATE_THRESHOLD:
            best["similarity"] = round(best_score, 3)
            best["recommendation"] = UPDATE_EXISTING
            best["url"] = public_url(str(best.get("slug") or ""))
            return best
        return None

    def prepare(self, article: Article, *, allow_duplicate: bool = False) -> PrepareResult:
        """Complète, maille et contrôle l'article. N'écrit rien sur le site."""
        blockers: list[str] = []

        if not article.slug:
            article.slug = self.site.free_slug(article.title, ignore_id=article.post_id)
        else:
            article.slug = self.site.free_slug(article.slug, ignore_id=article.post_id)

        if article.category_id is None:
            article.category_id = self.site.category_id(article.category)

        linked, applied = self.resolver().apply(article.content)
        article.content = linked
        article.internal_links = [{"label": l.label, "path": l.path, "kind": l.kind}
                                  for l in applied]

        if not article.excerpt:
            article.excerpt = build_excerpt(article.content)
        if not article.meta_title:
            article.meta_title = build_meta_title(article.title)
        if not article.meta_desc:
            article.meta_desc = build_meta_description(article.content, article.excerpt)

        grounding = validate_article(article.content, article.facts, kind=article.kind)
        seo = validate_seo(article.to_dict())
        duplicate = self.find_duplicate(article.title, ignore_id=article.post_id)

        if not grounding.passed:
            blockers.extend(grounding.reasons)
        if not seo.passed:
            blockers.extend(seo.issues)
        if duplicate and not allow_duplicate:
            blockers.append(
                f"Sujet déjà traité par « {duplicate['title']} » "
                f"(similarité {duplicate['similarity']}). Recommandation : {UPDATE_EXISTING}.")
        if not article.cover_image:
            blockers.append("Image de couverture absente.")

        status = "READY" if not blockers else NEEDS_REVIEW
        return PrepareResult(article=article, grounding=grounding, seo=seo,
                             duplicate=duplicate, status=status, blockers=blockers)

    def validate(self, prepared: PrepareResult) -> bool:
        """Contrôle final avant écriture. Ne juge que ce qui est vérifiable."""
        article = prepared.article
        required = {"titre": article.title, "slug": article.slug,
                    "contenu": article.content, "meta_title": article.meta_title,
                    "meta_desc": article.meta_desc, "extrait": article.excerpt}
        missing = [name for name, value in required.items() if not str(value).strip()]
        if missing:
            raise BlogSiteError(f"Article incomplet : {', '.join(missing)}.")
        if not article.facts:
            raise BlogSiteError("Article sans aucune source : écriture refusée.")
        return True

    def create_draft(self, prepared: PrepareResult) -> dict[str, Any]:
        """Écrit le brouillon dans ``blog_posts`` du site. Jamais publié ici."""
        self.validate(prepared)
        article = prepared.article
        saved = self.site.save(article.site_fields(status=DRAFT), post_id=article.post_id)
        post = saved["post"]
        article.post_id = int(post.get("id") or 0)
        article.slug = str(post.get("slug") or article.slug)
        self.record_event(article_id=article.post_id, slug=article.slug, status=DRAFT.upper(),
                          kind="draft", title=article.title, url=article.url,
                          sources=[f.to_dict() for f in article.facts],
                          content_hash=article.content_hash,
                          detail=f"{saved['action']} ({prepared.status})")
        return {"action": saved["action"], "post": post, "article": article.to_dict(),
                "status": prepared.status, "blockers": prepared.blockers}

    def publish(self, prepared: PrepareResult, *, force: bool = False) -> dict[str, Any]:
        """Publie puis VÉRIFIE. Le succès n'est déclaré qu'après vérification."""
        article = prepared.article
        if not prepared.publishable and not force:
            self.record_event(article_id=article.post_id, slug=article.slug,
                              status=NEEDS_REVIEW, kind="publish", title=article.title,
                              url=article.url, content_hash=article.content_hash,
                              detail="; ".join(prepared.blockers)[:500])
            return {"ok": False, "status": NEEDS_REVIEW, "blockers": prepared.blockers,
                    "verification": None, "article": article.to_dict()}

        if not article.post_id:
            self.create_draft(prepared)

        try:
            post = self.site.publish(article.post_id)
        except BlogSiteError as exc:
            self.record_event(article_id=article.post_id, slug=article.slug, status=FAILED,
                              kind="publish", title=article.title, url=article.url,
                              content_hash=article.content_hash, detail=str(exc)[:400])
            return {"ok": False, "status": FAILED, "error": str(exc),
                    "verification": None, "article": article.to_dict()}

        article.slug = str(post.get("slug") or article.slug)
        verification = self.verify_public(article)

        if not verification.get("passed"):
            self.record_event(article_id=article.post_id, slug=article.slug, status=FAILED,
                              kind="verify", title=article.title, url=article.url,
                              content_hash=article.content_hash, verification=verification,
                              detail=str(verification.get("reason") or "")[:400])
            return {"ok": False, "status": FAILED, "verification": verification,
                    "error": verification.get("reason") or "Vérification publique échouée.",
                    "article": article.to_dict()}

        self.record_event(article_id=article.post_id, slug=article.slug,
                          status=PUBLISHED_VERIFIED, kind="publish", title=article.title,
                          url=article.url, sources=[f.to_dict() for f in article.facts],
                          content_hash=article.content_hash, verification=verification)
        return {"ok": True, "status": PUBLISHED_VERIFIED, "verification": verification,
                "post": post, "article": article.to_dict()}

    def verify_public(self, article: Article) -> dict[str, Any]:
        return self.site.verify_public(article.slug, expected_title=article.title)

    # --- Mise à jour d'un article existant ----------------------------------
    def update_existing(self, post_id: int, prepared: PrepareResult, *,
                        keep_slug: bool = True) -> dict[str, Any]:
        """Met à jour un article en conservant slug, SEO et historique."""
        existing = self.site.get(post_id=post_id)
        if not existing:
            raise BlogSiteError(f"Article {post_id} introuvable sur le site.")
        article = prepared.article
        article.post_id = int(existing["id"])
        if keep_slug:
            article.slug = str(existing.get("slug") or article.slug)
        was_published = str(existing.get("status") or "").lower() in {
            PUBLISHED, "publish", "public", "online", "active"}
        status = PUBLISHED if was_published else DRAFT
        saved = self.site.save(article.site_fields(status=status), post_id=article.post_id)
        self.record_event(article_id=article.post_id, slug=article.slug, status="UPDATED",
                          kind="update", title=article.title, url=article.url,
                          sources=[f.to_dict() for f in article.facts],
                          content_hash=article.content_hash,
                          detail=f"status={status}")
        verification = self.verify_public(article) if was_published else None
        return {"ok": True, "action": saved["action"], "post": saved["post"],
                "was_published": was_published, "verification": verification,
                "article": article.to_dict()}
