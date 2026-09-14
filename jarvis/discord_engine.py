"""Moteur Discord — bot asynchrone isolé du serveur JARVIS.

Architecture
------------
Le bot vit dans SON PROPRE thread, avec SA PROPRE boucle asyncio. Le serveur
HTTP de JARVIS est un `ThreadingHTTPServer` synchrone : y greffer une boucle
asyncio partagée aurait couplé la disponibilité de l'interface à celle de
Discord. Ici, si Discord tombe, JARVIS ne s'en aperçoit pas.

    Agent/LLM → outil (SYNCHRONE, contrainte du SecureToolRunner)
              → submit(coro) → run_coroutine_threadsafe
              → boucle du bot (ASYNCHRONE) → API Discord

Tout ce qui touche Discord est `async`. La frontière synchrone se limite à
`submit()`, qui attend le résultat avec un délai maximum : un outil ne peut donc
pas rester bloqué indéfiniment si Discord ne répond plus.

`core.llm.chat()` étant synchrone, les appels au modèle passent par
`run_in_executor` : les exécuter directement gèlerait la boucle du bot, et donc
le heartbeat, ce que Discord interprète comme une déconnexion.

Sécurité
--------
  * Le token vient du coffre (`core.credentials`) ou de `DISCORD_BOT_TOKEN`.
    Il n'est jamais journalisé : `redact()` le retire de tout texte sortant, en
    plus du `scrub()` du coffre.
  * Toute action de modération est inscrite dans `audit_log`.
  * Les décisions de modération sont explicables : règles déterministes
    d'abord, avis du LLM ensuite — et le motif est toujours restitué.

`discord.py` est une dépendance OPTIONNELLE : le module s'importe et
s'interroge sans elle (statut « indisponible »), comme `secrets.py` le fait
pour `cryptography`. JARVIS ne doit pas refuser de démarrer parce qu'une
intégration facultative manque.
"""
from __future__ import annotations

import asyncio
import os
import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable

try:  # pragma: no cover - dépend de l'environnement
    import discord
    from discord.ext import commands

    HAVE_DISCORD = True
except Exception:  # pragma: no cover
    discord = None  # type: ignore
    commands = None  # type: ignore
    HAVE_DISCORD = False

CREDENTIAL_ID = "discord_bot"
DEFAULT_TIMEOUT = 30.0

# Sévérités, de la plus bénigne à la plus grave. L'ordre porte la logique
# d'escalade : on ne descend jamais d'un cran.
SEV_NONE, SEV_LOW, SEV_MEDIUM, SEV_HIGH, SEV_CRITICAL = "none", "low", "medium", "high", "critical"
SEVERITY_ORDER = {SEV_NONE: 0, SEV_LOW: 1, SEV_MEDIUM: 2, SEV_HIGH: 3, SEV_CRITICAL: 4}

# Couleurs des embeds d'alerte (RGB Discord).
ALERT_COLORS = {"INFO": 0x22D3EE, "WARNING": 0xFBBF24, "CRITICAL": 0xFB7185, "SUCCESS": 0x34D399}
SEVERITY_COLORS = {SEV_NONE: 0x34D399, SEV_LOW: 0x22D3EE, SEV_MEDIUM: 0xFBBF24,
                   SEV_HIGH: 0xF97316, SEV_CRITICAL: 0xFB7185}

# ---------------------------------------------------------------------------
# Détection déterministe. Première ligne de défense : elle s'applique même si
# aucun modèle n'est connecté, et son verdict est toujours explicable.
# ---------------------------------------------------------------------------
INVITE_RE = re.compile(r"(discord\.(gg|io|me|li)|discordapp\.com/invite)/\w+", re.I)
URL_RE = re.compile(r"https?://[^\s<>\"]+", re.I)
MENTION_RE = re.compile(r"<@[!&]?\d+>")

# Domaines de hameçonnage classiques visant les comptes Discord/Steam.
PHISHING_PATTERNS = [
    r"disc[o0]rd[-.]?(nitro|gift|give)", r"steamcommunity\.[a-z]{2,}(?<!\.com)",
    r"free\s*nitro", r"nitro\s*gratuit", r"claim\s*your\s*(gift|nitro)",
    r"\bairdrop\b", r"connecte[sz]?[- ]vous\s+avec\s+votre\s+compte",
]
TOXIC_PATTERNS = [
    r"\bta gueule\b", r"\bferme[- ]la\b", r"\bconnard\b", r"\bsalope\b", r"\bencul", r"\bfdp\b",
    r"\bnique\b", r"\bpd\b", r"\bdebile\b", r"\bfuck you\b", r"\bkill yourself\b", r"\bkys\b",
]
THREAT_PATTERNS = [r"\bje vais te (tuer|frapper|retrouver)\b", r"\bdox+\b", r"\bswat+ing\b",
                   r"\bi will kill you\b"]


def _fold(text: str) -> str:
    """Minuscules sans accents : « débile » et « debile » se rejoignent."""
    import unicodedata
    s = str(text or "").lower()
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


@dataclass
class Verdict:
    """Décision de modération, toujours motivée."""
    severity: str = SEV_NONE
    action: str = "none"           # none | warn | delete | timeout | kick | ban
    reasons: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    timeout_seconds: int = 0
    source: str = "rules"          # rules | rules+llm

    def to_dict(self) -> dict[str, Any]:
        return {"severity": self.severity, "action": self.action, "reasons": self.reasons,
                "categories": self.categories, "timeout_seconds": self.timeout_seconds,
                "source": self.source}


