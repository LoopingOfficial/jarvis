"""SEO éditorial et maillage interne, calés sur les routes RÉELLES du site.

Les routes ci-dessous ont été relevées dans le ``.htaccess`` du serveur, pas
supposées :

* ``^blog/([A-Za-z0-9_-]+)/?$        -> profile-post.php?slug=$1``
* ``^brainrots/([A-Za-z0-9_-]+)/?$   -> brainrot-detail.php?slug=$1``
* ``^wiki/<guide>/?$                 -> wiki-<guide>.php`` (liste figée : aucun
  fallback sous /wiki/, un guide absent est une vraie 404 — on ne lie donc
  jamais un guide qui n'est pas dans cette liste).

Le maillage ne lie que des cibles qui existent : un lien interne cassé est une
régression SEO, pas un bonus.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .blog_site import SITE_BASE, slugify

BUILD_ID = "JARVIS_BLOG_PUBLISHER_V1"

# Guides Wiki réellement routés (relevé .htaccess lignes 199-211).
WIKI_ROUTES = {
    "rebirths": "/wiki/rebirths",
    "rebirth": "/wiki/rebirths",
    "mutations": "/wiki/mutations-et-traits",
    "mutation": "/wiki/mutations-et-traits",
    "traits": "/wiki/mutations-et-traits",
    "trait": "/wiki/mutations-et-traits",
    "taux de spawn": "/wiki/taux-de-spawn",
    "spawn": "/wiki/taux-de-spawn",
    "niveaux de chance": "/wiki/niveaux-de-chance",
    "luck": "/wiki/niveaux-de-chance",
    "machine eternelle": "/wiki/machine-eternelle",
    "machine éternelle": "/wiki/machine-eternelle",
    "eternal machine": "/wiki/machine-eternelle",
    "lucky rots": "/wiki/lucky-rots",
    "box rots": "/wiki/box-rots",
    "boxrot": "/wiki/box-rots",
    "llama rots": "/wiki/llama-rots",
    "sprites": "/wiki/sprites",
    "roue": "/wiki/roue",
    "mecaniques": "/wiki/mecaniques-et-astuces",
    "mécaniques": "/wiki/mecaniques-et-astuces",
    "tous les brainrots": "/wiki/tous-les-brainrots",
}

# Pages de section réellement servies.
SECTION_ROUTES = {"codes": "/codes", "blog": "/blog", "wiki": "/wiki"}

# Densité : au-delà, on bourre. Limite volontairement basse.
MAX_INTERNAL_LINKS = 6
MAX_LINKS_PER_TARGET = 1

META_TITLE_MAX = 60
META_DESC_MIN, META_DESC_MAX = 110, 158
EXCERPT_MAX = 220


@dataclass
class InternalLink:
    label: str
    path: str
    kind: str

    @property
    def url(self) -> str:
        return f"{SITE_BASE}{self.path}"


class InternalLinkResolver:
    """Résout une mention vers une page existante du site. Rien d'inventé.

    Le catalogue de Brainrots vient de la table réelle ``brainrots``
    (id/name/slug), donc un lien ``/brainrots/<slug>`` n'est produit que si la
    fiche existe vraiment.
    """

    def __init__(self, brainrots: list[dict[str, Any]] | None = None) -> None:
        self._by_name: dict[str, str] = {}
        for entry in brainrots or []:
            name = str(entry.get("name") or "").strip()
            slug = str(entry.get("slug") or "").strip()
            if name and slug:
                self._by_name[_fold(name)] = slug

    @property
    def brainrot_count(self) -> int:
        return len(self._by_name)

    def resolve(self, mention: str) -> InternalLink | None:
        folded = _fold(mention)
        if not folded:
            return None
        slug = self._by_name.get(folded)
        if slug:
            return InternalLink(mention, f"/brainrots/{slug}", "brainrot")
        if folded in WIKI_ROUTES:
            return InternalLink(mention, WIKI_ROUTES[folded], "wiki")
        if folded in SECTION_ROUTES:
            return InternalLink(mention, SECTION_ROUTES[folded], "section")
        return None

    def candidates(self, text: str) -> list[InternalLink]:
        """Mentions liables trouvées dans le texte, sans doublon de cible."""
        plain = re.sub(r"(?s)<a\b.*?</a>", " ", text or "")
        plain = re.sub(r"(?s)<[^>]+>", " ", plain)
        found: list[InternalLink] = []
        seen: set[str] = set()

        # Recherche sur une version pliée de MÊME LONGUEUR que l'original :
        # les offsets restent valides, donc la casse d'origine est extraite
        # exactement (« Kitsunara », pas « Kitsunar »).
        aligned = _fold_aligned(plain)

        for name, slug in self._by_name.items():
            match = re.search(rf"\b{re.escape(name)}\b", aligned)
            if match and slug not in seen:
                seen.add(slug)
                found.append(InternalLink(plain[match.start():match.end()],
                                          f"/brainrots/{slug}", "brainrot"))

        for term, path in {**WIKI_ROUTES, **SECTION_ROUTES}.items():
            if path in seen:
                continue
            match = re.search(rf"\b{re.escape(_fold_aligned(term))}\b", aligned)
            if match:
                seen.add(path)
                found.append(InternalLink(plain[match.start():match.end()], path,
                                          "section" if term in SECTION_ROUTES else "wiki"))
        return found

    def apply(self, html: str, *, max_links: int = MAX_INTERNAL_LINKS) -> tuple[str, list[InternalLink]]:
        """Insère les liens dans le HTML, une fois par cible, hors liens existants.

        Les remplacements ne touchent jamais l'intérieur d'une balise ni d'un
        lien déjà posé : on ne réécrit que du texte visible.
        """
        applied: list[InternalLink] = []
        out = html or ""
        for link in self.candidates(out):
            if len(applied) >= max_links:
                break
            pattern = re.compile(rf"(?<![\w/>-])({re.escape(link.label)})(?![\w-])", re.I)
            replaced = 0

            def _sub(match: re.Match[str]) -> str:
                nonlocal replaced
                if replaced >= MAX_LINKS_PER_TARGET:
                    return match.group(0)
                replaced += 1
                return f'<a href="{link.path}">{match.group(1)}</a>'

            candidate = _replace_outside_markup(out, pattern, _sub)
            if replaced:
                out = candidate
                applied.append(link)
        return out, applied


def _replace_outside_markup(html: str, pattern: re.Pattern[str], repl) -> str:
    """Applique `pattern` uniquement aux segments de texte visible du HTML."""
    parts = re.split(r"(?s)(<a\b.*?</a>|<[^>]+>)", html)
    for index, part in enumerate(parts):
        if index % 2 == 0:
            parts[index] = pattern.sub(repl, part)
    return "".join(parts)


def _fold(text: str) -> str:
    out = (text or "").lower()
    for src, dst in {"à": "a", "â": "a", "é": "e", "è": "e", "ê": "e", "ë": "e", "î": "i",
                     "ï": "i", "ô": "o", "ù": "u", "û": "u", "ç": "c", "’": "'"}.items():
        out = out.replace(src, dst)
    return re.sub(r"\s+", " ", out).strip()


# Repli caractère par caractère : la longueur est conservée, donc un offset
# trouvé sur le texte plié désigne le même endroit sur le texte d'origine.
_ALIGNED_MAP = str.maketrans({
    "à": "a", "á": "a", "â": "a", "ä": "a", "ã": "a", "é": "e", "è": "e", "ê": "e",
    "ë": "e", "í": "i", "î": "i", "ï": "i", "ó": "o", "ô": "o", "ö": "o", "õ": "o",
    "ù": "u", "ú": "u", "û": "u", "ü": "u", "ç": "c", "ñ": "n", "ý": "y", "’": "'",
})


def _fold_aligned(text: str) -> str:
    return (text or "").lower().translate(_ALIGNED_MAP)


# --- Métadonnées ------------------------------------------------------------
def _plain(html: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def build_excerpt(html: str, *, limit: int = EXCERPT_MAX) -> str:
    """Résumé tiré du texte réel de l'article, coupé sur une frontière de phrase."""
    text = _plain(html)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in (". ", " ! ", " ? ", ", "):
        index = cut.rfind(sep)
        if index > limit * 0.5:
            return cut[:index + 1].strip()
    return cut.rsplit(" ", 1)[0].strip() + "…"


