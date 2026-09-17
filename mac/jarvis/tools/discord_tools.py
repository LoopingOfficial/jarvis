"""Outils Discord exposés aux agents JARVIS.

Frontière synchrone / asynchrone
--------------------------------
`SecureToolRunner` appelle les handlers en SYNCHRONE (`result = tool.handler(ctx)`).
Déclarer ici des `async def` renverrait des coroutines jamais attendues : le
runner les prendrait pour des résultats et l'action ne serait jamais exécutée.

Les handlers sont donc de fines passerelles : elles soumettent une coroutine à
la boucle du bot (`engine.submit`) et attendent son résultat avec un délai
maximum. TOUT le travail Discord reste asynchrone dans le moteur ; seule cette
couche d'adaptation est synchrone, et elle ne fait rien d'autre qu'attendre.

Aucune de ces fonctions ne manipule le token : le moteur le résout depuis le
coffre et le masque dans toute sortie.
"""
from __future__ import annotations

from typing import Any, Callable

from ..permissions import DESTRUCTIVE, READ_ONLY, SAFE_WRITE, SENSITIVE
from .base import ToolContext, ToolResult, registry

# Délais : une purge ou un lockdown parcourent beaucoup de salons et subissent
# les limites de débit de Discord. Leur laisser 30 s conduirait à un faux échec
# alors que l'action est en cours côté serveur.
TIMEOUT_FAST = 30.0
TIMEOUT_SLOW = 120.0


def _engine(ctx: ToolContext):
    """Moteur Discord du cœur, créé à la demande (import paresseux)."""
    core = ctx.core
    engine = getattr(core, "discord", None)
    if engine is None:
        from ..discord_engine import DiscordEngine
        engine = DiscordEngine(core)
        core.discord = engine
    return engine


def _channel_ref(args: dict[str, Any]) -> str:
    """Salon demandé, quel que soit le nom d'argument choisi par le modèle."""
    for key in ("channel_id", "channel", "channel_name", "salon"):
        value = args.get(key)
        if value not in (None, "", 0):
            return str(value).strip()
    return ""


def _bridge(ctx: ToolContext, make_coro: Callable[[Any], Any], *,
            timeout: float = TIMEOUT_FAST, risk: str = READ_ONLY,
            describe: Callable[[dict[str, Any]], str] | None = None) -> ToolResult:
    """Exécute une coroutine du moteur et normalise erreurs et sorties.

    Les échecs deviennent des ToolResult(ok=False) explicites : un agent doit
    pouvoir lire la raison et changer de stratégie, pas recevoir une trace.
    """
    engine = _engine(ctx)
    if not engine.available:
        return ToolResult(ok=False, risk=risk, output=(
            "discord.py n'est pas installé. Installe-le avec `pip install -U discord.py` "
            "puis redémarre JARVIS."))
    if not engine.connected:
        # Démarrage implicite : demander à l'utilisateur de lancer discord.start
        # avant chaque publication n'apporte rien, et le modèle avait tendance à
        # « répondre » que le message était publié sans jamais rien envoyer.
        started = engine.start()
        if not started.get("ok"):
            return ToolResult(ok=False, risk=risk, data=started, output=(
                "Le bot Discord n'est pas connecté et n'a pas pu démarrer : "
                + str(started.get("error") or "raison inconnue")))
    try:
        data = engine.submit(make_coro(engine), timeout=timeout)
    except TimeoutError as exc:
        return ToolResult(ok=False, risk=risk, output=engine.redact(exc))
    except ValueError as exc:                      # salon/serveur/membre introuvable
        return ToolResult(ok=False, risk=risk, output=engine.redact(exc))
    except Exception as exc:
        return ToolResult(ok=False, risk=risk,
                          output=f"Échec de l'action Discord : {engine.redact(exc)}")
    if isinstance(data, dict) and data.get("ok") is False:
        return ToolResult(ok=False, risk=risk, data=data, output=str(data.get("error") or "Échec."))
    output = describe(data) if (describe and isinstance(data, dict)) else "Action Discord effectuée."
    return ToolResult(ok=True, output=output, data=data, risk=risk)


# ---------------------------------------------------------------------------
# Cycle de vie
# ---------------------------------------------------------------------------
def _status(ctx: ToolContext) -> ToolResult:
    engine = _engine(ctx)
    status = engine.status()
    if not status["available"]:
        return ToolResult(ok=True, data=status,
                          output="discord.py n'est pas installé : intégration indisponible.")
    if not status["connected"]:
        return ToolResult(ok=True, data=status,
                          output=f"Bot hors ligne. Token : {status['token_source']}. "
                                 f"{status['error'] or ''}".strip())
    guilds = ", ".join(g["name"] for g in status["guilds"]) or "aucun serveur"
    return ToolResult(ok=True, data=status,
                      output=f"Bot connecté en tant que {status['user']} sur : {guilds}.")


registry.add(
    id="discord.status", name="État du bot Discord", category="Discord",
    description="Indique si le bot Discord est connecté, sous quelle identité et sur quels serveurs.",
    handler=_status, risk=READ_ONLY, agents=(),
    input_schema={"type": "object", "properties": {}},
)