def classify_message(content: str, *, author_is_new: bool = False,
                     mention_count: int = 0, repeat_count: int = 0) -> Verdict:
    """Analyse un message par règles. Testable sans Discord ni modèle.

    `repeat_count` = nombre de fois où ce même contenu vient d'être posté par
    cet auteur : c'est ce qui distingue un message anodin d'un spam de masse.
    """
    text = _fold(content)
    reasons: list[str] = []
    categories: list[str] = []
    severity = SEV_NONE

    def bump(level: str, reason: str, category: str) -> None:
        nonlocal severity
        if SEVERITY_ORDER[level] > SEVERITY_ORDER[severity]:
            severity = level
        reasons.append(reason)
        if category not in categories:
            categories.append(category)

    if any(re.search(p, text) for p in PHISHING_PATTERNS):
        bump(SEV_CRITICAL, "Motif de hameçonnage connu (faux Nitro / cadeau / airdrop).", "phishing")
    if any(re.search(p, text) for p in THREAT_PATTERNS):
        bump(SEV_CRITICAL, "Menace explicite envers une personne.", "menace")
    if any(re.search(p, text) for p in TOXIC_PATTERNS):
        bump(SEV_MEDIUM, "Insulte détectée.", "toxicite")
    if INVITE_RE.search(content or ""):
        # Une invitation postée par un compte tout juste arrivé est le schéma
        # classique du bot publicitaire ; postée par un habitué, c'est anodin.
        bump(SEV_HIGH if author_is_new else SEV_LOW, "Invitation vers un autre serveur.", "publicite")
    if mention_count >= 8:
        bump(SEV_HIGH, f"Mentions en masse ({mention_count}).", "spam")
    if repeat_count >= 4:
        bump(SEV_HIGH, f"Message identique répété {repeat_count} fois.", "spam")
    elif repeat_count >= 3:
        bump(SEV_MEDIUM, f"Message répété {repeat_count} fois.", "spam")

    letters = [c for c in (content or "") if c.isalpha()]
    if len(letters) >= 15 and sum(1 for c in letters if c.isupper()) / len(letters) > 0.8:
        bump(SEV_LOW, "Message majoritairement en majuscules.", "forme")

    actions = {SEV_NONE: "none", SEV_LOW: "warn", SEV_MEDIUM: "delete",
               SEV_HIGH: "timeout", SEV_CRITICAL: "ban"}
    timeouts = {SEV_HIGH: 3600, SEV_CRITICAL: 0}
    return Verdict(severity=severity, action=actions[severity], reasons=reasons,
                   categories=categories, timeout_seconds=timeouts.get(severity, 0))


class RaidTracker:
    """Fenêtre glissante des arrivées, par serveur.

    Un raid se reconnaît à la DENSITÉ des arrivées, pas à leur nombre : dix
    inscriptions dans la journée sont normales, dix en trente secondes ne le
    sont pas.
    """

    def __init__(self, threshold: int = 8, window_seconds: int = 30) -> None:
        self.threshold = threshold
        self.window = window_seconds
        self._joins: dict[int, deque[float]] = defaultdict(deque)

    def record(self, guild_id: int, at: float | None = None) -> None:
        self._joins[guild_id].append(time.time() if at is None else at)
        self._prune(guild_id)

    def _prune(self, guild_id: int) -> None:
        cutoff = time.time() - self.window
        joins = self._joins[guild_id]
        while joins and joins[0] < cutoff:
            joins.popleft()

    def status(self, guild_id: int) -> dict[str, Any]:
        self._prune(guild_id)
        recent = len(self._joins[guild_id])
        return {"guild_id": guild_id, "recent_joins": recent, "threshold": self.threshold,
                "window_seconds": self.window, "raid_suspected": recent >= self.threshold}


