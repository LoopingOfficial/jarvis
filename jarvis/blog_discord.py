"""Notification Discord d'un article — après vérification publique, jamais avant.

Deux verrous, tous deux dans ``notify`` et impossibles à contourner depuis
l'extérieur du module :

1. **Verrou de publication** — l'appelant doit fournir le rapport de
   ``verify_public`` et celui-ci doit être ``passed``. Un brouillon, une
   publication échouée ou une vérification ratée ne produisent aucun message.
2. **Verrou d'idempotence** — le journal ``blog_publication_events`` est
   consulté avant l'envoi. Une notification déjà émise pour ce contenu n'est
   jamais renvoyée.

Le bot Discord existant est réutilisé : aucun second bot n'est créé.
"""
from __future__ import annotations

import re
from typing import Any

from .blog_site import SITE_BASE

BUILD_ID = "JARVIS_BLOG_PUBLISHER_V1"

SETTINGS_SECTION = "blog"
SETTING_CHANNEL = "discord_channel_id"
SITE_NAME = "Brainrot-Fortnite"
SITE_LOGO = f"{SITE_BASE}/img/favicon.ico"

NEW_ARTICLE_TITLE = "📰 NOUVEL ARTICLE"
UPDATED_ARTICLE_TITLE = "🔄 ARTICLE MIS À JOUR"

BRAND_COLOR = 0x4FF461  # vert du site (blog_categories.color « Actualites »)

MAX_KEY_POINTS = 4
MIN_KEY_POINTS = 2


class DiscordNotifyBlocked(RuntimeError):
    """Refus explicite d'envoyer. Ce n'est jamais une erreur silencieuse."""


