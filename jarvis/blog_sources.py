"""RESEARCH : collecte de faits RÉELS, internes puis externes.

Sources internes relevées sur le site (endpoints publics, sans authentification,
vérifiés avant écriture de ce module) :

* ``/api/public/v1/brainrots`` — catalogue des fiches : id, name, slug, rarity,
  type, cost, income_per_second, image_url, detail_url.
* ``/api/public/v1/codes`` et ``/api/public/v1/codes/active`` — codes du jeu.
* table ``blog_posts`` du site, via le pont (articles déjà publiés).

Aucun fait n'est produit par le modèle : chaque ``Fact`` porte la source d'où
sa valeur a été lue. Une recherche qui ne trouve rien ne renvoie rien — elle
n'invente pas une actualité pour avoir quelque chose à publier.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .blog_grounding import Fact
from .blog_site import SITE_BASE

BUILD_ID = "JARVIS_BLOG_PUBLISHER_V1"

BRAINROTS_API = f"{SITE_BASE}/api/public/v1/brainrots"
CODES_API = f"{SITE_BASE}/api/public/v1/codes"
CODES_ACTIVE_API = f"{SITE_BASE}/api/public/v1/codes/active"

_USER_AGENT = "JARVIS-BlogPublisher/1.0"


def _get_json(url: str, *, timeout: int = 25) -> dict[str, Any] | None:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT,
                                                   "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if int(response.status) != 200:
                return None
            payload = json.loads(response.read(4_000_000).decode("utf-8", "replace"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


# --- Catalogue Brainrots ----------------------------------------------------
def fetch_brainrot_catalog(core: Any = None, *, limit: int = 500) -> list[dict[str, Any]]:
    """Fiches réelles du site. Utilisé par le maillage interne et les images.

    L'API plafonne à 50 entrées par page (``meta.pagination`` le dit) : sans
    pagination, le maillage interne ignorerait silencieusement les deux tiers
    du catalogue et produirait des articles sans liens vers les fiches.
    """
    rows: list[Any] = []
    page, pages = 1, 1
    while page <= pages and len(rows) < limit:
        payload = _get_json(f"{BRAINROTS_API}?page={page}&limit=50")
        if not payload:
            break
        batch = payload.get("data") or []
        if not batch:
            break
        rows += batch
        pagination = (payload.get("meta") or {}).get("pagination") or {}
        pages = int(pagination.get("total_pages") or 1)
        page += 1

    catalog: list[dict[str, Any]] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        name, slug = str(row.get("name") or "").strip(), str(row.get("slug") or "").strip()
        if name and slug:
            catalog.append({"id": row.get("id"), "name": name, "slug": slug,
                            "rarity": row.get("rarity"), "type": row.get("type"),
                            "cost": row.get("cost"),
                            "income_per_second": row.get("income_per_second"),
                            "image_url": row.get("image_url"),
                            "detail_url": row.get("detail_url"),
                            "updated_at": row.get("updated_at")})
    return catalog


def brainrot_facts(names: list[str] | None = None, *, limit: int = 500) -> list[Fact]:
    """Faits Brainrot tirés de la base du site, jamais du modèle."""
    catalog = fetch_brainrot_catalog(limit=limit)
    wanted = {_fold(n) for n in (names or []) if str(n).strip()}
    facts: list[Fact] = []
    for row in catalog:
        if wanted and _fold(row["name"]) not in wanted:
            continue
        values = [str(row["name"])]
        details = []
        for label, key in (("rareté", "rarity"), ("type", "type"),
                           ("prix", "cost"), ("revenu/s", "income_per_second")):
            value = row.get(key)
            if value not in (None, ""):
                details.append(f"{label} {value}")
                values.append(str(value))
        facts.append(Fact(
            text=f"{row['name']} — " + ", ".join(details) if details else row["name"],
            source_type="brainrots", source_ref=f"brainrots#{row.get('id')}",
            url=str(row.get("detail_url") or ""), values=values))
    return facts


# --- Codes ------------------------------------------------------------------
def fetch_codes(*, active_only: bool = True) -> list[dict[str, Any]]:
    payload = _get_json(CODES_ACTIVE_API if active_only else CODES_API)
    rows = (payload or {}).get("data") or []
    return [row for row in rows if isinstance(row, dict)]


def code_facts(*, active_only: bool = True) -> list[Fact]:
    """Codes RÉELS du site. Liste vide = aucun code : aucun article inventable."""
    facts: list[Fact] = []
    for row in fetch_codes(active_only=active_only):
        code = str(row.get("code") or row.get("name") or "").strip()
        if not code:
            continue
        reward = str(row.get("reward") or row.get("description") or "").strip()
        expires = str(row.get("expires_at") or row.get("expire_at") or "").strip()
        values = [code]
        parts = [code]
        if reward:
            parts.append(reward)
            values.append(reward)
        if expires:
            parts.append(f"expire {expires}")
            values.append(expires)
        facts.append(Fact(text=" — ".join(parts), source_type="codes",
                          source_ref=f"codes#{code}", url=f"{SITE_BASE}/codes",
                          values=values))
    return facts


# --- Articles déjà publiés --------------------------------------------------
def published_facts(site: Any, *, limit: int = 30) -> list[Fact]:
    """Ce que le site dit déjà. Sert surtout à détecter les doublons."""
    facts: list[Fact] = []
    for post in site.posts(limit=limit):
        title = str(post.get("title") or "").strip()
        if not title:
            continue
        facts.append(Fact(text=title, source_type="site_db",
                          source_ref=f"blog_posts#{post.get('id')}",
                          url=f"{SITE_BASE}/blog/{post.get('slug')}",
                          values=[title, str(post.get("published_at") or "")]))
    return facts


# --- Mémoire JARVIS ---------------------------------------------------------
def memory_facts(core: Any, query: str, *, limit: int = 5) -> list[Fact]:
    """Souvenirs durables pertinents. Source interne, traçable par id."""
    facts: list[Fact] = []
    try:
        memories = core.memory.search(query, limit=limit)
    except Exception:
        return facts
    for memory in memories or []:
        content = str((memory or {}).get("content") or "").strip()
        if content:
            facts.append(Fact(text=content, source_type="jarvis_memory",
                              source_ref=f"memory#{memory.get('id')}", values=[content]))
    return facts


# --- Recherche web ----------------------------------------------------------
def web_facts(core: Any, query: str, *, limit: int = 5, agent: str = "blog") -> list[Fact]:
    """Actualité externe, via l'outil de recherche réel de JARVIS.

    Les URLs sont conservées telles quelles comme sources de l'article. Le
    texte des pages n'est jamais recopié : il sert uniquement de matière
    factuelle, et chaque fait reste attaché à son URL.
    """
    facts: list[Fact] = []
    try:
        result = core.runner.run("web.search", {"query": query, "limit": limit},
                                 agent=agent, confirmed=True)
    except Exception:
        return facts
    if not getattr(result, "ok", False):
        return facts

    data = getattr(result, "data", None)
    entries: list[dict[str, Any]] = []
    if isinstance(data, dict):
        entries = [e for e in (data.get("results") or data.get("items") or []) if isinstance(e, dict)]
    elif isinstance(data, list):
        entries = [e for e in data if isinstance(e, dict)]

    if not entries:
        # Certaines implémentations ne renvoient que du texte : on en extrait
        # les URLs, sans jamais fabriquer de titre ou de date.
        for url in re.findall(r"https?://[^\s\)\]]+", str(getattr(result, "output", "")))[:limit]:
            facts.append(Fact(text=f"Source web : {url}", source_type="web",
                              source_ref=urllib.parse.urlparse(url).netloc, url=url))
        return facts

    for entry in entries[:limit]:
        url = str(entry.get("url") or entry.get("link") or "").strip()
        title = str(entry.get("title") or "").strip()
        snippet = str(entry.get("snippet") or entry.get("description") or "").strip()
        if not url:
            continue
        facts.append(Fact(text=" — ".join(p for p in (title, snippet) if p) or url,
                          source_type="web",
                          source_ref=urllib.parse.urlparse(url).netloc,
                          url=url, values=[title, snippet]))
    return facts


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()
