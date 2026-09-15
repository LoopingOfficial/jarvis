"""Agent BLOG / EDITORIAL : RESEARCH → VERIFY → WRITE → SEO → PUBLISH →
VERIFY_PUBLIC → DISCORD_NOTIFY.

Chaque étape publie son état réel (``blog.stage``) avec les outils qu'elle a
réellement employés : Spatial OS V5 n'affiche donc jamais une animation qui ne
correspond à rien. Une étape qui n'a rien fait ne s'annonce pas.

Le pipeline ne décide jamais seul de forcer une publication : c'est
``BlogPublisherService.prepare`` qui rend le verdict, et ``FULL_AUTO`` n'y
ajoute aucune permission — il se contente de ne pas s'arrêter quand le verdict
est READY.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from . import blog_discord, blog_sources
from .blog_grounding import Fact
from .blog_publisher import (AUTO_MODES, INTENT_DRAFT, INTENT_PREVIEW, INTENT_PUBLISH,
                             MODE_DRAFT_THEN_VALIDATE, MODE_DRAFTS_ONLY, MODE_FULL_AUTO,
                             UPDATE_EXISTING, Article, BlogPublisherService, PrepareResult)
from .blog_site import (DISCORD_NOTIFY, FAILED, NEEDS_REVIEW, PUBLISH, PUBLISHED_VERIFIED,
                        RESEARCH, SEO, SITE_BASE, VERIFY, VERIFY_PUBLIC, WRITE,
                        BlogSite, BlogSiteError)

BUILD_ID = "JARVIS_BLOG_PUBLISHER_V1"

AGENT_ID = "blog"

# Libellés affichés par Spatial OS, dans l'ordre réel du pipeline.
STAGE_LABELS = {
    RESEARCH: "Recherche du sujet",
    VERIFY: "Vérification des informations",
    WRITE: "Rédaction de l'article",
    SEO: "Optimisation SEO",
    PUBLISH: "Publication",
    VERIFY_PUBLIC: "Vérification du site",
    DISCORD_NOTIFY: "Notification Discord",
}

# Réglages Settings > Blog (section « blog » de SettingsStore).
SETTINGS_SECTION = "blog"
SETTING_AUTO_ENABLED = "auto_editorial"
SETTING_AUTO_MODE = "auto_mode"
SETTING_AUTO_RESEARCH = "auto_research"
SETTING_AUTO_DRAFTS = "auto_drafts"
SETTING_AUTO_PUBLISH = "auto_publish"
SETTING_AUTO_DISCORD = "auto_discord"

DEFAULT_SETTINGS = {
    SETTING_AUTO_ENABLED: True,
    SETTING_AUTO_MODE: MODE_DRAFTS_ONLY,
    SETTING_AUTO_RESEARCH: True,
    SETTING_AUTO_DRAFTS: True,
    SETTING_AUTO_PUBLISH: False,
    SETTING_AUTO_DISCORD: False,
    blog_discord.SETTING_CHANNEL: "",
}

# Un réglage peut arriver en booléen (SettingsStore) ou en texte (API REST).
_TRUE = {"1", "true", "on", "yes", "oui"}


def _is_on(value: Any) -> bool:
    return value is True or str(value).strip().lower() in _TRUE


@dataclass
class StageTrace:
    stage: str
    label: str
    status: str
    tools: list[str] = field(default_factory=list)
    detail: str = ""
    started_at: float = 0.0
    ended_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"stage": self.stage, "label": self.label, "status": self.status,
                "tools": list(self.tools), "detail": self.detail,
                "started_at": self.started_at, "ended_at": self.ended_at,
                "duration": round(max(0.0, self.ended_at - self.started_at), 3)}


# --- Compréhension des commandes manuelles ---------------------------------
_PUBLISH_RE = re.compile(r"\b(publie|publier|publication|met[s]?\s+en\s+ligne)\b", re.I)
_DRAFT_RE = re.compile(r"\b(brouillon|draft|ne\s+(?:le\s+)?publie\s+pas|sans\s+publier|"
                       r"pr[ée]pare)\b", re.I)
_PREVIEW_RE = re.compile(r"\b(pr[ée]visualise|aper[çc]u|preview|montre[- ]moi)\b", re.I)
_DISCORD_RE = re.compile(r"\b(discord|pr[ée]viens|pr[ée]venir|notifie|annonce)\b", re.I)
_SUGGEST_RE = re.compile(r"\b(quels?\s+articles?|que\s+pourrais|id[ée]es?\s+d'article|"
                         r"sujets?\s+(?:du\s+jour|possibles?))\b", re.I)


def parse_command(text: str) -> dict[str, Any]:
    """Traduit une phrase en intention. Le doute penche vers le brouillon.

    « Prépare un article mais ne le publie pas » contient « publie » : la
    négation doit donc l'emporter, sinon la commande la plus prudente
    déclencherait la publication.
    """
    raw = text or ""
    if _SUGGEST_RE.search(raw):
        return {"intent": "SUGGEST", "notify_discord": False, "topic": ""}

    draft = bool(_DRAFT_RE.search(raw))
    publish = bool(_PUBLISH_RE.search(raw)) and not draft
    preview = bool(_PREVIEW_RE.search(raw)) and not publish

    intent = INTENT_PUBLISH if publish else (INTENT_PREVIEW if preview else INTENT_DRAFT)
    topic = re.sub(r"^.*?\b(?:sur|à propos de|concernant)\b\s*", "", raw, flags=re.I).strip(" .?!")
    return {"intent": intent,
            "notify_discord": bool(_DISCORD_RE.search(raw)) and intent == INTENT_PUBLISH,
            "topic": topic if topic and topic.lower() != raw.lower() else raw.strip(" .?!")}


class EditorialAgent:
    """Pipeline éditorial complet, du sujet à la notification."""

    def __init__(self, core: Any, *, connector_id: str = "ssh") -> None:
        self._core = core
        self.site = BlogSite(core, connector_id=connector_id, agent=AGENT_ID)
        self.publisher = BlogPublisherService(core, site=self.site)
        self.trace: list[StageTrace] = []

    # --- État visible -------------------------------------------------------
    def _stage(self, stage: str, *, status: str = "running", tools: list[str] | None = None,
               detail: str = "") -> StageTrace:
        entry = StageTrace(stage=stage, label=STAGE_LABELS.get(stage, stage), status=status,
                           tools=list(tools or []), detail=detail, started_at=time.time())
        self.trace.append(entry)
        try:
            self._core.agents.set_state(AGENT_ID, "active", action=entry.label)
        except Exception:
            pass
        self._emit(entry)
        return entry

    def _done(self, entry: StageTrace, *, status: str = "ok", detail: str = "",
              tools: list[str] | None = None) -> None:
        entry.status = status
        entry.ended_at = time.time()
        if detail:
            entry.detail = detail
        if tools:
            entry.tools = sorted(set(entry.tools) | set(tools))
        self._emit(entry)

    def _emit(self, entry: StageTrace) -> None:
        try:
            self._core.events.emit("blog.stage", {"agent": AGENT_ID, **entry.to_dict()})
        except Exception:
            pass

    def _finish(self, status: str) -> None:
        try:
            self._core.agents.set_state(
                AGENT_ID, "done" if status in {PUBLISHED_VERIFIED, "DRAFT"} else "standby",
                action=status)
        except Exception:
            pass

    # --- Étapes -------------------------------------------------------------
    def research(self, topic: str, *, web: bool = True) -> list[Fact]:
        """Collecte des faits réels. Internes d'abord, web ensuite."""
        entry = self._stage(RESEARCH)
        facts: list[Fact] = []
        tools: list[str] = []

        names = _mentioned_names(topic)
        internal = blog_sources.brainrot_facts(names) if names else []
        if internal:
            tools.append("site.api/brainrots")
        facts += internal

        if re.search(r"\bcodes?\b", topic, re.I):
            codes = blog_sources.code_facts()
            tools.append("site.api/codes")
            facts += codes

        memory = blog_sources.memory_facts(self._core, topic)
        if memory:
            tools.append("memory.search")
        facts += memory

        if web:
            web_found = blog_sources.web_facts(self._core, topic, agent=AGENT_ID)
            if web_found:
                tools.append("web.search")
            facts += web_found

        self._done(entry, status="ok" if facts else "empty",
                   detail=f"{len(facts)} fait(s) collecté(s).", tools=tools)
        return facts

    def verify(self, facts: list[Fact], *, kind: str = "news") -> dict[str, Any]:
        """Contrôle de la matière AVANT rédaction : sans source, pas d'article."""
        entry = self._stage(VERIFY, tools=["blog_grounding"])
        distinct = {(f.source_type, f.source_ref or f.url) for f in facts}
        internal = sum(1 for f in facts if f.is_internal)
        official = sum(1 for f in facts if f.is_official)
        minimum = 2 if kind == "news" else 1
        ok = len(distinct) >= minimum
        report = {"sources": len(distinct), "internal": internal, "official": official,
                  "sufficient": ok, "minimum": minimum}
        self._done(entry, status="ok" if ok else "insufficient",
                   detail=f"{len(distinct)} source(s) distincte(s), minimum {minimum}.")
        return report

    def pick_cover_image(self, facts: list[Fact], topic: str) -> str:
        """Image : officielle/existante d'abord. Jamais une image trouvée au hasard.

        Ordre appliqué : image de la fiche Brainrot citée (stockée sur le site),
        puis bannière existante du site. Aucune image externe non vérifiée
        n'est retenue ici ; la génération reste une décision explicite.
        """
        for fact in facts:
            if fact.source_type == "brainrots" and fact.url:
                slug = fact.url.rstrip("/").rsplit("/", 1)[-1]
                for row in blog_sources.fetch_brainrot_catalog():
                    if row.get("slug") == slug and row.get("image_url"):
                        return str(row["image_url"])
        names = _mentioned_names(topic)
        if names:
            folded = {n.lower() for n in names}
            for row in blog_sources.fetch_brainrot_catalog():
                if str(row.get("name", "")).lower() in folded and row.get("image_url"):
                    return str(row["image_url"])
        return ""

    def compose(self, *, title: str, content: str, facts: list[Fact], topic: str = "",
                category: str = "actualites", kind: str = "news",
                cover_image: str = "", tags: list[str] | None = None) -> Article:
        """Assemble l'article. Le texte vient de l'appelant (modèle ou humain) ;
        les faits, l'image et les métadonnées viennent du site."""
        entry = self._stage(WRITE)
        article = Article(title=title.strip(), content=content, facts=list(facts),
                          category=category, kind=kind, tags=list(tags or []),
                          cover_image=cover_image or self.pick_cover_image(facts, topic or title))
        self._done(entry, detail=f"{len(content.split())} mots, {len(facts)} source(s).",
                   tools=["site.api/brainrots"] if article.cover_image else [])
        return article

    def prepare(self, article: Article, *, allow_duplicate: bool = False) -> PrepareResult:
        entry = self._stage(SEO, tools=["blog_seo", "InternalLinkResolver", "blog_grounding"])
        prepared = self.publisher.prepare(article, allow_duplicate=allow_duplicate)
        self._done(entry, status="ok" if prepared.publishable else "needs_review",
                   detail="; ".join(prepared.blockers)[:300] or
                          f"SEO PASS, grounding PASS, {len(article.internal_links)} lien(s) interne(s).")
        return prepared

    # --- Parcours complets --------------------------------------------------
    def draft(self, prepared: PrepareResult) -> dict[str, Any]:
        """Crée le brouillon RÉEL sur le site. Ne publie jamais."""
        entry = self._stage(PUBLISH, tools=["ssh.write_file", "php://jarvis_blog_bridge"],
                            detail="Création du brouillon.")
        try:
            result = self.publisher.create_draft(prepared)
        except BlogSiteError as exc:
            self._done(entry, status="failed", detail=str(exc)[:300])
            self._finish(FAILED)
            return {"ok": False, "status": FAILED, "error": str(exc)}
        self._done(entry, detail=f"Brouillon {result['post'].get('id')} « {result['post'].get('slug')} ».")
        self._finish("DRAFT")
        return {"ok": True, "status": "DRAFT", **result}

    def publish(self, prepared: PrepareResult, *, notify: bool = False,
                force: bool = False) -> dict[str, Any]:
        """Publie, vérifie, puis — seulement alors — notifie Discord."""
        entry = self._stage(PUBLISH, tools=["php://jarvis_blog_bridge"])
        result = self.publisher.publish(prepared, force=force)
        self._done(entry, status="ok" if result.get("ok") else "failed",
                   detail=result.get("status", ""))

        check = self._stage(VERIFY_PUBLIC, tools=["http.get"])
        verification = result.get("verification") or {}
        self._done(check, status="ok" if verification.get("passed") else "failed",
                   detail=str(verification.get("reason") or verification.get("url") or ""))

        if not result.get("ok"):
            self._finish(result.get("status", FAILED))
            return {**result, "discord": {"sent": False, "blocked": True,
                                          "reason": "Publication non vérifiée."}}

        discord_result: dict[str, Any] = {"sent": False, "blocked": True,
                                          "reason": "Notification non demandée."}
        if notify:
            stage = self._stage(DISCORD_NOTIFY, tools=["discord.send_embed"])
            try:
                discord_result = blog_discord.notify(
                    self._core, self.publisher, result["article"],
                    verification=verification)
                self._done(stage, status="ok" if discord_result.get("sent") else "blocked",
                           detail=str(discord_result.get("reason") or
                                      discord_result.get("message_id") or ""))
            except blog_discord.DiscordNotifyBlocked as exc:
                discord_result = {"sent": False, "blocked": True, "reason": str(exc)}
                self._done(stage, status="failed", detail=str(exc)[:300])

        self._finish(PUBLISHED_VERIFIED)
        return {**result, "discord": discord_result}

    def run(self, command: str, *, title: str = "", content: str = "",
            facts: list[Fact] | None = None, category: str = "actualites",
            kind: str = "news", allow_duplicate: bool = False,
            force: bool = False) -> dict[str, Any]:
        """Parcours complet piloté par une commande en français."""
        self.trace = []
        parsed = parse_command(command)
        topic = title or parsed["topic"]

        if parsed["intent"] == "SUGGEST":
            return {"intent": "SUGGEST", "topics": self.suggest_topics(),
                    "trace": [t.to_dict() for t in self.trace]}

        collected = list(facts) if facts is not None else self.research(topic)
        verification = self.verify(collected, kind=kind)

        if not content:
            self._finish(NEEDS_REVIEW)
            return {"intent": parsed["intent"], "status": NEEDS_REVIEW,
                    "reason": "Aucun texte d'article fourni : la rédaction revient au modèle.",
                    "facts": [f.to_dict() for f in collected], "research": verification,
                    "trace": [t.to_dict() for t in self.trace]}

        article = self.compose(title=topic if not title else title, content=content,
                               facts=collected, topic=topic, category=category, kind=kind)

        duplicate = self.publisher.find_duplicate(article.title)
        if duplicate and not allow_duplicate:
            self._finish(NEEDS_REVIEW)
            return {"intent": parsed["intent"], "status": UPDATE_EXISTING,
                    "duplicate": duplicate, "article": article.to_dict(),
                    "reason": f"« {duplicate['title']} » traite déjà ce sujet.",
                    "trace": [t.to_dict() for t in self.trace]}

        prepared = self.prepare(article, allow_duplicate=allow_duplicate)

        if parsed["intent"] == INTENT_PUBLISH:
            outcome = self.publish(prepared, notify=parsed["notify_discord"], force=force)
        elif parsed["intent"] == INTENT_PREVIEW:
            self._finish("PREVIEW")
            outcome = {"ok": True, "status": "PREVIEW", "article": prepared.article.to_dict(),
                       "blockers": prepared.blockers}
        else:
            outcome = self.draft(prepared)

        return {"intent": parsed["intent"], "prepare": prepared.to_dict(),
                "trace": [t.to_dict() for t in self.trace], **outcome}

    # --- Sujets -------------------------------------------------------------
    def suggest_topics(self, *, limit: int = 6) -> list[dict[str, Any]]:
        """Sujets adossés à des données réelles. Aucune actualité inventée.

        Un sujet n'apparaît que s'il repose sur quelque chose d'observable :
        des codes actifs qui existent, des fiches Brainrot récemment mises à
        jour, un guide Wiki non encore couvert par un article.
        """
        proposals: list[dict[str, Any]] = []

        codes = blog_sources.fetch_codes(active_only=True)
        if codes:
            proposals.append({
                "topic": "Les codes Steal the Brainrot actuellement actifs",
                "why": f"{len(codes)} code(s) actif(s) dans la base du site.",
                "source": "site.api/codes", "kind": "news", "facts": len(codes)})

        catalog = blog_sources.fetch_brainrot_catalog()
        recent = sorted([c for c in catalog if c.get("updated_at")],
                        key=lambda c: str(c["updated_at"]), reverse=True)[:5]
        if recent:
            names = ", ".join(str(c["name"]) for c in recent[:3])
            proposals.append({
                "topic": f"Les dernières fiches Brainrot mises à jour ({names})",
                "why": f"{len(recent)} fiche(s) modifiée(s) récemment dans la base du site.",
                "source": "site.api/brainrots", "kind": "guide", "facts": len(recent)})

        try:
            existing = {str(p.get("title") or "").lower() for p in self.site.posts(limit=50)}
        except BlogSiteError:
            existing = set()
        # WIKI_ROUTES contient des alias (« rebirth » et « rebirths » pointent
        # sur la même page) : on propose une page une seule fois.
        from .blog_seo import WIKI_ROUTES
        seen_paths: set[str] = set()
        for term, path in list(WIKI_ROUTES.items()):
            if len(proposals) >= limit:
                break
            if path in seen_paths or any(term in title for title in existing):
                continue
            seen_paths.add(path)
            proposals.append({"topic": f"Guide : {term}", "why": "Guide Wiki existant, "
                              "aucun article du blog ne le reprend.",
                              "source": f"site{path}", "kind": "guide", "facts": 1})
        return proposals[:limit]

    # --- Mode automatique ---------------------------------------------------
    def auto_settings(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, default in DEFAULT_SETTINGS.items():
            try:
                out[key] = self._core.settings.get(SETTINGS_SECTION, key, default)
            except Exception:
                out[key] = default
        return out

    def auto_mode(self) -> str:
        mode = str(self.auto_settings().get(SETTING_AUTO_MODE) or MODE_DRAFTS_ONLY)
        return mode if mode in AUTO_MODES else MODE_DRAFTS_ONLY

    def auto_allows_publish(self, prepared: PrepareResult) -> tuple[bool, str]:
        """FULL AUTO ne publie que sur un dossier entièrement propre."""
        settings = self.auto_settings()
        if not _is_on(settings.get(SETTING_AUTO_ENABLED)):
            return False, "AUTO_EDITORIAL désactivé."
        if self.auto_mode() != MODE_FULL_AUTO:
            return False, f"Mode {self.auto_mode()} : publication manuelle requise."
        if not _is_on(settings.get(SETTING_AUTO_PUBLISH)):
            return False, "Publication automatique désactivée."
        if not prepared.grounding.passed:
            return False, "Grounding FAIL."
        if not prepared.seo.passed:
            return False, "SEO FAIL."
        if prepared.duplicate:
            return False, "Doublon détecté."
        if not prepared.publishable:
            return False, "; ".join(prepared.blockers)[:200] or NEEDS_REVIEW
        return True, "READY"

    def auto_cycle(self, *, limit: int = 1) -> dict[str, Any]:
        """Cycle automatique sécurisé: recherche, vérification puis brouillon.

        La publication et Discord restent volontairement impossibles dans ce
        chemin, même si un réglage ancien demande un mode plus permissif.
        """
        settings = self.auto_settings()
        if not _is_on(settings.get(SETTING_AUTO_ENABLED)):
            return {"status": "DISABLED", "created": [], "needs_review": []}
        if not _is_on(settings.get(SETTING_AUTO_RESEARCH)):
            return {"status": "RESEARCH_DISABLED", "created": [], "needs_review": []}

        created: list[dict[str, Any]] = []
        needs_review: list[dict[str, Any]] = []
        for proposal in self.suggest_topics(limit=max(1, int(limit))):
            topic = str(proposal.get("topic") or "").strip()
            if not topic:
                continue
            facts = self.research(topic)
            verification = self.verify(facts, kind=str(proposal.get("kind") or "news"))
            if not verification["sufficient"]:
                needs_review.append({"topic": topic, "status": NEEDS_REVIEW,
                                     "reason": "Grounding insuffisant.", "research": verification})
                continue
            duplicate = self.publisher.find_duplicate(topic)
            if duplicate:
                continue
            content = self._draft_with_model(topic, facts)
            if not content:
                needs_review.append({"topic": topic, "status": NEEDS_REVIEW,
                                     "reason": "Rédaction automatique indisponible."})
                continue
            article = self.compose(title=topic, content=content, facts=facts, topic=topic,
                                   kind=str(proposal.get("kind") or "news"))
            prepared = self.prepare(article)
            if not prepared.publishable:
                needs_review.append({"topic": topic, "status": NEEDS_REVIEW,
                                     "reason": "; ".join(prepared.blockers)[:300]})
                continue
            if not _is_on(settings.get(SETTING_AUTO_DRAFTS)):
                needs_review.append({"topic": topic, "status": NEEDS_REVIEW,
                                     "reason": "Création automatique de brouillons désactivée."})
                continue
            created.append(self.draft(prepared))
        return {"status": "OK", "created": created, "needs_review": needs_review,
                "published": [], "discord": []}

    def _draft_with_model(self, topic: str, facts: list[Fact]) -> str:
        """Demande un HTML factuel au modèle sans lui transmettre de secret."""
        llm = getattr(self._core, "llm", None)
        if llm is None:
            return ""
        evidence = "\n".join(f"- {fact.text[:500]} ({fact.url})" for fact in facts[:8])
        try:
            response = llm.chat([
                {"role": "system", "content": (
                    "Rédige un brouillon HTML en français à partir uniquement des faits fournis. "
                    "N'invente aucune date, statistique ou affirmation. Utilise au moins deux h2."
                )},
                {"role": "user", "content": f"Sujet: {topic}\nFaits:\n{evidence}"},
            ], role="default", max_tokens=1200)
            text = str(getattr(response, "text", "") or getattr(response, "content", "") or "").strip()
            return text if "<p" in text or "<h2" in text else ""
        except Exception:
            return ""


def _mentioned_names(text: str) -> list[str]:
    """Noms propres plausibles mentionnés dans une commande."""
    candidates = re.findall(r"\b[A-ZÀ-Ý][\wÀ-ÿ'-]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ'-]+)*\b", text or "")
    stop = {"Steal", "Brainrot", "Fortnite", "Discord", "Publie", "Rédige", "Prépare",
            "Les", "Le", "La", "Un", "Une", "Des"}
    out: list[str] = []
    for candidate in candidates:
        cleaned = " ".join(w for w in candidate.split() if w not in stop).strip()
        if len(cleaned) > 2:
            out.append(cleaned)
    return out