def _start(ctx: ToolContext) -> ToolResult:
    engine = _engine(ctx)
    result = engine.start()
    if not result.get("ok"):
        return ToolResult(ok=False, risk=SENSITIVE, data=result,
                          output=str(result.get("error") or "Démarrage impossible."))
    return ToolResult(ok=True, risk=SENSITIVE, data=result,
                      output=f"Bot Discord connecté ({result.get('user') or '—'}).")


registry.add(
    id="discord.start", name="Démarrer le bot Discord", category="Discord",
    description=("Démarre le bot Discord dans son propre thread. Le token est lu dans le coffre "
                 "(fiche « discord_bot ») ou dans DISCORD_BOT_TOKEN."),
    handler=_start, risk=SENSITIVE, permissions=("execute",), agents=("jarvis", "system"),
    input_schema={"type": "object", "properties": {}},
)


def _stop(ctx: ToolContext) -> ToolResult:
    result = _engine(ctx).stop()
    return ToolResult(ok=bool(result.get("ok")), risk=SENSITIVE, data=result,
                      output="Bot Discord arrêté." if result.get("stopped") else "Bot déjà arrêté.")


registry.add(
    id="discord.stop", name="Arrêter le bot Discord", category="Discord",
    description="Déconnecte le bot Discord proprement.",
    handler=_stop, risk=SENSITIVE, permissions=("execute",), agents=("jarvis", "system"),
    input_schema={"type": "object", "properties": {}},
)


# ---------------------------------------------------------------------------
# A. Modération
# ---------------------------------------------------------------------------
def _scan(ctx: ToolContext) -> ToolResult:
    """Analyse SANS sanction : pas de message réel, donc pas d'action sur Discord.

    Les sanctions automatiques sont appliquées par le listener `on_message` du
    moteur, qui dispose du message et de son auteur. Cet outil sert à évaluer un
    texte (modération a posteriori, test d'une règle), et il le dit clairement
    plutôt que de laisser croire qu'il a puni quelqu'un.
    """
    from ..discord_engine import classify_message

    content = str(ctx.arguments.get("message_content") or "")
    if not content.strip():
        return ToolResult(ok=False, output="Aucun contenu à analyser.")
    verdict = classify_message(
        content,
        author_is_new=bool(ctx.arguments.get("author_is_new")),
        mention_count=int(ctx.arguments.get("mention_count") or 0),
        repeat_count=int(ctx.arguments.get("repeat_count") or 0),
    ).to_dict()
    verdict["author_id"] = str(ctx.arguments.get("author_id") or "")
    verdict["applied"] = False          # explicite : rien n'a été exécuté sur Discord
    reasons = " ".join(verdict["reasons"]) or "Rien à signaler."
    return ToolResult(
        ok=True, data=verdict, risk=READ_ONLY,
        output=(f"Sévérité {verdict['severity']} — action recommandée : {verdict['action']}. "
                f"{reasons} (analyse seule, aucune sanction appliquée)"))


registry.add(
    id="discord.scan_message", name="Analyser un message Discord", category="Discord",
    description=("Évalue un texte (toxicité, spam, hameçonnage, publicité) et recommande une action. "
                 "N'APPLIQUE AUCUNE sanction : les sanctions automatiques sont gérées en direct par "
                 "le bot à la réception des messages."),
    handler=_scan, risk=READ_ONLY, agents=(),
    input_schema={"type": "object", "properties": {
        "message_content": {"type": "string", "description": "Texte du message à analyser."},
        "author_id": {"type": "string", "description": "Identifiant Discord de l'auteur (traçabilité)."},
        "author_is_new": {"type": "boolean", "description": "L'auteur a rejoint il y a moins de 3 jours."},
        "mention_count": {"type": "integer", "description": "Nombre de mentions dans le message."},
        "repeat_count": {"type": "integer", "description": "Nombre de répétitions récentes du message."}},
        "required": ["message_content"]},
)


def _raid_status(ctx: ToolContext) -> ToolResult:
    engine = _engine(ctx)
    guild_id = int(ctx.arguments.get("guild_id") or 0)
    status = engine.raids.status(guild_id)
    verdict = ("Vague d'arrivées anormale détectée." if status["raid_suspected"]
               else "Rythme d'arrivées normal.")
    return ToolResult(ok=True, data=status, risk=READ_ONLY,
                      output=(f"{verdict} {status['recent_joins']} arrivée(s) en "
                              f"{status['window_seconds']} s (seuil : {status['threshold']})."))


registry.add(
    id="discord.anti_raid_status", name="État anti-raid", category="Discord",
    description=("Indique si un serveur subit une vague d'inscriptions anormale, "
                 "d'après les arrivées observées par le bot depuis son démarrage."),
    handler=_raid_status, risk=READ_ONLY, agents=(),
    input_schema={"type": "object", "properties": {
        "guild_id": {"type": "string", "description": "Identifiant du serveur."}},
        "required": ["guild_id"]},
)


