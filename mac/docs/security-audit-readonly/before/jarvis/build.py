"""Identité de build et traçabilité d'exécution.

JARVIS_BUILD_ID : valeur unique injectée à chaque correction de fond. Elle est
affichée au démarrage, dans Settings → Developer et dans /api/status pour
prouver que l'instance testée exécute réellement le code attendu.
"""
from __future__ import annotations

import os
import re

# Keep the repository-wide build marker stable for existing integrations.
# The image engine exposes its own, more precise build marker below.
JARVIS_BUILD_ID = "REMOTE_PATH_FIX_20260911_A"
READ_ONLY_SECURITY_ROUTING_BUILD_ID = "READ_ONLY_SECURITY_ROUTING_20260911_A"
IMAGE_PIPELINE_BUILD_ID = "IMAGE_HYBRID_SDXL_20260911_A"

# Racines génériques INTERDITES comme racine de travail distante de secours.
# Ces chemins ne doivent JAMAIS être utilisés comme fallback quand aucune
# configuration réelle n'existe (requête #21).
FORBIDDEN_REMOTE_DEFAULTS = ("/var/www/html", "/var/www", "/home/user")

_TRACE_ENABLED = os.getenv("JARVIS_DEBUG", "0").lower() in {"1", "true", "yes", "on"}


def trace(*parts: str) -> None:
    """Écrit une trace [FILE-TRACE] quand JARVIS_DEBUG est actif."""
    if not _TRACE_ENABLED:
        return
    for part in parts:
        line = f"[FILE-TRACE] {part}"
        try:
            print(line, flush=True)
        except Exception:
            try:
                print(line.encode("ascii", "replace").decode("ascii"), flush=True)
            except Exception:
                pass


def is_forbidden_remote_default(path: str | None) -> bool:
    """Vrai si le chemin (racine distante) est une valeur générique interdite."""
    p = str(path or "").strip().rstrip("/") or "/"
    return any(p == root or p.startswith(root + "/") for root in FORBIDDEN_REMOTE_DEFAULTS)


def scrub_forbidden_roots(text: str, working_directory: str = "") -> str:
    """Retire des mentions de racines génériques interdites d'un texte.

    Utilisé au moment de la construction du prompt : l'historique de
    conversation ne doit jamais réinjecter `/var/www/html` au modèle.
    """
    if not text:
        return text
    repl = working_directory or "{racine réelle du connecteur}"
    for root in FORBIDDEN_REMOTE_DEFAULTS:
        text = re.sub(re.escape(root) + r"/?(?=[\s.,;:!?'\"\u2019)])?", repl.rstrip("/"), text, flags=re.I)
        text = text.replace(root, repl.rstrip("/"))
    return text
