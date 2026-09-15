"""Traduction des erreurs Google Sheets en messages humains précis.

Règle d'or : un code d'erreur n'est JAMAIS affiché brut dans le chat, et il
n'est jamais remplacé par le seul message générique « je ne peux pas accéder
aux Google Sheets privés ». Chaque code a sa vraie signification :

    SHEET_NOT_FOUND      → le fichier est introuvable
    SHEET_ACCESS_DENIED  → accès refusé / authentification nécessaire
    SHEET_TAB_NOT_FOUND  → le fichier est trouvé mais l'onglet n'est pas résolu
    SHEET_EMPTY          → l'onglet est trouvé mais vide
    SHEET_PARSE_ERROR    → les données sont reçues mais illisibles
    NETWORK_ERROR        → problème de connexion
"""
from __future__ import annotations

from typing import Any

# Codes canoniques (synthèse) → message humain.
SHEET_ERROR_MESSAGES: dict[str, str] = {
    "SHEET_NOT_FOUND": "Le Google Sheet demandé est introuvable : vérifie l'URL ou l'identifiant.",
    "SHEET_ACCESS_DENIED": "Accès refusé au Google Sheet : il nécessite une authentification ou "
                           "n'est pas partagé en lecture publique.",
    "SHEET_TAB_NOT_FOUND": "J'ai bien trouvé le Google Sheet, mais je n'ai pas réussi à "
                           "identifier l'onglet demandé.",
    "SHEET_EMPTY": "L'onglet demandé a été trouvé mais il est vide : aucune donnée à analyser.",
    "SHEET_PARSE_ERROR": "Le Google Sheet a répondu mais ses données n'ont pas pu être lues.",
    "NETWORK_ERROR": "Problème de connexion lors de la lecture du Google Sheet.",
}

# Codes réellement émis par le backend → canonique ou message direct.
SHEET_ERROR_ALIASES: dict[str, str] = {
    "GOOGLE_SHEET_ACCESS_DENIED": "SHEET_ACCESS_DENIED",
    "GOOGLE_SHEET_INVALID_URL": "SHEET_NOT_FOUND",
    "GOOGLE_SHEET_READ_FAILED": "SHEET_PARSE_ERROR",
    "SHEET_TAB_NOT_FOUND": "SHEET_TAB_NOT_FOUND",
    "SHEETTAB_NOT_FOUND": "SHEET_TAB_NOT_FOUND",
    "SHEETTABNOT_FOUND": "SHEET_TAB_NOT_FOUND",
    "GOOGLE_SHEET_TOO_LARGE": "Le Google Sheet est trop volumineux pour être lu en une fois.",
    "SHEET_TABLE_NOT_FOUND": "L'onglet a été trouvé mais le tableau attendu n'y a pas été détecté.",
    "SHEET_VALUE_MISSING": "Certaines valeurs sont manquantes dans le Google Sheet.",
    "SHEET_VALUE_NOT_NUMERIC": "Des valeurs attendues numériques ne le sont pas dans le Google Sheet.",
    "SITE_READ_FAILED": "La lecture des données du site a échoué via le connecteur distant.",
    "SITE_SCHEMA_UNRECOGNISED": "Le format des données du site n'a pas été reconnu.",
    "STALE_COMPARISON": "La comparaison s'est basée sur des données qui ont changé depuis.",
    "NO_SHEET_URL": "Je n'ai pas de Google Sheet dans le contexte actuel.",
    "IMAGE_CATALOG_UNREACHABLE": "Le catalogue d'images du site est injoignable.",
    "IMAGE_CATALOG_REFUSED": "Le catalogue d'images du site a refusé la lecture.",
}

# Codes inhérents à une impossibilité d'accès (seuls ceux-ci autorisent la
# mention « privé / authentification » dans la réponse.
ACCESS_CODES = {"SHEET_ACCESS_DENIED", "GOOGLE_SHEET_ACCESS_DENIED"}


def canonical_sheet_error(code: str | None) -> str:
    """Ramène un code backend vers le canonique de la table (ou le code lui-même)."""
    code = (code or "").strip()
    if code in SHEET_ERROR_MESSAGES:
        return code
    if code in SHEET_ERROR_ALIASES:
        mapped = SHEET_ERROR_ALIASES[code]
        return mapped if mapped in SHEET_ERROR_MESSAGES else code
    return code


def sheet_error_message(code: str | None, detail: str = "") -> str:
    """Message humain pour un code d'erreur Sheet, sans jamais coller du brut."""
    canonical = canonical_sheet_error(code)
    message = SHEET_ERROR_MESSAGES.get(canonical) or SHEET_ERROR_ALIASES.get(canonical) or ""
    if not message:
        message = f"Lecture du Google Sheet impossible ({canonical})."
    if detail and str(detail).strip():
        message = f"{message} {str(detail).strip().rstrip('.')}."
    return message


def tab_resolution_suggestion(available_tabs: list[Any] | None = None,
                              requested_tab: str = "") -> str:
    """Options concrètes quand l'onglet demandé n'a pas pu être identifié.

    N'évoque JAMAIS un problème de confidentialité : c'est une résolution
    d'onglet, pas une question d'autorisation.
    """
    names = [t.get("name", "") if isinstance(t, dict) else str(t)
             for t in (available_tabs or []) if (t.get("name", "") if isinstance(t, dict) else str(t))]
    parts = ["Je peux : détecter automatiquement l'onglet, ou te lister les onglets trouvés "
             "pour que tu choisisses celui à utiliser."]
    if requested_tab:
        parts.append(f"Onglet demandé : « {requested_tab} ».")
    if names:
        listed = ", ".join(f"« {n} »" for n in names[:12])
        suffix = "…" if len(names) > 12 else ""
        parts.append(f"Onglets trouvés dans le classeur : {listed}{suffix}.")
    return " ".join(parts)


def is_access_error(code: str | None) -> bool:
    return canonical_sheet_error(code) in ACCESS_CODES or (code or "") in ACCESS_CODES