def _lockdown(ctx: ToolContext) -> ToolResult:
    enable = bool(ctx.arguments.get("enable", True))
    guild_id = int(ctx.arguments.get("guild_id") or 0)
    return _bridge(
        ctx, lambda e: e.lockdown(guild_id, enable), timeout=TIMEOUT_SLOW, risk=SENSITIVE,
        describe=lambda d: (f"Lockdown {'activé' if d['enabled'] else 'levé'} : "
                            f"{len(d['channels'])} salon(s) traité(s)"
                            + (f", {len(d['failed'])} échec(s)." if d["failed"] else ".")))


registry.add(
    id="discord.lockdown", name="Verrouiller le serveur", category="Discord",
    description=("Retire (ou rétablit) la permission d'écrire sur tous les salons textuels publics. "
                 "Le déverrouillage restaure l'état d'origine de chaque salon, pas un état ouvert."),
    handler=_lockdown, risk=SENSITIVE, confirmation_policy="always",
    permissions=("admin",), agents=("jarvis", "system"),
    dangerous_hint="Coupe la parole à tout le serveur.",
    input_schema={"type": "object", "properties": {
        "guild_id": {"type": "string", "description": "Identifiant du serveur."},
        "enable": {"type": "boolean", "description": "true = verrouiller, false = déverrouiller."}},
        "required": ["guild_id"]},
)


def _purge(ctx: ToolContext) -> ToolResult:
    channel_id = _channel_ref(ctx.arguments)
    limit = int(ctx.arguments.get("limit") or 50)
    filter_type = str(ctx.arguments.get("filter_type") or "all")
    return _bridge(
        ctx, lambda e: e.purge(channel_id, limit, filter_type), timeout=TIMEOUT_SLOW,
        risk=DESTRUCTIVE,
        describe=lambda d: f"{d['deleted']} message(s) supprimé(s) dans {d['channel']} (filtre : {d['filter']}).")


registry.add(
    id="discord.purge", name="Purger un salon", category="Discord",
    description=("Supprime les derniers messages d'un salon. Filtres : all, bots, links, invites. "
                 "Plafonné à 200 messages par appel."),
    handler=_purge, risk=DESTRUCTIVE, confirmation_policy="always",
    permissions=("destructive",), agents=("jarvis",),
    dangerous_hint="Suppression définitive : Discord ne permet pas de restaurer des messages purgés.",
    input_schema={"type": "object", "properties": {
        "channel_id": {"type": "string", "description": "Identifiant du salon."},
        "limit": {"type": "integer", "description": "Nombre de messages à examiner (max 200)."},
        "filter_type": {"type": "string", "enum": ["all", "bots", "links", "invites"]}},
        "required": ["channel_id"]},
)


# ---------------------------------------------------------------------------
# B. Monitoring
# ---------------------------------------------------------------------------
def _alert(ctx: ToolContext) -> ToolResult:
    channel_id = _channel_ref(ctx.arguments)
    title = str(ctx.arguments.get("title") or "Alerte JARVIS")
    message = str(ctx.arguments.get("message") or "")
    severity = str(ctx.arguments.get("severity") or "INFO").upper()
    return _bridge(ctx, lambda e: e.send_alert(channel_id, title, message, severity),
                   risk=SAFE_WRITE,
                   describe=lambda d: f"Alerte {severity} publiée dans {d['channel']}.")


registry.add(
    id="discord.send_alert", name="Publier une alerte", category="Discord",
    description=("Publie une alerte formatée (embed coloré selon la sévérité) dans un salon : "
                 "panne, erreur système, métrique franchie."),
    handler=_alert, risk=SAFE_WRITE, permissions=("write",), agents=(),
    input_schema={"type": "object", "properties": {
        "channel": {"type": "string", "description": "Nom du salon ou identifiant."},
        "channel_id": {"type": "string", "description": "Alias de `channel`."},
        "title": {"type": "string"},
        "message": {"type": "string"},
        "severity": {"type": "string", "enum": ["INFO", "WARNING", "CRITICAL", "SUCCESS"]}},
        "required": ["channel_id", "message"]},
)


# ---------------------------------------------------------------------------
# C. Animation de communauté
# ---------------------------------------------------------------------------
def _announce(ctx: ToolContext) -> ToolResult:
    channel_id = _channel_ref(ctx.arguments)
    raw = str(ctx.arguments.get("raw_prompt") or "")
    ping = ctx.arguments.get("ping_role_id")
    return _bridge(ctx, lambda e: e.publish_announcement(channel_id, raw,
                                                         int(ping) if ping else None),
                   timeout=TIMEOUT_SLOW, risk=SENSITIVE,
                   describe=lambda d: f"Annonce publiée : « {d['title']} ».")


registry.add(
    id="discord.publish_announcement", name="Publier une annonce", category="Discord",
    description=("Met en forme une idée brute en annonce professionnelle via le modèle, "
                 "puis la publie en embed, avec mention de rôle facultative."),
    handler=_announce, risk=SENSITIVE, confirmation_policy="always",
    permissions=("write",), agents=("jarvis", "cmo"),
    dangerous_hint="Publication visible par tous les membres du salon.",
    input_schema={"type": "object", "properties": {
        "channel_id": {"type": "string"},
        "raw_prompt": {"type": "string", "description": "Idée brute à mettre en forme."},
        "ping_role_id": {"type": "string", "description": "Rôle à mentionner (facultatif)."}},
        "required": ["channel_id", "raw_prompt"]},
)


