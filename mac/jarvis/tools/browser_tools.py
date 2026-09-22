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
                "Commande : ./.venv/bin/python -m pip install playwright "
                "puis ./.venv/bin/python -m playwright install chromium (depuis mac/).",
            )
        mgr.start()
        out = mgr.action(op, ctx.arguments, timeout=45)
        # L'événement suit l'action RÉELLE : il est émis après son exécution,
        # avec son résultat. (Il était construit avant l'appel et référençait
        # `out` : NameError avalée par le except, donc jamais diffusé.)
        event_type = {"navigate": "browser.navigate", "close": "browser.closed",
                      "wait": "browser.wait", "read_page": "browser.read",
                      "reload": "browser.navigate"}.get(op, "browser." + op)
        try:
            ctx.core.events.emit(event_type, {
                "op": op, "ok": bool(out.get("ok")),
                "arguments": {k: v for k, v in ctx.arguments.items()
                              if k != "private" and k != "value"},
                "url": out.get("url") or "",
                "target": out.get("target") or out.get("url") or out.get("title") or "",
            }, cache=False)
        except Exception:
            pass
        if out.get("ok"):
            target = out.get("target") or out.get("url") or out.get("title") or ""
            msg = target or _DONE_MESSAGES.get(op, "OK")
            data = {k: v for k, v in out.items() if k != "ok"}
            return ToolResult(True, msg, data=data)
        if out.get("gate"):
            # Authentification, captcha, choix manuel : la mission passe en
            # waiting_user. VELKO reste au poste et n'invente aucune suite.
            message = str(out.get("gate") or out.get("message")
                          or "Action requise de ta part sur la page.")
            try:
                if ctx.task_id:
                    ctx.core.velko_tasks.waiting_user(
                        ctx.task_id, f"Action bloquée : {message}",
                        data={"source": "browser", "url": out.get("url") or ""})
            except Exception:
                pass
            return ToolResult(False, message)
        return ToolResult(False, str(out.get("error") or "erreur navigateur"))

    return handler


# Le développeur teste lui aussi dans le vrai navigateur (cycle bot Discord).
_NAVIGATE_AGENTS = ("jarvis", "browser", "coding", "discord")

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
registry.add(
    id="browser.forward", name="Aller à la page suivante", category="Navigateur",
    description="Avance dans l'historique du navigateur intégré.",
    handler=_live_browser("forward"), risk=READ_ONLY, agents=_NAVIGATE_AGENTS,
    input_schema={"type": "object", "properties": {}},
)

registry.add(
    id="browser.reload", name="Recharger la page", category="Navigateur",
    description="Recharge la page actuellement ouverte dans le navigateur intégré.",
    handler=_live_browser("reload"), risk=READ_ONLY, agents=_NAVIGATE_AGENTS,
    input_schema={"type": "object", "properties": {
        "timeout": {"type": "integer", "description": "Délai max de chargement en ms."}},
    },
)

registry.add(
    id="browser.read_page", name="Lire la page ouverte", category="Navigateur",
    description=(
        "Renvoie le TEXTE RÉEL de la page actuellement ouverte dans le navigateur "
        "intégré. C'est ainsi que l'on vérifie ce qu'affiche vraiment un site : "
        "aucun contenu n'est deviné."
    ),
    handler=_live_browser("read_page"), risk=READ_ONLY, agents=_NAVIGATE_AGENTS,
    input_schema={"type": "object", "properties": {
        "max_chars": {"type": "integer", "description": "Longueur maximale renvoyée (défaut 6000)."}},
    },
)

registry.add(
    id="browser.status", name="État de la session navigateur", category="Navigateur",
    description=(
        "État RÉEL de la session navigateur de VELKO : ouverte ou non, URL et titre "
        "courants, attente éventuelle d'une action de l'utilisateur."
    ),
    handler=_live_browser("status"), risk=READ_ONLY, agents=_NAVIGATE_AGENTS,
    input_schema={"type": "object", "properties": {}},
)


DISCORD_APP = "https://discord.com/channels/@me"
# Repères lus dans la VRAIE page. On exige une preuve POSITIVE d'être connecté :
# l'absence de formulaire de connexion ne suffit pas, une page encore en cours
# de chargement est vide et ferait conclure à tort qu'on est authentifié.
_LOGIN_SIGNS = ("Email or Phone Number", "E-mail ou numéro de téléphone",
                "Log In", "Connexion", "Need an account", "Besoin d'un compte",
                "Log in with QR Code", "Se connecter avec un QR Code",
                "Forgot your password", "Mot de passe oublié")
