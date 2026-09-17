"""Outils navigateur live : pilotent la session partagée de l'aperçu.

Une seule instance Chromium (BrowserManager) sert à la fois l'agent JARVIS
(outils browser.*) et l'UI Live Browser Preview. Ces outils s'enregistrent
dans le REGISTRE PRINCIPAL (jarvis/tools/base.py) utilisé par le chat :
c'est ce registre que l'orchestrateur expose au modèle (orchestrator.py,
registry.for_agent), PAS self_upgrade/tools_def.py.

Aucune session Chromium n'est créée ici : tout passe par get_manager(),
le singleton créé dans JarvisCore.__init__ (core.browser).
"""
from __future__ import annotations

from typing import Callable

from ..permissions import READ_ONLY, SAFE_WRITE
from .base import ToolContext, ToolResult, registry

_DONE_MESSAGES = {
    "navigate": "Page ouverte.",
    "click": "Clic effectué.",
    "type": "Texte saisi.",
    "scroll": "Défilement effectué.",
    "wait": "Attente terminée.",
    "back": "Retour effectué.",
    "pause": "Action manuelle requise.",
    "close": "Session navigateur fermée.",
}


def _live_browser(op: str) -> Callable[[ToolContext], ToolResult]:
    """Handler générique : route l'appel vers la session partagée."""

    def handler(ctx: ToolContext) -> ToolResult:
        from ..browser_manager import get_manager

        mgr = get_manager()
        if mgr is None:
            return ToolResult(False, "Session navigateur live non initialisée.")
        if not mgr.available():
            return ToolResult(
                False,
                "Navigateur intégré indisponible : Playwright n'est pas installé. "
                "Commande : .venv\\Scripts\\python.exe -m pip install playwright "
                "et .venv\\Scripts\\python.exe -m playwright install chromium",
            )
        mgr.start()
        out = mgr.action(op, ctx.arguments, timeout=45)
        if out.get("ok"):
            target = out.get("target") or out.get("url") or out.get("title") or ""
            msg = target or _DONE_MESSAGES.get(op, "OK")
            data = {k: v for k, v in out.items() if k != "ok"}
            return ToolResult(True, msg, data=data)
        if out.get("gate"):
            return ToolResult(False, str(out.get("message") or "Action requise de ta part sur la page."))
        return ToolResult(False, str(out.get("error") or "erreur navigateur"))

    return handler


_NAVIGATE_AGENTS = ("jarvis", "browser")

registry.add(
    id="browser.navigate", name="Ouvrir une page (navigateur intégré)", category="Navigateur",
    description=(
        "Ouvre une URL dans le navigateur intégré de JARVIS et l'affiche en direct "
        "dans l'aperçu Live Browser (même session visible à l'écran). URL complète "
        "http/https ; « https:// » est ajouté si absent."
    ),
    handler=_live_browser("navigate"), risk=READ_ONLY, agents=_NAVIGATE_AGENTS,
    input_schema={"type": "object", "properties": {
        "url": {"type": "string", "description": "URL à ouvrir (http/https)."},
        "timeout": {"type": "integer", "description": "Délai max de chargement en ms."}},
        "required": ["url"]},
)

registry.add(
    id="browser.click", name="Cliquer dans la page", category="Navigateur",
    description=(
        "Clique sur un élément de la page ouverte dans le navigateur intégré. "
        "Le sélecteur peut être un texte visible, un attribut aria-label/placeholder/title "
        "ou un sélecteur CSS."
    ),
    handler=_live_browser("click"), risk=SAFE_WRITE, agents=_NAVIGATE_AGENTS,
    input_schema={"type": "object", "properties": {
        "selector": {"type": "string", "description": "Texte, aria-label/label ou sélecteur CSS de la cible."}},
        "required": ["selector"]},
)

registry.add(
    id="browser.type", name="Saisir du texte dans la page", category="Navigateur",
    description=(
        "Remplit un champ de la page ouverte dans le navigateur intégré. "
        "Le sélecteur peut être un texte visible, placeholder, label ou sélecteur CSS."
    ),
    handler=_live_browser("type"), risk=SAFE_WRITE, agents=_NAVIGATE_AGENTS,
    input_schema={"type": "object", "properties": {
        "selector": {"type": "string", "description": "Champ cible (placeholder/label/texte/CSS)."},
        "value": {"type": "string", "description": "Texte à saisir."},
        "private": {"type": "boolean", "description": "Cacher la valeur dans les logs (champ mot de passe)."}},
        "required": ["selector", "value"]},
)

registry.add(
    id="browser.scroll", name="Faire défiler la page", category="Navigateur",
    description="Fait défiler verticalement la page ouverte dans le navigateur intégré.",
    handler=_live_browser("scroll"), risk=READ_ONLY, agents=_NAVIGATE_AGENTS,
    input_schema={"type": "object", "properties": {
        "y": {"type": "integer", "description": "Décalage vertical en pixels (négatif = remonter)."}},
        "required": ["y"]},
)

registry.add(
    id="browser.wait", name="Attendre", category="Navigateur",
    description="Pause de quelques millisecondes pour laisser charger la page ou réagir au réseau.",
    handler=_live_browser("wait"), risk=READ_ONLY, agents=_NAVIGATE_AGENTS,
    input_schema={"type": "object", "properties": {
        "ms": {"type": "integer", "description": "Durée d'attente en millisecondes (défaut 800)."}},
    },
)

registry.add(
    id="browser.back", name="Revenir en arrière", category="Navigateur",
    description="Revient à la page précédente dans le navigateur intégré.",
    handler=_live_browser("back"), risk=READ_ONLY, agents=_NAVIGATE_AGENTS,
    input_schema={"type": "object", "properties": {}},
)

registry.add(
    id="browser.pause", name="Laisser l'utilisateur agir", category="Navigateur",
    description=(
        "Laisse la main à l'utilisateur sur la page (captcha, connexion, choix manuel). "
        "L'automatisation se met en attente jusqu'à reprise explicite."
    ),
    handler=_live_browser("pause"), risk=READ_ONLY, agents=_NAVIGATE_AGENTS,
    input_schema={"type": "object", "properties": {
        "message": {"type": "string", "description": "Ce que l'utilisateur doit faire sur la page."}},
    },
)

registry.add(
    id="browser.close", name="Fermer la session navigateur", category="Navigateur",
    description="Ferme la page du navigateur intégré et termine l'aperçu Live Browser.",
    handler=_live_browser("close"), risk=READ_ONLY, agents=_NAVIGATE_AGENTS,
    input_schema={"type": "object", "properties": {}},
)