def _summarize(ctx: ToolContext) -> ToolResult:
    channel_id = _channel_ref(ctx.arguments)
    count = int(ctx.arguments.get("message_count") or 100)
    return _bridge(ctx, lambda e: e.summarize_channel(channel_id, count), timeout=TIMEOUT_SLOW,
                   risk=READ_ONLY,
                   describe=lambda d: f"Résumé de {d['messages_read']} message(s) :\n{d['summary']}")


registry.add(
    id="discord.summarize_channel", name="Résumer un salon", category="Discord",
    description="Lit les derniers messages d'un salon et en produit un résumé synthétique.",
    handler=_summarize, risk=READ_ONLY, agents=(),
    input_schema={"type": "object", "properties": {
        "channel_id": {"type": "string"},
        "message_count": {"type": "integer", "description": "Messages à lire (5 à 300)."}},
        "required": ["channel_id"]},
)


def _welcome(ctx: ToolContext) -> ToolResult:
    member_id = int(ctx.arguments.get("member_id") or 0)
    guild_id = int(ctx.arguments.get("guild_id") or 0)
    roles = ctx.arguments.get("roles") or []
    if isinstance(roles, str):
        roles = [r.strip() for r in roles.split(",") if r.strip()]
    return _bridge(ctx, lambda e: e.welcome(member_id, guild_id, roles), timeout=TIMEOUT_SLOW,
                   risk=SAFE_WRITE,
                   describe=lambda d: (f"Bienvenue envoyée à {d['member']} "
                                       f"({'MP' if d['dm_sent'] else 'salon public'})"
                                       + (f", rôles : {', '.join(d['roles_granted'])}."
                                          if d["roles_granted"] else ".")))


registry.add(
    id="discord.welcome_onboarding", name="Accueillir un membre", category="Discord",
    description=("Envoie un message de bienvenue rédigé par le modèle (en MP, sinon dans #bienvenue) "
                 "et attribue les rôles de base demandés."),
    handler=_welcome, risk=SAFE_WRITE, permissions=("write",), agents=(),
    input_schema={"type": "object", "properties": {
        "member_id": {"type": "string"}, "guild_id": {"type": "string"},
        "roles": {"type": "array", "items": {"type": "string"},
                  "description": "Noms des rôles à attribuer."}},
        "required": ["member_id", "guild_id"]},
)


# ---------------------------------------------------------------------------
# D. Support
# ---------------------------------------------------------------------------
def _ticket(ctx: ToolContext) -> ToolResult:
    channel_id = _channel_ref({"channel": ctx.arguments.get("ticket_channel_id")})
    message = str(ctx.arguments.get("user_message") or "")
    return _bridge(ctx, lambda e: e.handle_ticket(channel_id, message), timeout=TIMEOUT_SLOW,
                   risk=SAFE_WRITE,
                   describe=lambda d: (f"Réponse publiée ({d['sources']} source(s) de la base). "
                                       + ("Piste de résolution proposée." if d["auto_resolved"]
                                          else "Escalade recommandée : rien de documenté.")))


registry.add(
    id="discord.handle_ticket", name="Répondre à un ticket", category="Discord",
    description=("Cherche dans la base de connaissances de JARVIS et publie une première réponse "
                 "dans le salon du ticket. Si rien n'est documenté, le dit et recommande l'escalade "
                 "au lieu d'inventer une procédure."),
    handler=_ticket, risk=SAFE_WRITE, permissions=("write",), agents=("jarvis", "ops"),
    input_schema={"type": "object", "properties": {
        "ticket_channel_id": {"type": "string"},
        "user_message": {"type": "string", "description": "Demande du client."}},
        "required": ["ticket_channel_id", "user_message"]},
)


def _escalate(ctx: ToolContext) -> ToolResult:
    channel_id = _channel_ref({"channel": ctx.arguments.get("ticket_channel_id")})
    reason = str(ctx.arguments.get("reason") or "Escalade demandée.")
    role = str(ctx.arguments.get("support_role") or "Support")
    return _bridge(ctx, lambda e: e.escalate_ticket(channel_id, reason, role), timeout=TIMEOUT_SLOW,
                   risk=SAFE_WRITE,
                   describe=lambda d: (f"Ticket escaladé dans {d['channel']}"
                                       + (" (rôle support notifié)" if d["role_notified"]
                                          else " (rôle support introuvable)")
                                       + (f", fiche CRM {d['crm_interaction_id']}."
                                          if d["crm_interaction_id"] else ".")))