class DiscordEngine:
    """Cycle de vie du bot + opérations asynchrones exposées aux outils."""

    def __init__(self, core) -> None:
        self._core = core
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._bot: Any = None
        self._ready = threading.Event()
        # `_settled` = « la tentative de connexion a rendu son verdict », succès
        # ou échec. Sans lui, `start()` attendrait le délai complet alors qu'un
        # token invalide est rejeté par Discord en une fraction de seconde.
        self._settled = threading.Event()
        self._token = ""
        self._error = ""
        self._started_at = 0.0
        self.raids = RaidTracker()
        self._recent: dict[int, deque[str]] = defaultdict(lambda: deque(maxlen=6))
        self._lockdowns: dict[int, dict[int, Any]] = {}     # guild -> {channel: état précédent}
        self.settings_key = ("integrations", "discord")

    # -- secrets ----------------------------------------------------------
    def _resolve_token(self) -> str:
        """Coffre d'abord, variable d'environnement ensuite.

        Le coffre trace l'accès ; l'environnement sert au démarrage en CI ou
        avant qu'une fiche n'ait été créée.
        """
        creds = getattr(self._core, "credentials", None)
        if creds is not None:
            try:
                from .vault_credentials import AgentContext
                bundle = creds.get_credentials(
                    CREDENTIAL_ID,
                    AgentContext(agent_id="discord_engine", tool="discord.start",
                                 purpose="démarrage du bot"),
                )
                token = bundle.get("api_key") or bundle.get("password") or bundle.get("access_token")
                if token:
                    return token
            except Exception:
                pass          # fiche absente ou non habilitée : on tente l'environnement
        return os.getenv("DISCORD_BOT_TOKEN", "").strip()

    def redact(self, text: Any) -> str:
        """Retire le token de tout texte destiné à un log ou à un message."""
        out = str(text or "")
        if self._token and self._token in out:
            out = out.replace(self._token, "[token masqué]")
        vault = getattr(self._core, "vault", None)
        if vault is not None:
            try:
                out = vault.scrub(out)
            except Exception:
                pass
        return out

    # -- audit ------------------------------------------------------------
    def audit(self, action: str, *, status: str = "ok", detail: Any = "", agent: str = "discord") -> None:
        audit = getattr(self._core, "audit", None)
        if audit is None:
            return
        try:
            audit.record(action=action, status=status, agent=agent, tool="discord",
                         detail=self.redact(detail) if isinstance(detail, str) else detail)
        except Exception:
            pass

    def emit(self, kind: str, payload: dict[str, Any]) -> None:
        events = getattr(self._core, "events", None)
        if events is None:
            return
        try:
            events.emit(kind, payload)
        except Exception:
            pass

    # -- cycle de vie -----------------------------------------------------
    @property
    def available(self) -> bool:
        return HAVE_DISCORD

    @property
    def connected(self) -> bool:
        return bool(self._bot is not None and self._ready.is_set() and not getattr(self._bot, "is_closed", lambda: True)())

    def status(self) -> dict[str, Any]:
        bot = self._bot
        guilds = []
        if self.connected:
            try:
                guilds = [{"id": g.id, "name": g.name, "members": g.member_count} for g in bot.guilds]
            except Exception:
                guilds = []
        return {
            "available": HAVE_DISCORD,
            "connected": self.connected,
            "user": str(getattr(bot, "user", "") or ""),
            "guilds": guilds,
            "uptime_s": int(time.time() - self._started_at) if self._started_at else 0,
            "error": self.redact(self._error),
            "token_source": "coffre" if getattr(self._core, "credentials", None) and self._token
                            and not os.getenv("DISCORD_BOT_TOKEN") else ("environnement" if self._token else "absent"),
        }

    def start(self, *, wait: float = 20.0) -> dict[str, Any]:
        """Démarre le bot dans son thread. Idempotent."""
        if not HAVE_DISCORD:
            return {"ok": False, "error": "discord.py n'est pas installé (pip install -U discord.py)."}
        if self.connected:
            return {"ok": True, "already_running": True, **self.status()}
        self._token = self._resolve_token()
        if not self._token:
            return {"ok": False, "error": ("Aucun token Discord : crée une fiche « discord_bot » dans le "
                                           "coffre, ou définis DISCORD_BOT_TOKEN.")}
        self._error = ""
        self._ready.clear()
        self._settled.clear()
        self._thread = threading.Thread(target=self._run_forever, daemon=True, name="jarvis-discord")
        self._thread.start()
        # On attend le VERDICT, pas le succès : un token invalide doit répondre
        # tout de suite, pas au bout du délai maximum.
        settled = self._settled.wait(timeout=wait)
        ready = self._ready.is_set()
        if ready:
            action, status = "discord.bot.started", "ok"
        elif settled:
            action, status = "discord.bot.start_failed", "error"
        else:
            action, status = "discord.bot.start_timeout", "warn"
        self.audit(action, status=status, detail={"attente_s": wait, "erreur": self._error})
        result = {"ok": bool(ready), **self.status()}
        if not ready and not result.get("error"):
            result["error"] = f"Discord n'a pas répondu en {wait:g} s."
        return result

    def _run_forever(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            self._bot = self._build_bot()
            loop.run_until_complete(self._bot.start(self._token))
        except Exception as exc:
            # Le message d'erreur de discord.py peut contenir le token : on le
            # fait passer par `redact()` avant toute trace.
            self._error = self.redact(exc)
            self.audit("discord.bot.crashed", status="error", detail=self._error)
        finally:
            self._ready.clear()
            self._settled.set()          # verdict rendu, quel qu'il soit
            try:
                # `bot.start()` qui échoue à l'authentification laisse la session
                # HTTP ouverte (« Unclosed connector ») : chaque tentative
                # fuiterait un connecteur aiohttp. On ferme explicitement.
                if self._bot is not None and not self._bot.is_closed():
                    loop.run_until_complete(self._bot.close())
            except Exception:
                pass
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            self._loop = None

    def stop(self, *, timeout: float = 10.0) -> dict[str, Any]:
        if not self.connected or self._loop is None:
            return {"ok": True, "stopped": False}
        try:
            asyncio.run_coroutine_threadsafe(self._bot.close(), self._loop).result(timeout=timeout)
        except Exception as exc:
            return {"ok": False, "error": self.redact(exc)}
        self._ready.clear()
        self.audit("discord.bot.stopped")
        return {"ok": True, "stopped": True}

    # -- passerelle synchrone --------------------------------------------
    def submit(self, coro, *, timeout: float = DEFAULT_TIMEOUT):
        """Exécute une coroutine dans la boucle du bot depuis un thread tiers.

        C'est LA frontière entre le monde synchrone du SecureToolRunner et le
        monde asynchrone de discord.py. Le délai maximum est impératif : sans
        lui, un outil appelé par un agent resterait bloqué si Discord cesse de
        répondre, et bloquerait la tâche entière.
        """
        if not HAVE_DISCORD:
            raise RuntimeError("discord.py n'est pas installé.")
        if self._loop is None or not self.connected:
            raise RuntimeError("Le bot Discord n'est pas connecté. Démarre-le d'abord.")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except asyncio.TimeoutError:
            future.cancel()
            raise TimeoutError(f"Discord n'a pas répondu en {timeout:g} s.") from None

    async def llm(self, prompt: str, *, system: str = "", role: str = "default",
                  max_tokens: int = 700) -> str:
        """Appel LLM sans bloquer la boucle du bot.

        `core.llm.chat()` est synchrone : l'appeler directement ici gèlerait le
        heartbeat Discord, que le serveur interprète comme une déconnexion.
        """
        messages = ([{"role": "system", "content": system}] if system else []) + \
                   [{"role": "user", "content": prompt}]
        loop = asyncio.get_running_loop()

        def call():
            return self._core.llm.chat(messages, role=role, max_tokens=max_tokens)

        try:
            response = await loop.run_in_executor(None, call)
        except Exception as exc:
            return f"[modèle indisponible : {self.redact(exc)}]"
        text = getattr(response, "text", "") or getattr(response, "content", "")
        error = getattr(response, "error", "")
        return text.strip() if text else f"[modèle indisponible : {self.redact(error)}]"

    # -- appels API résistants aux limites de débit ----------------------
    async def api(self, factory: Callable[[], Any], *, attempts: int = 4, what: str = "appel Discord"):
        """Exécute un appel API en respectant les 429 (Too Many Requests).

        discord.py gère déjà ses propres limites, mais pas les limites globales
        ni les 429 renvoyés lors d'actions en rafale (purge, lockdown). On relit
        `retry_after` fourni par Discord plutôt que d'attendre une durée
        arbitraire, et on n'insiste jamais sur une erreur non liée au débit :
        réessayer un 403 ne fera que multiplier les refus.
        """
        delay = 1.0
        for attempt in range(1, attempts + 1):
            try:
                return await factory()
            except Exception as exc:
                is_http = HAVE_DISCORD and isinstance(exc, discord.errors.HTTPException)
                status = getattr(exc, "status", 0)
                if not is_http or status not in (429, 500, 502, 503, 504) or attempt >= attempts:
                    raise
                retry_after = float(getattr(exc, "retry_after", 0) or 0) or delay
                self.emit("discord.rate_limited",
                          {"what": what, "retry_after": retry_after, "attempt": attempt})
                await asyncio.sleep(min(retry_after, 60.0))
                delay = min(delay * 2, 30.0)
        return None

    # -- construction du bot ---------------------------------------------
    def _build_bot(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True            # privilégié : à activer dans le portail développeur
        intents.messages = True
        intents.message_content = True    # privilégié également
        intents.voice_states = True

        bot = commands.Bot(command_prefix="!jarvis ", intents=intents, help_command=None)
        engine = self

        @bot.event
        async def on_ready():
            engine._started_at = time.time()
            engine._ready.set()
            engine._settled.set()
            engine.emit("discord.ready", {"user": str(bot.user), "guilds": len(bot.guilds)})
            engine.audit("discord.bot.ready", detail={"user": str(bot.user), "guilds": len(bot.guilds)})

        @bot.event
        async def on_message(message):
            if message.author.bot or not message.guild:
                return
            try:
                await engine.scan_message(message)
            except Exception as exc:
                engine.audit("discord.scan.failed", status="error", detail=engine.redact(exc))
            await bot.process_commands(message)

        @bot.event
        async def on_member_join(member):
            engine.raids.record(member.guild.id)
            status = engine.raids.status(member.guild.id)
            if status["raid_suspected"]:
                engine.emit("discord.raid.suspected", status)
                engine.audit("discord.raid.suspected", status="warn", detail=status)
                engine._core.events.feed(
                    f"Vague d'arrivées suspecte sur {member.guild.name}", level="warn",
                    kind="discord", detail=f"{status['recent_joins']} arrivées en {status['window_seconds']} s",
                    source="discord")

        # Journal des événements sensibles : suppression de rôle, modification
        # de salon. On rapporte ce qui s'est passé, sans le réinterpréter.
        @bot.event
        async def on_guild_role_delete(role):
            await engine.report_admin(role.guild, "Rôle supprimé", f"Rôle « {role.name} » supprimé.", "WARNING")

        @bot.event
        async def on_guild_channel_delete(channel):
            await engine.report_admin(channel.guild, "Salon supprimé",
                                      f"Salon « {channel.name} » supprimé.", "WARNING")

        @bot.event
        async def on_guild_channel_update(before, after):
            if before.name != after.name:
                await engine.report_admin(after.guild, "Salon renommé",
                                          f"« {before.name} » → « {after.name} »", "INFO")
        return bot

    # -- outils asynchrones ----------------------------------------------
    def _guild(self, guild_id: int):
        guild = self._bot.get_guild(int(guild_id))
        if guild is None:
            raise ValueError(f"Serveur {guild_id} introuvable (le bot y est-il invité ?).")
        return guild

    def _channel(self, channel_id: int):
        channel = self._bot.get_channel(int(channel_id))
        if channel is None:
            raise ValueError(f"Salon {channel_id} introuvable.")
        return channel

    async def scan_message(self, message) -> dict[str, Any]:
        """Analyse + sanction graduée. Renvoie le verdict appliqué."""
        content = message.content or ""
        author = message.author
        history = self._recent[author.id]
        repeat = sum(1 for previous in history if previous == content) + 1 if content else 0
        history.append(content)

        joined_at = getattr(author, "joined_at", None)
        is_new = bool(joined_at and (time.time() - joined_at.timestamp()) < 3 * 86400)
        verdict = classify_message(content, author_is_new=is_new,
                                   mention_count=len(MENTION_RE.findall(content)),
                                   repeat_count=repeat)

        # Le modèle n'est sollicité que dans la zone grise : rien de flagrant,
        # mais un texte non vide. Inutile de payer un appel pour « bonjour »,
        # et hors de question d'attendre son avis pour bloquer un hameçonnage.
        if verdict.severity in (SEV_NONE, SEV_LOW) and len(content) > 40:
            judged = await self._llm_opinion(content)
            if judged and SEVERITY_ORDER[judged.severity] > SEVERITY_ORDER[verdict.severity]:
                verdict = judged

        if verdict.action == "none":
            return verdict.to_dict()
        await self.apply_sanction(message, verdict)
        return verdict.to_dict()

    async def _llm_opinion(self, content: str) -> Verdict | None:
        answer = await self.llm(
            f"Message à évaluer :\n---\n{content[:1500]}\n---",
            system=("Tu modères un serveur Discord. Réponds UNIQUEMENT par "
                    "`severite|categorie|motif court`, où severite vaut none, low, medium ou high. "
                    "N'invente pas d'infraction : dans le doute, réponds `none|-|-`."),
            max_tokens=80)
        if not answer or answer.startswith("[modèle"):
            return None
        parts = [p.strip() for p in answer.split("|")]
        severity = _fold(parts[0]) if parts else ""
        if severity not in SEVERITY_ORDER or severity == SEV_NONE:
            return None
        actions = {SEV_LOW: "warn", SEV_MEDIUM: "delete", SEV_HIGH: "timeout"}
        return Verdict(severity=severity, action=actions.get(severity, "warn"),
                       reasons=[parts[2] if len(parts) > 2 else "Signalé par le modèle."],
                       categories=[parts[1]] if len(parts) > 1 else [],
                       timeout_seconds=3600 if severity == SEV_HIGH else 0, source="rules+llm")

    async def apply_sanction(self, message, verdict: Verdict) -> None:
        """Applique la sanction et la journalise. Les échecs sont tracés, pas avalés."""
        author, guild = message.author, message.guild
        reason = " ".join(verdict.reasons)[:400] or "Modération automatique."
        performed: list[str] = []

        try:
            if verdict.action in ("delete", "timeout", "kick", "ban"):
                await self.api(lambda: message.delete(), what="suppression de message")
                performed.append("message supprimé")
            if verdict.action == "warn":
                await self.dm(author, "Avertissement",
                              f"Ton message dans #{message.channel} a été signalé.\n**Motif** : {reason}",
                              "WARNING")
                performed.append("avertissement en MP")
            elif verdict.action == "timeout":
                until = discord.utils.utcnow() + __import__("datetime").timedelta(
                    seconds=verdict.timeout_seconds or 3600)
                await self.api(lambda: author.timeout(until, reason=reason), what="timeout")
                performed.append(f"exclusion temporaire {verdict.timeout_seconds or 3600} s")
            elif verdict.action == "kick":
                await self.api(lambda: guild.kick(author, reason=reason), what="kick")
                performed.append("expulsion")
            elif verdict.action == "ban":
                await self.api(lambda: guild.ban(author, reason=reason, delete_message_days=1),
                               what="ban")
                performed.append("bannissement")
        except Exception as exc:
            # Cause la plus fréquente : le rôle du bot est sous celui de la cible.
            self.audit("discord.sanction.failed", status="error",
                       detail={"action": verdict.action, "erreur": self.redact(exc)})
            self.emit("discord.sanction.failed", {"action": verdict.action, "error": self.redact(exc)})
            return

        detail = {"action": verdict.action, "severite": verdict.severity,
                  "auteur": f"{author} ({author.id})", "salon": str(message.channel),
                  "motifs": verdict.reasons, "categories": verdict.categories,
                  "effectue": performed, "source": verdict.source}
        self.audit("discord.moderation", status="warn", detail=detail)
        self.emit("discord.moderation", detail)
        await self.report_admin(guild, f"Modération · {verdict.severity}",
                                f"**{author}** — {reason}\nActions : {', '.join(performed) or 'aucune'}",
                                "WARNING" if verdict.severity != SEV_CRITICAL else "CRITICAL")

    async def dm(self, user, title: str, message: str, severity: str = "INFO") -> bool:
        """Message privé. Un MP fermé n'est pas une erreur : on le signale sans échouer."""
        try:
            await self.api(lambda: user.send(embed=self.embed(title, message, severity)), what="MP")
            return True
        except Exception as exc:
            self.emit("discord.dm.failed", {"user": str(user), "error": self.redact(exc)})
            return False

    def embed(self, title: str, description: str, severity: str = "INFO", *,
              fields: list[tuple[str, str]] | None = None):
        colour = ALERT_COLORS.get(str(severity).upper(), ALERT_COLORS["INFO"])
        embed = discord.Embed(title=str(title)[:256], description=self.redact(description)[:4000],
                              colour=colour, timestamp=discord.utils.utcnow())
        for name, value in (fields or []):
            embed.add_field(name=str(name)[:256], value=self.redact(value)[:1024], inline=False)
        embed.set_footer(text="JARVIS")
        return embed

    async def report_admin(self, guild, title: str, message: str, severity: str = "INFO") -> bool:
        """Compte-rendu dans #logs-admin s'il existe. Silencieux sinon."""
        if guild is None:
            return False
        channel = discord.utils.get(guild.text_channels, name="logs-admin")
        if channel is None:
            return False
        try:
            await self.api(lambda: channel.send(embed=self.embed(title, message, severity)),
                           what="journal admin")
            return True
        except Exception:
            return False

    async def send_alert(self, channel_id: int, title: str, message: str,
                         severity: str = "INFO") -> dict[str, Any]:
        channel = self._channel(channel_id)
        sent = await self.api(lambda: channel.send(embed=self.embed(title, message, severity)),
                              what="alerte")
        self.audit("discord.alert.sent", detail={"salon": str(channel), "titre": title,
                                                 "severite": severity})
        return {"ok": True, "message_id": getattr(sent, "id", None), "channel": str(channel)}

    async def purge(self, channel_id: int, limit: int = 50, filter_type: str = "all") -> dict[str, Any]:
        """Nettoyage ciblé. `limit` est plafonné : une purge massive est irréversible."""
        channel = self._channel(channel_id)
        limit = max(1, min(int(limit), 200))
        checks = {
            "all": lambda m: True,
            "bots": lambda m: m.author.bot,
            "links": lambda m: bool(URL_RE.search(m.content or "")),
            "invites": lambda m: bool(INVITE_RE.search(m.content or "")),
        }
        check = checks.get(str(filter_type), checks["all"])
        deleted = await self.api(lambda: channel.purge(limit=limit, check=check),
                                 what="purge")
        count = len(deleted or [])
        self.audit("discord.purge", status="warn",
                   detail={"salon": str(channel), "supprimes": count, "filtre": filter_type})
        self.emit("discord.purge", {"channel": str(channel), "deleted": count})
        return {"ok": True, "deleted": count, "channel": str(channel), "filter": filter_type}

    async def lockdown(self, guild_id: int, enable: bool = True) -> dict[str, Any]:
        """Verrouille/déverrouille l'envoi de messages sur les salons publics.

        L'état précédent de chaque salon est mémorisé pour que le déverrouillage
        RESTAURE la configuration d'origine au lieu d'autoriser tout le monde
        partout — un salon en lecture seule avant le raid doit le rester après.
        """
        guild = self._guild(guild_id)
        everyone = guild.default_role
        changed, failed = [], []
        if enable:
            saved: dict[int, Any] = {}
            for channel in guild.text_channels:
                overwrite = channel.overwrites_for(everyone)
                saved[channel.id] = overwrite.send_messages
                if overwrite.send_messages is False:
                    continue
                overwrite.send_messages = False
                try:
                    await self.api(lambda c=channel, o=overwrite: c.set_permissions(
                        everyone, overwrite=o, reason="Lockdown JARVIS"), what="lockdown")
                    changed.append(channel.name)
                except Exception as exc:
                    failed.append(f"{channel.name}: {self.redact(exc)[:80]}")
            self._lockdowns[guild.id] = saved
        else:
            saved = self._lockdowns.pop(guild.id, {})
            for channel in guild.text_channels:
                if channel.id not in saved:
                    continue
                overwrite = channel.overwrites_for(everyone)
                overwrite.send_messages = saved[channel.id]
                try:
                    await self.api(lambda c=channel, o=overwrite: c.set_permissions(
                        everyone, overwrite=o, reason="Fin du lockdown JARVIS"), what="lockdown")
                    changed.append(channel.name)
                except Exception as exc:
                    failed.append(f"{channel.name}: {self.redact(exc)[:80]}")

        detail = {"serveur": guild.name, "active": enable, "salons": len(changed), "echecs": failed}
        self.audit("discord.lockdown", status="warn", detail=detail)
        self.emit("discord.lockdown", detail)
        await self.report_admin(guild, "Lockdown " + ("activé" if enable else "levé"),
                                f"{len(changed)} salon(s) traité(s).",
                                "CRITICAL" if enable else "SUCCESS")
        return {"ok": True, "enabled": enable, "channels": changed, "failed": failed,
                "restored": not enable}

    async def summarize_channel(self, channel_id: int, message_count: int = 100) -> dict[str, Any]:
        channel = self._channel(channel_id)
        count = max(5, min(int(message_count), 300))
        lines = []
        async for message in channel.history(limit=count):
            if message.author.bot or not (message.content or "").strip():
                continue
            lines.append(f"{message.author.display_name}: {message.content[:300]}")
        if not lines:
            return {"ok": False, "error": "Aucun message exploitable dans ce salon."}
        lines.reverse()
        summary = await self.llm(
            "Conversation :\n" + "\n".join(lines)[:12000],
            system=("Résume cette conversation Discord en français : 5 puces maximum, "
                    "puis une ligne « À faire : » si des actions ont été décidées. "
                    "N'invente aucune décision qui n'apparaît pas dans le texte."),
            max_tokens=600)
        self.audit("discord.summary", detail={"salon": str(channel), "messages": len(lines)})
        return {"ok": True, "channel": str(channel), "messages_read": len(lines), "summary": summary}

    async def publish_announcement(self, channel_id: int, raw_prompt: str,
                                   ping_role_id: int | None = None) -> dict[str, Any]:
        channel = self._channel(channel_id)
        drafted = await self.llm(
            f"Idée brute : {raw_prompt}",
            system=("Rédige une annonce Discord professionnelle en français. Première ligne = titre "
                    "court sans emoji ni markdown, lignes suivantes = corps (3 phrases maximum). "
                    "Ne promets rien qui ne soit pas dans l'idée brute."),
            max_tokens=400)
        title, _, body = drafted.partition("\n")
        content = f"<@&{int(ping_role_id)}>" if ping_role_id else None
        sent = await self.api(
            lambda: channel.send(content=content,
                                 embed=self.embed(title.strip() or "Annonce", body.strip() or drafted,
                                                  "INFO"),
                                 allowed_mentions=discord.AllowedMentions(roles=True, everyone=False)),
            what="annonce")
        self.audit("discord.announcement", detail={"salon": str(channel), "titre": title.strip()})
        return {"ok": True, "message_id": getattr(sent, "id", None),
                "title": title.strip(), "body": body.strip()}

    async def welcome(self, member_id: int, guild_id: int, role_names: list[str] | None = None) -> dict[str, Any]:
        guild = self._guild(guild_id)
        member = guild.get_member(int(member_id))
        if member is None:
            raise ValueError(f"Membre {member_id} introuvable sur {guild.name}.")
        text = await self.llm(
            f"Nouveau membre : {member.display_name}. Serveur : {guild.name}.",
            system=("Écris un message de bienvenue chaleureux en français, 2 phrases maximum, "
                    "sans inventer de règles ni de salons dont tu ignores l'existence."),
            max_tokens=150)
        granted, failed = [], []
        for name in (role_names or []):
            role = discord.utils.get(guild.roles, name=name)
            if role is None:
                failed.append(f"{name} (rôle inexistant)")
                continue
            try:
                await self.api(lambda r=role: member.add_roles(r, reason="Onboarding JARVIS"),
                               what="attribution de rôle")
                granted.append(name)
            except Exception as exc:
                failed.append(f"{name}: {self.redact(exc)[:60]}")
        delivered = await self.dm(member, f"Bienvenue sur {guild.name}", text, "SUCCESS")
        if not delivered:
            channel = discord.utils.get(guild.text_channels, name="bienvenue")
            if channel is not None:
                await self.api(lambda: channel.send(content=member.mention,
                                                    embed=self.embed("Bienvenue", text, "SUCCESS")),
                               what="bienvenue")
        self.audit("discord.onboarding", detail={"membre": str(member), "roles": granted,
                                                 "echecs": failed, "mp": delivered})
        return {"ok": True, "member": str(member), "dm_sent": delivered,
                "roles_granted": granted, "roles_failed": failed, "message": text}

    # -- support niveau 1 -------------------------------------------------
    async def handle_ticket(self, channel_id: int, user_message: str) -> dict[str, Any]:
        """Première réponse appuyée sur la base de connaissances RÉELLE.

        Si la recherche ne ramène rien, on ne laisse pas le modèle improviser
        une procédure : on le dit et on propose l'escalade. Une réponse
        inventée sur un ticket de support coûte plus cher que pas de réponse.
        """
        channel = self._channel(channel_id)
        loop = asyncio.get_running_loop()

        def search() -> list[dict[str, Any]]:
            for attempt in (
                lambda: self._core.memory.search_knowledge(user_message, limit=4),
                lambda: self._core.brain.search(user_message, limit=4),
            ):
                try:
                    found = attempt()
                    if found:
                        return list(found)
                except Exception:
                    continue
            return []

        hits = await loop.run_in_executor(None, search)
        if not hits:
            answer = ("Je n'ai pas trouvé de réponse documentée à cette question. "
                      "Un membre du support va prendre le relais.")
            resolved = False
        else:
            extracts = "\n\n".join(
                f"[{h.get('title') or h.get('id')}] {str(h.get('content') or '')[:800]}" for h in hits)
            answer = await self.llm(
                f"Question du client :\n{user_message}\n\nExtraits de la base :\n{extracts}",
                system=("Réponds en français, uniquement à partir des extraits fournis. "
                        "Si les extraits ne suffisent pas, dis-le explicitement et propose "
                        "de transmettre au support. N'invente aucune procédure."),
                max_tokens=500)
            resolved = "ne suffis" not in _fold(answer) and "transmett" not in _fold(answer)

        await self.api(lambda: channel.send(embed=self.embed(
            "Première réponse", answer, "INFO" if resolved else "WARNING")), what="ticket")
        self.audit("discord.ticket.answered",
                   detail={"salon": str(channel), "sources": len(hits), "auto_resolu": resolved})
        return {"ok": True, "answered": True, "auto_resolved": resolved,
                "sources": len(hits), "answer": answer}

    async def escalate_ticket(self, channel_id: int, reason: str,
                              support_role: str = "Support") -> dict[str, Any]:
        """Donne l'accès au rôle support et crée une fiche CRM résumée."""
        channel = self._channel(channel_id)
        guild = channel.guild
        role = discord.utils.get(guild.roles, name=support_role)
        if role is not None:
            overwrite = channel.overwrites_for(role)
            overwrite.view_channel = True
            overwrite.send_messages = True
            await self.api(lambda: channel.set_permissions(role, overwrite=overwrite,
                                                           reason="Escalade JARVIS"),
                           what="escalade")

        transcript = []
        async for message in channel.history(limit=50):
            if (message.content or "").strip():
                transcript.append(f"{message.author.display_name}: {message.content[:200]}")
        transcript.reverse()
        summary = await self.llm(
            "Échanges :\n" + "\n".join(transcript)[:6000],
            system=("Résume ce ticket de support en 3 lignes : problème, ce qui a été tenté, "
                    "ce qui reste à faire."),
            max_tokens=300) if transcript else "Ticket sans échange."

        pipeline = getattr(self._core, "crm_pipeline", None)
        interaction_id = ""
        if pipeline is not None:
            loop = asyncio.get_running_loop()

            def log_it():
                return pipeline.log_interaction({
                    "kind": "task", "direction": "in", "subject": f"Ticket Discord #{channel.name}",
                    "summary": f"{summary}\n\nEscalade : {reason}", "agent": "discord",
                    "tool": "discord.escalate_ticket", "source_ref": f"discord:channel:{channel.id}",
                })
            try:
                result = await loop.run_in_executor(None, log_it)
                interaction_id = (result.get("interaction") or {}).get("id", "")
            except Exception as exc:
                self.audit("discord.ticket.crm_failed", status="error", detail=self.redact(exc))

        await self.api(lambda: channel.send(
            content=(role.mention if role else None),
            embed=self.embed("Ticket escaladé", f"**Motif** : {reason}\n\n{summary}", "WARNING"),
            allowed_mentions=discord.AllowedMentions(roles=True, everyone=False)), what="escalade")
        self.audit("discord.ticket.escalated", status="warn",
                   detail={"salon": str(channel), "motif": reason, "role": support_role,
                           "crm_interaction": interaction_id})
        return {"ok": True, "channel": str(channel), "role_notified": bool(role),
                "summary": summary, "crm_interaction_id": interaction_id}

    # -- passerelle CRM ---------------------------------------------------
    async def crm_quick_lead(self, user_id: int, guild_id: int, notes: str = "") -> dict[str, Any]:
        """Enregistre un membre Discord comme prospect dans le CRM."""
        guild = self._guild(guild_id)
        member = guild.get_member(int(user_id))
        if member is None:
            member = await self.api(lambda: guild.fetch_member(int(user_id)), what="fetch_member")
        if member is None:
            raise ValueError(f"Membre {user_id} introuvable sur {guild.name}.")

        roles = [r.name for r in getattr(member, "roles", []) if r.name != "@everyone"]
        loop = asyncio.get_running_loop()

        def save():
            contact = self._core.crm.upsert({
                "name": member.display_name,
                # Discord ne fournit pas d'e-mail : on n'en fabrique pas. Le champ
                # reste vide et l'identité Discord est conservée dans les notes.
                "notes": (f"Discord : {member} (id {member.id})\n"
                          f"Serveur : {guild.name}\nRôles : {', '.join(roles) or 'aucun'}\n"
                          f"{notes}").strip(),
            })
            pipeline = getattr(self._core, "crm_pipeline", None)
            scoring = None
            if pipeline is not None:
                scoring = pipeline.log_interaction({
                    "contact_id": contact["id"], "kind": "note", "direction": "in",
                    "subject": f"Prospect Discord — {member.display_name}",
                    "summary": notes or f"Arrivé via Discord ({guild.name}).",
                    "agent": "discord", "tool": "discord.crm_quick_lead",
                    "source_ref": f"discord:user:{member.id}",
                })
            return contact, scoring

        contact, scoring = await loop.run_in_executor(None, save)
        self.audit("discord.crm.lead", detail={"membre": str(member), "contact_id": contact["id"]})
        self.emit("discord.crm.lead", {"contact_id": contact["id"], "member": str(member)})
        return {"ok": True, "contact_id": contact["id"], "name": contact["name"],
                "avatar": str(getattr(member, "display_avatar", "") or ""), "roles": roles,
                "scoring": (scoring or {}).get("scoring")}

    # -- pilotage à distance ---------------------------------------------
    async def remote_command(self, admin_id: int, command: str) -> dict[str, Any]:
        """Pilotage de JARVIS depuis Discord, réservé aux administrateurs déclarés.

        La liste blanche vit dans les réglages (`integrations.discord_admins`),
        PAS dans le code et pas dans le message : se fier au rôle Discord seul
        reviendrait à confier le pilotage de JARVIS à quiconque parvient à
        obtenir ce rôle sur le serveur.
        """
        admins = self._core.settings.get("integrations", "discord_admins", []) or []
        if str(admin_id) not in [str(a) for a in admins]:
            self.audit("discord.remote.denied", status="denied",
                       detail={"admin_id": admin_id, "commande": str(command)[:120]})
            return {"ok": False, "error": ("Utilisateur non autorisé. Ajoute son identifiant Discord "
                                           "dans Réglages → integrations.discord_admins.")}

        verb, _, argument = str(command or "").strip().partition(" ")
        verb = verb.lower().lstrip("/")
        loop = asyncio.get_running_loop()

        if verb == "status":
            snapshot = await loop.run_in_executor(None, self._core.status)
            core_state = snapshot.get("core", {})
            data = {"version": snapshot.get("version"), "uptime_s": snapshot.get("uptime_s"),
                    "systeme": (core_state.get("system") or {}).get("detail", ""),
                    "agents": (core_state.get("agents") or {}).get("detail", "")}
        elif verb in ("run-task", "run_task", "task"):
            if not argument.strip():
                return {"ok": False, "error": "Précise la tâche à lancer."}
            task = await loop.run_in_executor(
                None, lambda: self._core.tasks.create(name=argument.strip()[:200], kind="chat",
                                                      agent="discord"))
            data = {"task_id": task.get("id") if isinstance(task, dict) else str(task)}
        elif verb == "restart":
            # Volontairement NON implémenté : un redémarrage déclenché depuis un
            # salon coupe l'interface, la voix et les tâches en cours, sans que
            # personne devant la machine ne l'ait demandé. On le refuse
            # explicitement plutôt que de l'exposer à moitié.
            self.audit("discord.remote.refused", status="warn",
                       detail={"admin_id": admin_id, "commande": "restart"})
            return {"ok": False, "error": ("Le redémarrage n'est pas pilotable depuis Discord : "
                                           "il coupe l'interface et les tâches en cours. "
                                           "Lance-le depuis la machine.")}
        else:
            return {"ok": False, "error": f"Commande inconnue : {verb}. Disponibles : status, run-task."}

        self.audit("discord.remote.executed", detail={"admin_id": admin_id, "commande": verb,
                                                      "argument": argument[:120]})
        return {"ok": True, "command": verb, "data": data}