def _plain(html: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def key_points(article: dict[str, Any], *, limit: int = MAX_KEY_POINTS) -> list[str]:
    """Points clés tirés de la structure réelle de l'article (H2, puis listes).

    Rien n'est reformulé ni inventé : ce sont les intertitres que l'article
    porte déjà. C'est ce qui donne envie de cliquer sans recopier le contenu.
    """
    content = str(article.get("content") or "")
    points = [_plain(m) for m in re.findall(r"(?is)<h2[^>]*>(.*?)</h2>", content)]
    if len(points) < MIN_KEY_POINTS:
        points += [_plain(m) for m in re.findall(r"(?is)<li[^>]*>(.*?)</li>", content)]
    cleaned: list[str] = []
    for point in points:
        point = point.strip(" :—-")
        if point and point.lower() not in {p.lower() for p in cleaned}:
            cleaned.append(point[:90])
    return cleaned[:limit]


def build_payload(article: dict[str, Any], *, updated: bool = False) -> dict[str, Any]:
    """Contenu de l'embed. Un teaser, jamais l'article complet."""
    title = str(article.get("title") or "").strip()
    url = str(article.get("url") or "").strip()
    excerpt = str(article.get("excerpt") or "").strip() or _plain(
        str(article.get("content") or ""))[:200]
    points = key_points(article)

    description = [f"**{title}**", "", excerpt]
    if points:
        description += ["", "Dans cet article :"] + [f"• {p}" for p in points]
    description += ["", f"🌐 [Lire l'article]({url})"]

    cover = str(article.get("cover_image") or "").strip()
    if cover and not cover.startswith("http"):
        cover = f"{SITE_BASE}/{cover.lstrip('/')}"

    return {
        "embed_title": UPDATED_ARTICLE_TITLE if updated else NEW_ARTICLE_TITLE,
        "description": "\n".join(description),
        "article_title": title,
        "url": url,
        "image": cover,
        "key_points": points,
        "color": BRAND_COLOR,
        "author": SITE_NAME,
        "logo": SITE_LOGO,
    }


def channel_id(core: Any) -> str:
    """Salon configuré dans Settings > Blog. Aucun salon deviné par défaut."""
    try:
        value = core.settings.get(SETTINGS_SECTION, SETTING_CHANNEL, "")
    except Exception:
        value = ""
    return str(value or "").strip()


def _engine(core: Any):
    engine = getattr(core, "discord", None)
    if engine is None:
        from .discord_engine import DiscordEngine
        engine = DiscordEngine(core)
        core.discord = engine
    return engine


def _send(core: Any, target_channel: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Envoie l'embed via le bot existant, avec bouton « Lire l'article »."""
    from . import discord_engine as de

    if not de.HAVE_DISCORD:
        raise DiscordNotifyBlocked("Bibliothèque Discord indisponible.")
    engine = _engine(core)
    if not engine.connected:
        started = engine.start()
        if not started.get("ok", True) or not engine.connected:
            raise DiscordNotifyBlocked("Bot Discord non connecté.")

    discord = de.discord

    async def send() -> dict[str, Any]:
        channel = engine._channel(int(target_channel))
        embed = discord.Embed(title=payload["embed_title"],
                              description=payload["description"][:4000],
                              colour=payload["color"], url=payload["url"] or None,
                              timestamp=discord.utils.utcnow())
        embed.set_author(name=payload["author"], url=SITE_BASE, icon_url=payload["logo"])
        if payload["image"]:
            embed.set_image(url=payload["image"])
        embed.set_footer(text=SITE_NAME, icon_url=payload["logo"])

        view = None
        if payload["url"]:
            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="Lire l'article", url=payload["url"],
                                            style=discord.ButtonStyle.link))
        message = await channel.send(embed=embed, view=view) if view else \
            await channel.send(embed=embed)
        return {"message_id": str(getattr(message, "id", "")), "channel": str(channel)}

    return engine.submit(send())


def notify(core: Any, publisher: Any, article: dict[str, Any], *,
           verification: dict[str, Any] | None, updated: bool = False,
           channel: str = "") -> dict[str, Any]:
    """Notifie Discord — uniquement si l'article est publié ET vérifié.

    Renvoie toujours un verdict explicite. ``sent=False`` avec un ``reason``
    est un résultat normal (déjà notifié, non vérifié), pas une exception,
    sauf en cas de configuration manquante ou d'échec d'envoi réel.
    """
    article_id = int(article.get("post_id") or 0)
    content_hash = str(article.get("content_hash") or "")

    if not verification or not verification.get("passed"):
        reason = (verification or {}).get("reason") or "Article non vérifié publiquement."
        return {"sent": False, "blocked": True, "reason": reason}

    if not article_id:
        return {"sent": False, "blocked": True, "reason": "Article sans identifiant sur le site."}

    existing = publisher.already_notified(article_id, content_hash=content_hash)
    if existing:
        return {"sent": False, "blocked": True, "reason": "Notification déjà envoyée.",
                "duplicate_of": existing.get("discord_message_id", ""),
                "event_id": existing.get("id")}

    target = (channel or channel_id(core)).strip()
    if not target.isdigit():
        raise DiscordNotifyBlocked(
            f"Salon Discord du blog non configuré : renseignez {SETTING_CHANNEL} "
            f"dans Settings > Blog.")

    payload = build_payload(article, updated=updated)
    try:
        sent = _send(core, target, payload)
    except DiscordNotifyBlocked:
        raise
    except Exception as exc:
        publisher.record_event(article_id=article_id, slug=str(article.get("slug") or ""),
                               status="FAILED", kind="discord",
                               title=str(article.get("title") or ""),
                               url=str(article.get("url") or ""),
                               content_hash=content_hash, discord_channel_id=target,
                               detail=str(exc)[:400])
        raise DiscordNotifyBlocked(f"Envoi Discord impossible : {exc}") from exc

    publisher.record_event(article_id=article_id, slug=str(article.get("slug") or ""),
                           status="SENT", kind="discord",
                           title=str(article.get("title") or ""),
                           url=str(article.get("url") or ""),
                           content_hash=content_hash,
                           discord_message_id=sent.get("message_id", ""),
                           discord_channel_id=target,
                           verification=verification,
                           detail="update" if updated else "publish")
    return {"sent": True, "blocked": False, "message_id": sent.get("message_id", ""),
            "channel": target, "payload": payload}