registry.add(
    id="discord.escalate_ticket", name="Escalader un ticket", category="Discord",
    description=("Donne au rôle support l'accès au salon du ticket, y publie un résumé, "
                 "et enregistre une interaction dans le CRM."),
    handler=_escalate, risk=SAFE_WRITE, permissions=("write",), agents=("jarvis", "ops"),
    input_schema={"type": "object", "properties": {
        "ticket_channel_id": {"type": "string"},
        "reason": {"type": "string"},
        "support_role": {"type": "string", "description": "Nom du rôle support (défaut : Support)."}},
        "required": ["ticket_channel_id", "reason"]},
)


# ---------------------------------------------------------------------------
# E. Passerelles CRM et pilotage
# ---------------------------------------------------------------------------
def _quick_lead(ctx: ToolContext) -> ToolResult:
    user_id = int(ctx.arguments.get("user_id") or 0)
    guild_id = int(ctx.arguments.get("guild_id") or 0)
    notes = str(ctx.arguments.get("notes") or "")
    return _bridge(ctx, lambda e: e.crm_quick_lead(user_id, guild_id, notes), risk=SAFE_WRITE,
                   describe=lambda d: (f"{d['name']} enregistré comme prospect (fiche {d['contact_id']})"
                                       + (f", score {d['scoring']['score']}." if d.get("scoring") else ".")))


registry.add(
    id="discord.crm_quick_lead", name="Créer un prospect depuis Discord", category="Discord",
    description=("Enregistre un membre Discord comme prospect dans le CRM (pseudo, rôles, serveur) "
                 "et journalise l'interaction. Aucun e-mail n'est inventé : Discord n'en fournit pas."),
    handler=_quick_lead, risk=SAFE_WRITE, permissions=("write",), agents=("jarvis", "cmo", "ops"),
    input_schema={"type": "object", "properties": {
        "user_id": {"type": "string"}, "guild_id": {"type": "string"},
        "notes": {"type": "string", "description": "Contexte : ce que la personne cherche."}},
        "required": ["user_id", "guild_id"]},
)


def _remote(ctx: ToolContext) -> ToolResult:
    admin_id = str(ctx.arguments.get("admin_id") or "")
    command = str(ctx.arguments.get("command") or "")
    return _bridge(ctx, lambda e: e.remote_command(admin_id, command), timeout=TIMEOUT_SLOW,
                   risk=DESTRUCTIVE,
                   describe=lambda d: f"Commande « {d['command']} » exécutée : {d['data']}")


registry.add(
    id="discord.remote_command", name="Piloter JARVIS depuis Discord", category="Discord",
    description=("Exécute une commande d'administration demandée depuis Discord (status, run-task). "
                 "L'auteur doit figurer dans integrations.discord_admins : le rôle Discord ne suffit pas. "
                 "Le redémarrage est volontairement refusé."),
    handler=_remote, risk=DESTRUCTIVE, confirmation_policy="always",
    permissions=("admin",), agents=("jarvis",),
    dangerous_hint="Pilotage de JARVIS depuis un canal externe.",
    input_schema={"type": "object", "properties": {
        "admin_id": {"type": "string", "description": "Identifiant Discord du demandeur."},
        "command": {"type": "string", "description": "status | run-task <description>"}},
        "required": ["admin_id", "command"]},
)


# ---------------------------------------------------------------------------
# F. Publication simple (briques des tâches planifiées)
# ---------------------------------------------------------------------------
def _send_message(ctx: ToolContext) -> ToolResult:
    channel = _channel_ref(ctx.arguments)
    content = str(ctx.arguments.get("content") or ctx.arguments.get("message") or "")
    if not channel:
        return ToolResult(ok=False, risk=SAFE_WRITE,
                          output="Indique le salon (nom ou identifiant) avec `channel`.")
    if not content.strip():
        return ToolResult(ok=False, risk=SAFE_WRITE, output="Le message est vide.")
    return _bridge(ctx, lambda e: e.send_message(channel, content), risk=SAFE_WRITE,
                   describe=lambda d: f"Message publié dans #{d['channel']} (id {d.get('message_id')}).")


registry.add(
    id="discord.send_message", name="Envoyer un message", category="Discord",
    description=("Publie réellement un message texte dans un salon Discord. À UTILISER "
                 "dès qu'on demande de publier, poster, envoyer ou tester un message sur "
                 "Discord : ne jamais affirmer qu'un message est publié sans avoir appelé "
                 "cet outil. Le salon s'indique par son NOM (ex. « commandes-staff », les emoji "
                 "et décorations sont ignorés) ou par son identifiant. Le bot est "
                 "démarré automatiquement si besoin. Les mentions de masse (@everyone, "
                 "@here) sont neutralisées."),
    handler=_send_message, risk=SAFE_WRITE, permissions=("write",), agents=(),
    input_schema={"type": "object", "properties": {
        "channel": {"type": "string", "description": "Nom du salon (#commandes-staff) ou identifiant."},
        "channel_id": {"type": "string", "description": "Alias de `channel`."},
        "content": {"type": "string", "description": "Texte à publier (2000 caractères max)."}},
        "required": ["channel", "content"]},
)