def build_meta_description(html: str, excerpt: str = "") -> str:
    base = (excerpt or build_excerpt(html, limit=META_DESC_MAX)).strip()
    if len(base) > META_DESC_MAX:
        base = base[:META_DESC_MAX].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    return base


def build_meta_title(title: str, *, suffix: str = " | Brainrot Fortnite") -> str:
    title = (title or "").strip()
    if len(title) + len(suffix) <= META_TITLE_MAX:
        return title + suffix
    return title[:META_TITLE_MAX].rsplit(" ", 1)[0].rstrip(" ,;:-")


@dataclass
class SeoReport:
    status: str
    issues: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "issues": list(self.issues), "metrics": dict(self.metrics)}


def validate_seo(article: dict[str, Any]) -> SeoReport:
    """Contrôle SEO. Bloquant pour une publication automatique en FULL AUTO."""
    issues: list[str] = []
    title = str(article.get("title") or "").strip()
    slug = str(article.get("slug") or "").strip()
    content = str(article.get("content") or "")
    meta_title = str(article.get("meta_title") or "").strip()
    meta_desc = str(article.get("meta_desc") or "").strip()
    excerpt = str(article.get("excerpt") or "").strip()
    words = len(_plain(content).split())
    h2_count = len(re.findall(r"(?i)<h2\b", content))

    if not title:
        issues.append("Titre manquant.")
    elif len(title) > 90:
        issues.append(f"Titre trop long ({len(title)} caractères).")
    if not slug or slugify(slug) != slug:
        issues.append(f"Slug invalide pour la route du site : « {slug} ».")
    if not meta_title:
        issues.append("meta_title manquant.")
    if len(meta_desc) < META_DESC_MIN or len(meta_desc) > META_DESC_MAX:
        issues.append(f"meta_desc hors bornes ({len(meta_desc)} car., attendu "
                      f"{META_DESC_MIN}-{META_DESC_MAX}).")
    if not excerpt:
        issues.append("Extrait manquant.")
    if words < 250:
        issues.append(f"Article trop court ({words} mots).")
    if h2_count < 2:
        issues.append(f"Structure insuffisante ({h2_count} H2, minimum 2).")
    if article.get("category_id") in (None, "", 0):
        issues.append("Catégorie non résolue.")

    links = re.findall(r'href="(/[^"]*)"', content)
    if len(links) > MAX_INTERNAL_LINKS:
        issues.append(f"Trop de liens internes ({len(links)}).")

    metrics = {"words": words, "h2": h2_count, "internal_links": len(links),
               "meta_desc_length": len(meta_desc), "title_length": len(title)}
    return SeoReport("PASS" if not issues else "FAIL", issues, metrics)
