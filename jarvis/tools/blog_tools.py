"""Outils éditoriaux : recherche, brouillon, publication, vérification, Discord.

Les risques sont posés d'après l'effet réel sur le site :
``blog.draft`` écrit dans la base du site (SAFE_WRITE), ``blog.publish`` rend
du contenu public (SENSITIVE, confirmation), ``blog.notify_discord`` écrit dans
un serveur communautaire (SENSITIVE).
"""
from __future__ import annotations

from typing import Any

from ..permissions import READ_ONLY, SAFE_WRITE, SENSITIVE
from .base import ToolContext, ToolResult, registry

AGENTS = ("jarvis", "blog")


def _agent(ctx: ToolContext):
    """Agent éditorial du cœur, créé à la demande (import paresseux)."""
    core = ctx.core
    agent = getattr(core, "blog_editorial", None)
    if agent is None:
        from ..blog_editorial import EditorialAgent
        agent = EditorialAgent(core)
        core.blog_editorial = agent
    return agent


def _facts_from(payload: Any) -> list:
    from ..blog_grounding import Fact
    out = []
    for entry in payload or []:
        if isinstance(entry, dict) and str(entry.get("text") or "").strip():
            out.append(Fact.from_dict(entry))
    return out


# --- Recherche --------------------------------------------------------------
def _research(ctx: ToolContext) -> ToolResult:
    topic = str(ctx.arguments.get("topic") or "").strip()
    if not topic:
        return ToolResult(False, "Sujet manquant.")
    agent = _agent(ctx)
    facts = agent.research(topic, web=bool(ctx.arguments.get("web", True)))
    report = agent.verify(facts, kind=str(ctx.arguments.get("kind") or "news"))
    lines = [f"- [{f.source_type}] {f.text[:160]}" + (f" ({f.url})" if f.url else "")
             for f in facts[:20]]
    summary = (f"{len(facts)} fait(s), {report['sources']} source(s) distincte(s) "
               f"(minimum {report['minimum']}).")
    if not report["sufficient"]:
        summary += " Sources insuffisantes : ne pas publier automatiquement (NEEDS_REVIEW)."
    return ToolResult(True, summary + ("\n" + "\n".join(lines) if lines else ""),
                      data={"facts": [f.to_dict() for f in facts], "research": report,
                            "trace": [t.to_dict() for t in agent.trace]})


registry.add(
    id="blog.research", name="Rechercher les faits d'un article", category="Blog",
    description="Collecte les faits réels d'un sujet : données du site (Brainrots, codes), "
                "mémoire JARVIS et recherche web. N'invente rien ; renvoie les sources.",
    handler=_research, risk=READ_ONLY, agents=AGENTS,
    input_schema={"type": "object", "properties": {
        "topic": {"type": "string"},
        "kind": {"type": "string", "enum": ["news", "guide"]},
        "web": {"type": "boolean", "description": "Inclure la recherche web externe."}},
        "required": ["topic"]},
)


# --- Sujets -----------------------------------------------------------------
def _topics(ctx: ToolContext) -> ToolResult:
    proposals = _agent(ctx).suggest_topics(limit=int(ctx.arguments.get("limit") or 6))
    if not proposals:
        return ToolResult(True, "Aucun sujet adossé à des données réelles aujourd'hui.",
                          data={"topics": []})
    lines = [f"- {p['topic']} — {p['why']}" for p in proposals]
    return ToolResult(True, "\n".join(lines), data={"topics": proposals})


registry.add(
    id="blog.topics", name="Sujets d'article possibles", category="Blog",
    description="Propose des sujets fondés sur des données réelles du site (codes actifs, "
                "fiches Brainrot récentes, guides Wiki non couverts).",
    handler=_topics, risk=READ_ONLY, agents=AGENTS,
    input_schema={"type": "object", "properties": {"limit": {"type": "integer"}}},
)


# --- Préparation ------------------------------------------------------------
def _prepare_article(ctx: ToolContext):
    agent = _agent(ctx)
    args = ctx.arguments
    article = agent.compose(
        title=str(args.get("title") or "").strip(),
        content=str(args.get("content") or ""),
        facts=_facts_from(args.get("facts")),
        topic=str(args.get("topic") or args.get("title") or ""),
        category=str(args.get("category") or "actualites"),
        kind=str(args.get("kind") or "news"),
        cover_image=str(args.get("cover_image") or ""),
        tags=list(args.get("tags") or []))
    prepared = agent.prepare(article, allow_duplicate=bool(args.get("allow_duplicate")))
    return agent, prepared