def _list_channels(ctx: ToolContext) -> ToolResult:
    query = str(ctx.arguments.get("query") or "")

    def describe(data: dict[str, Any]) -> str:
        rows = data.get("channels") or []
        if not rows:
            return "Aucun salon texte visible par le bot."
        return "Salons : " + ", ".join(
            f"#{r['name']} ({r['id']}{'' if r['can_send'] else ', écriture refusée'})"
            for r in rows[:40])

    return _bridge(ctx, lambda e: e.list_channels(query), risk=READ_ONLY, describe=describe)


registry.add(
    id="discord.list_channels", name="Lister les salons Discord", category="Discord",
    description=("Liste les salons texte visibles par le bot, avec leur identifiant et le droit "
                 "d'y écrire. Utile pour retrouver un salon avant de publier, ou pour lever "
                 "une ambiguïté de nom."),
    handler=_list_channels, risk=READ_ONLY, agents=(),
    input_schema={"type": "object", "properties": {
        "query": {"type": "string", "description": "Filtre facultatif sur le nom du salon."}}},
)


def _delete_message(ctx: ToolContext) -> ToolResult:
    channel = _channel_ref(ctx.arguments)
    raw = str(ctx.arguments.get("message_id") or "").strip()
    if not channel or not raw.isdigit():
        return ToolResult(ok=False, risk=SENSITIVE,
                          output="Indique le salon et l'identifiant numérique du message.")
    return _bridge(ctx, lambda e: e.delete_message(channel, int(raw)), risk=SENSITIVE,
                   describe=lambda r: f"Message {r['message_id']} supprimé de #{r['channel']}.")


registry.add(
    id="discord.delete_message", name="Supprimer un message", category="Discord",
    description=("Supprime UN message précis, désigné par son identifiant. Contrairement à "
                 "discord.purge, rien d'autre ne peut disparaître. Suppression définitive : "
                 "elle est inscrite dans le journal d'audit."),
    handler=_delete_message, risk=SENSITIVE, confirmation_policy="always",
    permissions=("write",), agents=(),
    dangerous_hint="Le message sera définitivement supprimé de Discord.",
    input_schema={"type": "object", "properties": {
        "channel": {"type": "string", "description": "Nom du salon ou identifiant."},
        "channel_id": {"type": "string", "description": "Alias de `channel`."},
        "message_id": {"type": "string", "description": "Identifiant du message à supprimer."}},
        "required": ["channel", "message_id"]},
)


def _send_embed(ctx: ToolContext) -> ToolResult:
    channel = _channel_ref(ctx.arguments)
    if not channel:
        return ToolResult(ok=False, risk=SAFE_WRITE,
                          output="Indique le salon (nom ou identifiant) avec `channel`.")
    title = str(ctx.arguments.get("title") or "JARVIS")
    description = str(ctx.arguments.get("description") or ctx.arguments.get("message") or "")
    severity = str(ctx.arguments.get("severity") or "INFO").upper()
    fields = ctx.arguments.get("fields") or []
    image = str(ctx.arguments.get("image") or ctx.arguments.get("image_url") or "")
    thumbnail = str(ctx.arguments.get("thumbnail") or "")
    footer = str(ctx.arguments.get("footer") or "JARVIS")
    inline = bool(ctx.arguments.get("inline"))
    return _bridge(ctx, lambda e: e.send_embed(channel, title, description, severity, fields,
                                               inline=inline, image=image, thumbnail=thumbnail,
                                               footer=footer),
                   risk=SAFE_WRITE,
                   describe=lambda d: (f"Embed « {d['title']} » publié dans #{d['channel']} "
                                       f"(id {d.get('message_id')})."))


registry.add(
    id="discord.send_embed", name="Envoyer un embed", category="Discord",
    description=("Publie un message formaté dans un salon : embed coloré, champs, bannière "
                 "(URL ou fichier local, GIF animé accepté), vignette et pied de page."),
    handler=_send_embed, risk=SAFE_WRITE, permissions=("write",), agents=(),
    input_schema={"type": "object", "properties": {
        "channel": {"type": "string", "description": "Nom du salon ou identifiant."},
        "channel_id": {"type": "string", "description": "Alias de `channel`."},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "severity": {"type": "string", "enum": ["INFO", "WARNING", "CRITICAL", "SUCCESS"]},
        "image": {"type": "string", "description": ("Bannière affichée en grand : URL http(s) ou "
                                                    "chemin de fichier local (un GIF reste animé, "
                                                    "8 Mo maximum).")},
        "thumbnail": {"type": "string", "description": "Vignette en haut à droite : URL ou fichier local."},
        "footer": {"type": "string", "description": "Texte du pied de page (« JARVIS » par défaut)."},
        "inline": {"type": "boolean", "description": "Champs affichés côte à côte plutôt qu'empilés."},
        "fields": {"type": "array", "description": "Champs [{name, value}] facultatifs.",
                   "items": {"type": "object", "properties": {
                       "name": {"type": "string"}, "value": {"type": "string"}}}}},
        "required": ["channel", "description"]},
)