_APP_SIGNS = ("Find or start a conversation", "Trouver ou démarrer une conversation",
              "Direct Messages", "Messages privés", "Friends", "Amis",
              "Add Friend", "Ajouter un ami", "Online", "En ligne")


def _discord_state(mgr) -> tuple[str, dict]:
    """(état, page) où état vaut « in », « out » ou « unknown ».

    L'état est relu après une courte attente et, si la page est encore vide,
    une seconde fois : mieux vaut deux lectures qu'une conclusion fausse.
    """
    page: dict = {}
    for attempt in range(2):
        mgr.action("wait", {"ms": 2500 if attempt == 0 else 4000}, timeout=20)
        page = mgr.action("read_page", {"max_chars": 6000}, timeout=45)
        text = str(page.get("text") or "")
        if any(sign in text for sign in _LOGIN_SIGNS):
            return "out", page
        if any(sign in text for sign in _APP_SIGNS):
            return "in", page
    return "unknown", page


def _discord_web(ctx: ToolContext) -> ToolResult:
    """Ouvre Discord dans la session persistante de VELKO et dit l'état RÉEL.

    Aucune donnée d'identification n'est demandée ni saisie ici : si la session
    n'est pas authentifiée, VELKO ouvre la vraie page et attend que
    l'utilisateur se connecte lui-même, une seule fois. Le profil Chromium
    étant persistant, la session est ensuite réutilisée.
    """
    from ..browser_manager import get_manager

    mgr = get_manager()
    if mgr is None or not mgr.available():
        return ToolResult(False, "Navigateur intégré indisponible : Playwright n'est pas installé.")
    mgr.start()
    # Une barrière posée lors d'un contrôle précédent bloquerait toutes les
    # opérations : on la lève, puisque ce contrôle-ci sert justement à
    # réévaluer l'état réel de la session.
    mgr.resume()
    opened = mgr.action("navigate", {"url": str(ctx.arguments.get("url") or DISCORD_APP)}, timeout=60)
    if not opened.get("ok"):
        return ToolResult(False, f"Discord web injoignable : {opened.get('error') or 'erreur inconnue'}")
    state, page = _discord_state(mgr)
    url = str(page.get("url") or opened.get("url") or "")
    if state == "in":
        title = str(page.get("title") or "Discord")
        return ToolResult(True,
                          f"Session Discord web active dans le navigateur de VELKO ({title}). "
                          f"URL : {url}",
                          data={"authenticated": True, "url": url, "title": title})
    # Non authentifié — ou état indécidable, ce qui se traite pareil : on ouvre
    # la barrière et on attend l'utilisateur, sans jamais supposer l'accès.
    message = ("Connexion Discord nécessaire dans la fenêtre VELKO."
               if state == "out" else
               "État de la session Discord indécidable : vérifiez la fenêtre VELKO.")
    mgr.request_user(message)
    try:
        if ctx.task_id:
            ctx.core.velko_tasks.waiting_user(ctx.task_id, message,
                                              data={"source": "discord_web", "url": url})
    except Exception:
        pass
    return ToolResult(False, message + " Connectez-vous vous-même sur l'écran de droite : "
                      "la session restera ensuite enregistrée. Je ne demande ni e-mail, "
                      "ni mot de passe, ni code 2FA.",
                      data={"authenticated": False, "state": state, "url": url, "gate": True})


registry.add(
    id="discord.web_session", name="Discord dans le navigateur de VELKO", category="Discord",
    description=(
        "Ouvre Discord dans la session navigateur PERSISTANTE de VELKO et renvoie son état "
        "réel d'authentification. Si la session n'est pas connectée, VELKO affiche la vraie "
        "page de connexion et passe la tâche en waiting_user : l'utilisateur se connecte "
        "lui-même une seule fois, puis la session est réutilisée. Aucune donnée "
        "d'identification n'est jamais demandée ni saisie par VELKO."
    ),
    handler=_discord_web, risk=READ_ONLY, agents=_NAVIGATE_AGENTS,
    input_schema={"type": "object", "properties": {
        "url": {"type": "string", "description": "URL Discord à ouvrir (défaut : l'application)."}},
        "required": []},
)