def _preview(ctx: ToolContext) -> ToolResult:
    if not str(ctx.arguments.get("content") or "").strip():
        return ToolResult(False, "Contenu de l'article manquant.")
    agent, prepared = _prepare_article(ctx)
    article = prepared.article
    verdict = "READY" if prepared.publishable else prepared.status
    lines = [f"Statut : {verdict}",
             f"Titre : {article.title}",
             f"Slug : {article.slug} → {article.url}",
             f"SEO : {prepared.seo.status} ({prepared.seo.metrics})",
             f"Grounding : {prepared.grounding.status} "
             f"({prepared.grounding.source_count} source(s))",
             f"Liens internes : {len(article.internal_links)}",
             f"Image : {article.cover_image or 'aucune'}"]
    if prepared.blockers:
        lines.append("Bloquants : " + " | ".join(prepared.blockers))
    return ToolResult(True, "\n".join(lines), data=prepared.to_dict())


registry.add(
    id="blog.preview", name="Prévisualiser un article", category="Blog",
    description="Complète l'article (slug, SEO, extrait, maillage interne, image), contrôle le "
                "grounding et les doublons. N'écrit rien sur le site.",
    handler=_preview, risk=READ_ONLY, agents=AGENTS,
    input_schema={"type": "object", "properties": {
        "title": {"type": "string"}, "content": {"type": "string", "description": "HTML simple."},
        "facts": {"type": "array", "description": "Faits de blog.research.",
                  "items": {"type": "object"}},
        "category": {"type": "string"}, "kind": {"type": "string", "enum": ["news", "guide"]},
        "cover_image": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}},
        "allow_duplicate": {"type": "boolean"}},
        "required": ["title", "content"]},
)


# --- Brouillon --------------------------------------------------------------
def _draft(ctx: ToolContext) -> ToolResult:
    if not str(ctx.arguments.get("content") or "").strip():
        return ToolResult(False, "Contenu de l'article manquant.")
    agent, prepared = _prepare_article(ctx)
    result = agent.draft(prepared)
    if not result.get("ok"):
        return ToolResult(False, str(result.get("error") or "Création du brouillon impossible."),
                          risk=SAFE_WRITE, data=result)
    post = result["post"]
    message = (f"Brouillon créé sur le site : #{post.get('id')} « {post.get('title')} » "
               f"(slug {post.get('slug')}, statut {post.get('status')}).\n"
               f"URL une fois publié : {prepared.article.url}")
    if prepared.blockers:
        message += "\nÀ revoir avant publication : " + " | ".join(prepared.blockers)
    return ToolResult(True, message, risk=SAFE_WRITE, data=result)


registry.add(
    id="blog.draft", name="Créer un brouillon", category="Blog",
    description="Écrit l'article en BROUILLON dans le blog réel du site (statut draft). "
                "Ne publie pas et ne notifie jamais Discord.",
    handler=_draft, risk=SAFE_WRITE, permissions=("write",), agents=AGENTS,
    dangerous_hint="Un brouillon sera créé dans la base du site.",
    input_schema={"type": "object", "properties": {
        "title": {"type": "string"}, "content": {"type": "string"},
        "facts": {"type": "array", "items": {"type": "object"}},
        "category": {"type": "string"}, "kind": {"type": "string", "enum": ["news", "guide"]},
        "cover_image": {"type": "string"}, "allow_duplicate": {"type": "boolean"}},
        "required": ["title", "content"]},
)


# --- Publication ------------------------------------------------------------
def _publish(ctx: ToolContext) -> ToolResult:
    if not str(ctx.arguments.get("content") or "").strip():
        return ToolResult(False, "Contenu de l'article manquant.")
    agent, prepared = _prepare_article(ctx)
    result = agent.publish(prepared, notify=bool(ctx.arguments.get("notify_discord")),
                           force=bool(ctx.arguments.get("force")))
    if not result.get("ok"):
        reason = result.get("error") or " | ".join(result.get("blockers") or []) or result.get("status")
        return ToolResult(False, f"Publication refusée ({result.get('status')}) : {reason}",
                          risk=SENSITIVE, data=result)
    article = result["article"]
    discord = result.get("discord") or {}
    message = (f"Article publié et vérifié.\n\nTitre :\n{article['title']}\n\n"
               f"URL :\n{article['url']}\n\nDiscord :\n"
               + ("notification envoyée." if discord.get("sent")
                  else f"non envoyée ({discord.get('reason')})."))
    return ToolResult(True, message, risk=SENSITIVE, data=result)


