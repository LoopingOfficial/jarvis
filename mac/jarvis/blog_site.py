"""Passerelle vers le blog RÉEL de brainrot-fortnite.com.

Inspection effectuée sur le serveur avant écriture de ce module :

* ``blog.php`` liste les articles via ``includes_app/blog_content.php``
  (``blog_db()``, ``blog_fetch_posts()``, ``blog_fetch_categories()``).
* Tables MySQL réelles : ``blog_posts`` (id, category_id, title, slug, status,
  cover_image, excerpt, content, meta_title, meta_desc, author_id,
  published_at, created_at, updated_at) et ``blog_categories``.
* Route publique réelle, relevée dans ``.htaccess`` :
  ``RewriteRule ^blog/([A-Za-z0-9_-]+)/?$ profile-post.php?slug=$1`` — donc
  l'URL canonique d'un article est ``/blog/<slug>``.
* ``admin/api/blog_post_save.php`` existe mais exige une session admin et un
  jeton CSRF : inutilisable depuis un agent. Le chemin retenu est le pont PHP
  CLI ``tools/jarvis_blog_bridge.php``, qui appelle le système du site.

Aucun CMS parallèle n'est créé : ce module écrit dans les tables du site, avec
le schéma du site, et l'article ressort sur les pages existantes du site.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BUILD_ID = "JARVIS_BLOG_PUBLISHER_V1"

SITE_BASE = "https://brainrot-fortnite.com"
BRIDGE_REMOTE_PATH = "tools/jarvis_blog_bridge.php"
BRIDGE_LOCAL_PATH = Path(__file__).with_name("blog_bridge.php")

DRAFT, PUBLISHED = "draft", "published"

# Statuts du pipeline éditorial, publiés tels quels vers Spatial OS V5.
RESEARCH, VERIFY, WRITE, SEO, PUBLISH, VERIFY_PUBLIC, DISCORD_NOTIFY = (
    "RESEARCH", "VERIFY", "WRITE", "SEO", "PUBLISH", "VERIFY_PUBLIC", "DISCORD_NOTIFY")

PUBLISHED_VERIFIED = "PUBLISHED_VERIFIED"
NEEDS_REVIEW = "NEEDS_REVIEW"
FAILED = "FAILED"


class BlogSiteError(RuntimeError):
    """Erreur renvoyée par le site lui-même. Jamais silencieuse."""


def public_url(slug: str) -> str:
    return f"{SITE_BASE}/blog/{slug.strip('/')}"


def slugify(value: str) -> str:
    """Slug compatible avec la route réelle : ``[A-Za-z0-9_-]+`` uniquement."""
    text = (value or "").strip().lower()
    accents = {"à": "a", "â": "a", "ä": "a", "é": "e", "è": "e", "ê": "e", "ë": "e",
               "î": "i", "ï": "i", "ô": "o", "ö": "o", "ù": "u", "û": "u", "ü": "u",
               "ç": "c", "œ": "oe", "æ": "ae"}
    for src, dst in accents.items():
        text = text.replace(src, dst)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    return text[:180] or "article"


class BlogSite:
    """Lecture / écriture du blog réel, via le pont PHP CLI sur le serveur."""

    def __init__(self, core: Any, *, connector_id: str = "ssh", agent: str = "blog") -> None:
        self._core = core
        self.connector_id = connector_id
        self.agent = agent
        self._bridge_ready = False

    # --- Transport ----------------------------------------------------------
    def _ssh(self, tool: str, arguments: dict[str, Any]) -> Any:
        args = dict(arguments)
        args.setdefault("connector_id", self.connector_id)
        result = self._core.runner.run(tool, args, agent=self.agent, confirmed=True)
        if not getattr(result, "ok", False):
            raise BlogSiteError(f"{tool} a échoué : {str(getattr(result, 'output', ''))[:300]}")
        return getattr(result, "output", "") or ""

    def ensure_bridge(self, *, force: bool = False) -> bool:
        """Déploie le pont s'il manque ou si son contenu a changé.

        La comparaison porte sur le SHA-256 réel du fichier distant : on ne
        réécrit jamais un fichier identique, et on ne suppose jamais qu'un
        fichier déployé une fois est encore à jour.
        """
        if self._bridge_ready and not force:
            return False
        source = BRIDGE_LOCAL_PATH.read_text(encoding="utf-8")
        expected = hashlib.sha256(source.encode("utf-8")).hexdigest()
        remote = ""
        if not force:
            out = self._ssh("ssh.run", {
                "command": f"sha256sum {BRIDGE_REMOTE_PATH} 2>/dev/null | cut -d' ' -f1"})
            remote = re.sub(r"^\[[^\]]*\]\s*", "", str(out)).strip().splitlines()
            remote = remote[-1].strip() if remote else ""
        if remote == expected:
            self._bridge_ready = True
            return False
        self._ssh("ssh.run", {"command": "mkdir -p tools"})
        self._ssh("ssh.write_file", {"path": BRIDGE_REMOTE_PATH, "content": source})
        self._bridge_ready = True
        return True

    def call(self, op: str, **payload: Any) -> dict[str, Any]:
        """Invoque une opération du pont et renvoie sa réponse JSON."""
        self.ensure_bridge()
        body = dict(payload)
        body["op"] = op
        blob = base64.b64encode(json.dumps(body, ensure_ascii=False).encode("utf-8")).decode("ascii")
        out = str(self._ssh("ssh.run", {
            "command": f"php {BRIDGE_REMOTE_PATH} {blob}", "timeout": 120}))
        # Le pont n'écrit qu'une ligne JSON ; le transport SSH peut préfixer
        # « [SSH] » et des avertissements PHP. On retient la dernière ligne
        # réellement décodable plutôt que de faire confiance au format.
        parsed: dict[str, Any] | None = None
        for line in reversed(out.splitlines()):
            candidate = line.strip()
            start = candidate.find("{")
            if start < 0:
                continue
            try:
                data = json.loads(candidate[start:])
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and "ok" in data:
                parsed = data
                break
        if parsed is None:
            raise BlogSiteError(f"Réponse illisible du pont blog : {out[:300]}")
        if not parsed.get("ok"):
            raise BlogSiteError(
                f"{parsed.get('error', 'UNKNOWN')} {parsed.get('detail', '')}".strip())
        return parsed

    # --- Lecture ------------------------------------------------------------
    def ping(self) -> dict[str, Any]:
        return self.call("ping")

    def categories(self) -> list[dict[str, Any]]:
        return list(self.call("categories").get("categories") or [])

    def posts(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(self.call("posts", limit=limit).get("posts") or [])

    def get(self, *, slug: str = "", post_id: int = 0) -> dict[str, Any] | None:
        return self.call("get", slug=slug, id=post_id).get("post")

    def search(self, terms: list[str]) -> list[dict[str, Any]]:
        terms = [t for t in (terms or []) if str(t).strip()]
        if not terms:
            return []
        return list(self.call("search", terms=terms).get("matches") or [])

    def category_id(self, wanted: str, *, default_slug: str = "actualites") -> int | None:
        """Résout une catégorie existante. N'en crée jamais une nouvelle."""
        wanted_slug = slugify(wanted or "")
        cats = self.categories()
        for cat in cats:
            if slugify(str(cat.get("slug") or "")) == wanted_slug or \
                    slugify(str(cat.get("name") or "")) == wanted_slug:
                return int(cat["id"])
        for cat in cats:
            if slugify(str(cat.get("slug") or "")) == default_slug:
                return int(cat["id"])
        return int(cats[0]["id"]) if cats else None

    def free_slug(self, base: str, *, ignore_id: int = 0) -> str:
        """Slug libre sur le site réel. Le suffixe n'est ajouté qu'en collision."""
        root = slugify(base)
        candidate = root
        for attempt in range(2, 40):
            existing = self.get(slug=candidate)
            if not existing or int(existing.get("id") or 0) == int(ignore_id or 0):
                return candidate
            candidate = f"{root}-{attempt}"
        raise BlogSiteError(f"Aucun slug libre pour « {root} ».")

    # --- Écriture -----------------------------------------------------------
    def save(self, fields: dict[str, Any], *, post_id: int = 0) -> dict[str, Any]:
        result = self.call("save", fields=fields, id=int(post_id or 0))
        return {"action": result.get("action"), "post": result.get("post") or {}}

    def publish(self, post_id: int, *, published_at: str = "") -> dict[str, Any]:
        return self.call("publish", id=int(post_id), published_at=published_at).get("post") or {}

    # --- Vérification publique ---------------------------------------------
    def verify_public(self, slug: str, *, expected_title: str = "",
                      timeout: int = 25) -> dict[str, Any]:
        """Vérifie que l'article est RÉELLEMENT accessible publiquement.

        C'est la seule preuve acceptée avant une notification Discord : un
        `status` en base valant `published` ne prouve rien sur ce que sert le
        site. On exige HTTP 200, le titre présent dans la page et un canonical
        cohérent avec l'URL publique.
        """
        url = public_url(slug)
        report: dict[str, Any] = {"url": url, "http_status": 0, "title_found": False,
                                  "canonical_ok": False, "canonical": "", "passed": False,
                                  "reason": ""}
        request = urllib.request.Request(url, headers={"User-Agent": "JARVIS-BlogPublisher/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                report["http_status"] = int(response.status)
                html = response.read(400_000).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            report["http_status"] = int(exc.code)
            report["reason"] = f"HTTP {exc.code}"
            return report
        except Exception as exc:  # réseau, DNS, TLS
            report["reason"] = f"Requête impossible : {exc}"
            return report

        if report["http_status"] != 200:
            report["reason"] = f"HTTP {report['http_status']}"
            return report

        haystack = _strip_tags(html)
        needle = (expected_title or "").strip()
        report["title_found"] = bool(needle) and _fold(needle) in _fold(haystack)

        match = re.search(r"<link[^>]+rel=[\"']canonical[\"'][^>]*>", html, re.I)
        if match:
            href = re.search(r"href=[\"']([^\"']+)[\"']", match.group(0), re.I)
            report["canonical"] = href.group(1) if href else ""
        report["canonical_ok"] = report["canonical"].rstrip("/").endswith(f"/blog/{slug}")

        if not report["title_found"]:
            report["reason"] = "Titre absent de la page publique."
        elif not report["canonical_ok"]:
            report["reason"] = f"Canonical inattendu : {report['canonical'] or 'absent'}"
        else:
            report["passed"] = True
        return report


def _strip_tags(html: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def _fold(text: str) -> str:
    out = (text or "").lower()
    for src, dst in {"à": "a", "â": "a", "é": "e", "è": "e", "ê": "e", "ë": "e", "î": "i",
                     "ï": "i", "ô": "o", "ù": "u", "û": "u", "ç": "c", "’": "'"}.items():
        out = out.replace(src, dst)
    return re.sub(r"\s+", " ", out).strip()