# ---------------------------------------------------------------------------
# G. Tâches Discord planifiées
# ---------------------------------------------------------------------------
def _scheduler(ctx: ToolContext):
    """Planificateur du cœur, créé à la demande comme le moteur Discord."""
    core = ctx.core
    scheduler = getattr(core, "discord_scheduler", None)
    if scheduler is None:
        from ..discord_scheduler import DiscordScheduler
        scheduler = DiscordScheduler(core)
        core.discord_scheduler = scheduler
    return scheduler


def _schedule_add(ctx: ToolContext) -> ToolResult:
    from ..discord_scheduler import ScheduleError, describe_interval

    args = ctx.arguments
    params = args.get("params") or {}
    if isinstance(params, str):                     # le modèle envoie parfois du JSON en texte
        import json
        try:
            params = json.loads(params)
        except Exception:
            return ToolResult(ok=False, risk=SENSITIVE, output="`params` doit être un objet JSON.")
    if not isinstance(params, dict):
        return ToolResult(ok=False, risk=SENSITIVE, output="`params` doit être un objet JSON.")
    try:
        task = _scheduler(ctx).add(
            name=str(args.get("name") or ""),
            interval=args.get("interval"),
            tool_to_call=str(args.get("tool_to_call") or ""),
            target_channel_id=str(args.get("target_channel_id") or ""),
            params=params,
            status=str(args.get("status") or "ACTIVE"),
            allow_destructive=bool(args.get("allow_destructive")),
            source=f"agent:{ctx.agent}",
        )
    except ScheduleError as exc:
        return ToolResult(ok=False, risk=SENSITIVE, output=str(exc))
    return ToolResult(ok=True, risk=SENSITIVE, data=task,
                      output=(f"Tâche « {task['name']} » planifiée "
                              f"({describe_interval(task['trigger'])}), identifiant {task['id']}. "
                              f"Prochaine exécution : {task['next_run_iso'] or 'suspendue'}."))


registry.add(
    id="discord.schedule_add", name="Planifier une action Discord", category="Discord",
    description=("Programme l'exécution récurrente d'un outil Discord : intervalle (« 30m », « 2h »), "
                 "heure fixe (« 09:00 ») ou expression cron (« 0 9 * * * »). "
                 "Seuls les outils discord.* sont planifiables."),
    handler=_schedule_add, risk=SENSITIVE, confirmation_policy="always",
    permissions=("write",), agents=("jarvis", "system", "ops", "cmo"),
    dangerous_hint="L'action se répétera sans nouvelle validation à chaque exécution.",
    input_schema={"type": "object", "properties": {
        "name": {"type": "string", "description": "Nom de la tâche (ex. « Annonce matinale »)."},
        "interval": {"type": "string",
                     "description": "« 30m », « 2h », « 09:00 » ou cron « 0 9 * * * »."},
        "tool_to_call": {"type": "string",
                         "description": ("Outil à déclencher : discord.send_message, discord.send_embed, "
                                         "discord.send_alert, discord.purge, discord.summarize_channel…")},
        "target_channel_id": {"type": "string", "description": "Salon concerné."},
        "params": {"type": "object", "description": "Paramètres passés à l'outil."},
        "status": {"type": "string", "enum": ["ACTIVE", "PAUSED"]},
        "allow_destructive": {"type": "boolean",
                              "description": "Obligatoire pour planifier une action irréversible (purge)."}},
        "required": ["name", "interval", "tool_to_call"]},
)


def _schedule_list(ctx: ToolContext) -> ToolResult:
    scheduler = _scheduler(ctx)
    status = str(ctx.arguments.get("status") or "")
    tasks = scheduler.list(status=status)
    if not tasks:
        return ToolResult(ok=True, data={"tasks": []}, risk=READ_ONLY,
                          output="Aucune tâche Discord planifiée.")
    lines = [(f"- [{t['status']}] {t['name']} ({t['id']}) — {t['tool_to_call']} {t['interval']}, "
              f"salon {t['target_channel_id'] or '—'}, prochaine : {t['next_run_iso'] or '—'}, "
              f"{t['run_count']} exécution(s), dernier statut : {t['last_status'] or '—'}")
             for t in tasks]
    return ToolResult(ok=True, risk=READ_ONLY,
                      data={"tasks": tasks, "runs": scheduler.runs(limit=20)},
                      output="Tâches Discord planifiées :\n" + "\n".join(lines))


registry.add(
    id="discord.schedule_list", name="Lister les actions planifiées", category="Discord",
    description="Liste les tâches Discord planifiées, leur statut et leur prochaine exécution.",
    handler=_schedule_list, risk=READ_ONLY, agents=(),
    input_schema={"type": "object", "properties": {
        "status": {"type": "string", "enum": ["ACTIVE", "PAUSED"],
                   "description": "Filtre facultatif."}}},
)