registry.add(
    id="blog.publish", name="Publier un article", category="Blog",
    description="Publie l'article sur le site, VÉRIFIE l'URL publique (HTTP 200, titre, "
                "canonical) et ne notifie Discord qu'après cette vérification.",
    handler=_publish, risk=SENSITIVE, permissions=("write",), agents=AGENTS,
    confirmation_policy="always",
    dangerous_hint="L'article deviendra public sur brainrot-fortnite.com.",
    input_schema={"type": "object", "properties": {
        "title": {"type": "string"}, "content": {"type": "string"},
        "facts": {"type": "array", "items": {"type": "object"}},
        "category": {"type": "string"}, "kind": {"type": "string", "enum": ["news", "guide"]},
        "cover_image": {"type": "string"}, "notify_discord": {"type": "boolean"},
        "allow_duplicate": {"type": "boolean"},
        "force": {"type": "boolean", "description": "Passer outre un verdict NEEDS_REVIEW."}},
        "required": ["title", "content"]},
)


# --- Vérification publique --------------------------------------------------
def _verify(ctx: ToolContext) -> ToolResult:
    slug = str(ctx.arguments.get("slug") or "").strip()
    if not slug:
        return ToolResult(False, "Slug manquant.")
    agent = _agent(ctx)
    post = agent.site.get(slug=slug)
    report = agent.site.verify_public(
        slug, expected_title=str(ctx.arguments.get("title") or (post or {}).get("title") or ""))
    verdict = "PUBLISHED_VERIFIED" if report["passed"] else "ÉCHEC"
    return ToolResult(report["passed"],
                      f"{verdict} — {report['url']} (HTTP {report['http_status']}, "
                      f"titre {'OK' if report['title_found'] else 'absent'}, "
                      f"canonical {'OK' if report['canonical_ok'] else report['canonical'] or 'absent'})"
                      + (f"\n{report['reason']}" if report["reason"] else ""),
                      data={"verification": report, "post": post})


registry.add(
    id="blog.verify_public", name="Vérifier un article en ligne", category="Blog",
    description="Vérifie qu'un article est réellement accessible : HTTP 200, titre présent, "
                "canonical cohérent. C'est la condition d'une notification Discord.",
    handler=_verify, risk=READ_ONLY, agents=AGENTS,
    input_schema={"type": "object", "properties": {
        "slug": {"type": "string"}, "title": {"type": "string"}}, "required": ["slug"]},
)


# --- Discord ----------------------------------------------------------------
def _notify(ctx: ToolContext) -> ToolResult:
    from .. import blog_discord

    slug = str(ctx.arguments.get("slug") or "").strip()
    if not slug:
        return ToolResult(False, "Slug manquant.")
    agent = _agent(ctx)
    post = agent.site.get(slug=slug)
    if not post:
        return ToolResult(False, f"Aucun article « {slug} » sur le site.", risk=SENSITIVE)

    status = str(post.get("status") or "").lower()
    if status not in {"published", "publish", "public", "online", "active"}:
        return ToolResult(False, f"Article en statut « {status} » : un brouillon ne se notifie "
                                 f"jamais.", risk=SENSITIVE)

    report = agent.site.verify_public(slug, expected_title=str(post.get("title") or ""))
    if not report["passed"]:
        return ToolResult(False, f"Vérification publique échouée : {report['reason']}. "
                                 f"Aucune notification envoyée.", risk=SENSITIVE,
                          data={"verification": report})

    from ..blog_publisher import Article
    article = Article(title=str(post.get("title") or ""), content=str(post.get("content") or ""),
                      slug=slug, excerpt=str(post.get("excerpt") or ""),
                      cover_image=str(post.get("cover_image") or ""),
                      post_id=int(post.get("id") or 0)).to_dict()
    try:
        result = blog_discord.notify(ctx.core, agent.publisher, article,
                                     verification=report,
                                     updated=bool(ctx.arguments.get("updated")),
                                     channel=str(ctx.arguments.get("channel_id") or ""))
    except blog_discord.DiscordNotifyBlocked as exc:
        return ToolResult(False, str(exc), risk=SENSITIVE)
    if not result.get("sent"):
        return ToolResult(False, f"Notification non envoyée : {result.get('reason')}",
                          risk=SENSITIVE, data=result)
    return ToolResult(True, f"Notification Discord envoyée (message {result['message_id']}).",
                      risk=SENSITIVE, data=result)


