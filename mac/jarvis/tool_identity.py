"""Identité canonique des outils — UNE clé par outil pour tout le système.

Problème corrigé ici : le même outil était enregistré sous deux clés
(CAS), par exemple :

    "Lire un fichier distant"   (nom humain, clé issue de tool.name)
    "ssh.read_file"             (tool_id canonique)

Dans tout JARVIS les statistiques, la connaissance, l'expertise et
l'apprentissage utilisent désormais UNIQUEMENT le `tool_id` canonique
(ex. ``ssh.read_file``). Le nom humain, les alias et la catégorie ne sont
que des métadonnées d'affichage rattachées à cette identité unique.

Résolution :
    canonical("Lire un fichier distant")  -> "ssh.read_file"
    canonical("ssh.read_file")            -> "ssh.read_file"
    canonical("read remote file")         -> "ssh.read_file"
    canonical("outil inconnu")            -> "outil inconnu" (clé préservée)

La table est construite à partir du registre d'outils réel (base.py) et
peut aussi être alimentée manuellement (migration, tests, outils dynamiques).
"""
from __future__ import annotations

import unicodedata
from typing import Any

from .tools.base import registry

# Un curseur de version/docs par outil : "latest" par défaut (la version est
# écrite par les sessions d'apprentissage quand une source l'indique).
DEFAULT_DOCS_VERSION = "latest"

# Alias supplémentaires connus (au-delà des noms affichés du registre).
EXTRA_ALIASES: dict[str, list[str]] = {
    "ssh.read_file": ["read remote file", "lecture fichier distant", "afficher un fichier distant"],
    "ssh.list": ["list remote files", "liste dossier distant", "lister fichiers distants", "List un dossier distant", "Lister un dossier distant"],
    "ssh.run": ["run remote command", "commande distante"],
    "connector.list": ["list connectors", "liste connecteurs"],
    "fs.read": ["lire un fichier local"],
    "memory.search": ["recherche memoire"],
}


def _norm(value: str) -> str:
    """Minuscules, sans accents ni diacritiques, espaces compactés."""
    text = unicodedata.normalize("NFD", (value or "").casefold())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return " ".join(text.split())


class ToolIdentity:
    """Registre central : tool_id canonique + métadonnées d'affichage."""

    def __init__(self) -> None:
        self._by_id: dict[str, dict[str, Any]] = {}
        self._alias: dict[str, str] = {}  # clé normalisée -> tool_id canonique

    def register(self, tool_id: str, display_name: str = "", category: str = "",
                 description: str = "", aliases: list[str] | tuple[str, ...] = ()) -> None:
        tool_id = (tool_id or "").strip()
        if not tool_id:
            return
        entry = self._by_id.setdefault(tool_id, {
            "id": tool_id, "display_name": display_name or tool_id,
            "category": category or "", "description": description or "",
            "aliases": [str(a) for a in aliases],
        })
        if display_name:
            entry["display_name"] = display_name
        if category:
            entry["category"] = category
        if description:
            entry["description"] = description
        entry["aliases"] = list(dict.fromkeys([*entry["aliases"], *aliases,
                                               *EXTRA_ALIASES.get(tool_id, []), display_name]))
        for alias in [display_name, tool_id, *entry["aliases"], *EXTRA_ALIASES.get(tool_id, [])]:
            if alias:
                self._alias.setdefault(_norm(alias), tool_id)

    def rebuild_from_registry(self) -> None:
        """Re-synchronise depuis le registre d'outils réel (idempotent)."""
        for tool in registry.all():
            self.register(tool.id, display_name=tool.name, category=tool.category,
                          description=tool.description, aliases=[])

    def canonical(self, key: str) -> str:
        """Résout n'importe quelle clé (id / nom humain / alias) vers le tool_id canonique."""
        if not key:
            return key or ""
        key = str(key).strip()
        if key in self._by_id:
            return key
        return self._alias.get(_norm(key), key)

    def info(self, tool_id: str) -> dict[str, Any] | None:
        """Fiche d'identité pour un tool_id canonique (None si inconnu)."""
        cid = self.canonical(tool_id)
        return self._by_id.get(cid)

    def display_name(self, tool_id: str) -> str:
        info = self.info(tool_id)
        if info:
            return info["display_name"]
        return tool_id

    def is_known(self, tool_id: str) -> bool:
        return tool_id in self._by_id

    def all(self) -> list[dict[str, Any]]:
        return sorted(self._by_id.values(), key=lambda e: (e["category"], e["display_name"]))


# Instance partagée par tout le système.
identity = ToolIdentity()


def ensure_identity() -> ToolIdentity:
    """Reconstruit (si vide) l'index depuis le registre réel et le retourne."""
    identity.rebuild_from_registry()
    return identity