def _schedule_delete(ctx: ToolContext) -> ToolResult:
    from ..discord_scheduler import ScheduleError

    scheduler = _scheduler(ctx)
    task_id = str(ctx.arguments.get("task_id") or "")
    mode = str(ctx.arguments.get("mode") or "delete").lower()
    task = scheduler.get(task_id)
    if not task:
        return ToolResult(ok=False, risk=SENSITIVE,
                          output=(f"Aucune tâche planifiée « {task_id} ». "
                                  f"Utilise discord.schedule_list pour retrouver son identifiant."))
    try:
        if mode == "pause":
            updated = scheduler.set_status(task_id, "PAUSED")
            return ToolResult(ok=True, risk=SENSITIVE, data=updated,
                              output=f"Tâche « {task['name']} » suspendue (elle reste enregistrée).")
        if mode == "resume":
            updated = scheduler.set_status(task_id, "ACTIVE")
            return ToolResult(ok=True, risk=SENSITIVE, data=updated,
                              output=(f"Tâche « {task['name']} » réactivée. Prochaine exécution : "
                                      f"{updated['next_run_iso']}."))
    except ScheduleError as exc:
        return ToolResult(ok=False, risk=SENSITIVE, output=str(exc))
    scheduler.delete(task_id)
    return ToolResult(ok=True, risk=SENSITIVE, data={"deleted": task_id},
                      output=f"Tâche « {task['name']} » supprimée.")


registry.add(
    id="discord.schedule_delete", name="Supprimer une action planifiée", category="Discord",
    description=("Supprime une tâche Discord planifiée, ou la suspend / réactive "
                 "(mode : delete | pause | resume)."),
    handler=_schedule_delete, risk=SENSITIVE, permissions=("write",),
    agents=("jarvis", "system", "ops", "cmo"),
    input_schema={"type": "object", "properties": {
        "task_id": {"type": "string", "description": "Identifiant de la tâche planifiée."},
        "mode": {"type": "string", "enum": ["delete", "pause", "resume"]}},
        "required": ["task_id"]},
)


# ---------------------------------------------------------------------------
# D. Appel vocal matinal
# ---------------------------------------------------------------------------
def _call_manager(ctx: ToolContext):
    """Planificateur de l'appel matinal, porté par le cœur."""
    manager = getattr(ctx.core, "discord_call", None)
    if manager is None:
        from ..discord_call import MorningCallManager
        manager = MorningCallManager(ctx.core)
        ctx.core.discord_call = manager
    return manager


def _call_status(ctx: ToolContext) -> ToolResult:
    status = _call_manager(ctx).status()
    state = "activé" if status["enabled"] else "désactivé"
    heure = f"{status['hour']:02d}h{status['minute']:02d}"
    lines = [f"Appel matinal {state}, prévu à {heure}."]
    if status["next_run_iso"]:
        lines.append(f"Prochain déclenchement : {status['next_run_iso']}"
                     + (" (rappel reporté)" if status["snoozed"] else "") + ".")
    caps = status["capabilities"]
    lines.append("Voix : {}. Écoute : {}. Transcription : {}.".format(
        "oui" if caps["tts"] else "non",
        "oui" if caps["voice_receive"] else "non",
        "oui" if caps["transcription"] else "non"))
    lines.extend(status["hints"])
    return ToolResult(ok=True, data=status, output=" ".join(lines))


registry.add(
    id="discord.morning_call_status", name="État de l'appel matinal", category="Discord",
    description=("Indique si le rapport matinal par appel vocal Discord est actif, à quelle heure, "
                 "et ce qui manque éventuellement (voix, écoute, transcription, salons)."),
    handler=_call_status, risk=READ_ONLY, agents=(),
    input_schema={"type": "object", "properties": {}},
)


def _call_now(ctx: ToolContext) -> ToolResult:
    """Déclenche l'appel tout de suite. L'outil attend la fin de l'appel.

    C'est voulu : le résultat dit ce qui s'est réellement passé (rapport lu,
    reporté, personne dans le salon), plutôt qu'un « appel lancé » que rien ne
    viendrait confirmer.
    """
    result = _call_manager(ctx).run_now(reason=str(ctx.arguments.get("reason") or "manuel"))
    labels = {"delivered": "Rapport lu à voix haute dans le salon vocal.",
              "posted": "Rapport publié à l'écrit (appel vocal impossible).",
              "snoozed": "Rapport reporté à ta demande.",
              "cancelled": "Rapport annulé à ta demande.",
              "no_show": "Personne n'a rejoint le salon vocal ; rapport laissé à l'écrit.",
              "failed": "Appel impossible."}
    outcome = str(result.get("outcome") or "failed")
    detail = str(result.get("detail") or "")
    return ToolResult(ok=bool(result.get("ok")), risk=SAFE_WRITE, data=result,
                      output=f"{labels.get(outcome, outcome)} {detail}".strip())


registry.add(
    id="discord.morning_call", name="Lancer l'appel vocal matinal", category="Discord",
    description=("Collecte le rapport (mails, tâches, projets, notifications, système), pingue le "
                 "salon privé, rejoint le salon vocal et lit le rapport à voix haute. Attend la fin "
                 "de l'appel avant de répondre."),
    handler=_call_now, risk=SAFE_WRITE, permissions=("write",),
    agents=("jarvis", "system", "ops"),
    input_schema={"type": "object", "properties": {
        "reason": {"type": "string", "description": "Motif consigné dans l'audit."}}},
)