registry.add(
    id="blog.notify_discord", name="Annoncer un article sur Discord", category="Blog",
    description="Publie l'embed d'annonce dans le salon BLOG_DISCORD_CHANNEL_ID. Refuse si "
                "l'article n'est pas publié ET vérifié, ou s'il a déjà été annoncé.",
    handler=_notify, risk=SENSITIVE, permissions=("write",), agents=AGENTS,
    confirmation_policy="always",
    dangerous_hint="Un message sera publié dans le serveur Discord.",
    input_schema={"type": "object", "properties": {
        "slug": {"type": "string"}, "updated": {"type": "boolean"},
        "channel_id": {"type": "string"}}, "required": ["slug"]},
)


# --- Tableau de bord --------------------------------------------------------
def _dashboard(ctx: ToolContext) -> ToolResult:
    agent = _agent(ctx)
    posts = agent.site.posts(limit=int(ctx.arguments.get("limit") or 50))
    events = agent.publisher.events(limit=100)
    notified = {int(e["article_id"]) for e in events
                if e["kind"] == "discord" and e["status"] == "SENT"}
    failures = [e for e in events if e["status"] == "FAILED"]

    buckets: dict[str, list[dict[str, Any]]] = {"drafts": [], "published": [], "other": []}
    for post in posts:
        status = str(post.get("status") or "").lower()
        key = "published" if status in {"published", "publish", "public", "online", "active"} \
            else ("drafts" if status == "draft" else "other")
        buckets[key].append({
            "id": post.get("id"), "title": post.get("title"), "slug": post.get("slug"),
            "status": post.get("status"), "published_at": post.get("published_at"),
            "url": f"https://brainrot-fortnite.com/blog/{post.get('slug')}",
            "discord": "envoyée" if int(post.get("id") or 0) in notified else "—",
        })

    lines = [f"Brouillons : {len(buckets['drafts'])}",
             f"Publiés : {len(buckets['published'])}",
             f"Échecs journalisés : {len(failures)}"]
    for item in buckets["drafts"][:10]:
        lines.append(f"- [brouillon] #{item['id']} {item['title']} → {item['url']}")
    for item in buckets["published"][:10]:
        lines.append(f"- [publié] #{item['id']} {item['title']} (Discord {item['discord']})")
    return ToolResult(True, "\n".join(lines),
                      data={"buckets": buckets, "events": events[:25],
                            "failures": failures[:10],
                            "settings": agent.auto_settings()})


registry.add(
    id="blog.dashboard", name="Tableau de bord éditorial", category="Blog",
    description="État réel du blog : à rédiger, brouillons, publiés, échecs, statut Discord "
                "et réglages du mode automatique.",
    handler=_dashboard, risk=READ_ONLY, agents=AGENTS,
    input_schema={"type": "object", "properties": {"limit": {"type": "integer"}}},
)


# --- Mise à jour ------------------------------------------------------------
def _update(ctx: ToolContext) -> ToolResult:
    slug = str(ctx.arguments.get("slug") or "").strip()
    if not slug or not str(ctx.arguments.get("content") or "").strip():
        return ToolResult(False, "Slug et contenu requis.")
    agent = _agent(ctx)
    existing = agent.site.get(slug=slug)
    if not existing:
        return ToolResult(False, f"Aucun article « {slug} » sur le site.")
    agent_, prepared = _prepare_article(ctx)
    prepared.article.post_id = int(existing["id"])
    prepared.article.slug = slug
    result = agent.publisher.update_existing(int(existing["id"]), prepared)
    verification = result.get("verification") or {}
    message = (f"Article #{existing['id']} mis à jour ({result['action']}). "
               f"Statut conservé : {'publié' if result['was_published'] else 'brouillon'}.")
    if result["was_published"]:
        message += (f" Vérification publique : "
                    f"{'PASS' if verification.get('passed') else verification.get('reason')}.")
    return ToolResult(True, message, risk=SAFE_WRITE, data=result)


registry.add(
    id="blog.update", name="Mettre à jour un article", category="Blog",
    description="Met à jour un article existant en conservant son slug, son SEO et son "
                "historique. Ne change pas son statut de publication.",
    handler=_update, risk=SAFE_WRITE, permissions=("write",), agents=AGENTS,
    dangerous_hint="Le contenu de l'article existant sera remplacé.",
    input_schema={"type": "object", "properties": {
        "slug": {"type": "string"}, "title": {"type": "string"}, "content": {"type": "string"},
        "facts": {"type": "array", "items": {"type": "object"}},
        "cover_image": {"type": "string"}}, "required": ["slug", "content"]},
)
