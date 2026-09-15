"""Rapport matinal par appel vocal Discord — réveil, dialogue et compte rendu.

Ce que fait le module
---------------------
Chaque matin à l'heure configurée (06h00 par défaut) :

  1. une **collecte** agrège l'état réel de la nuit (mails, tâches, projets,
     notifications, santé machine) — aucune donnée n'est inventée : une source
     indisponible est annoncée comme telle ;
  2. le bot **pingue** le salon texte privé (« JARVIS demande un appel vocal »),
     ce qui fait vibrer le téléphone ;
  3. il **rejoint** le salon vocal dédié et attend ;
  4. dès que l'utilisateur arrive, JARVIS **prend la parole** (edge-tts,
     `fr-FR-HenriNeural`) et pose la question d'accroche ;
  5. il **écoute**, transcrit localement (`faster-whisper`) et agit : rapport
     immédiat, ou report de cinq minutes.

Choix d'architecture
--------------------
*Pas d'APScheduler.* Le dépôt a déjà deux boucles de planification
(`AutomationManager`, `DiscordScheduler`) qui savent lire un déclencheur
`daily` ; en ajouter une troisième, issue d'une bibliothèque tierce, aurait
fait une dépendance de plus pour un calcul que `core.automations.next_run()`
rend déjà. On reprend donc le motif maison : un thread démon, un tick, une
échéance.

*Rien ne bloque la boucle du bot.* La collecte (IMAP, SQLite), la synthèse
vocale et la transcription Whisper sont synchrones et lentes : elles passent
toutes par `run_in_executor`. Geler la boucle gèlerait le heartbeat Discord,
que le serveur interprète comme une déconnexion.

Dépendances optionnelles, et ce qui se passe sans elles
------------------------------------------------------
  * **PyNaCl + FFmpeg** (`pip install -U "discord.py[voice]"`) : sans eux, pas
    de voix du tout. Le rapport est alors **publié en texte** dans le salon —
    l'information arrive, seule la forme change.
  * **discord-ext-voice-recv** : discord.py ne sait pas *recevoir* de l'audio.
    Sans cette extension, JARVIS parle mais n'entend pas ; il bascule alors sur
    une réponse écrite dans le salon (« maintenant » / « dans 5 minutes »).
  * **faster-whisper** : idem, transcription impossible → repli écrit.

Aucun de ces replis n'est silencieux : chacun est dit à l'oral ou à l'écrit, et
consigné dans l'audit.
"""
from __future__ import annotations

import asyncio
import io
import re
import tempfile
import threading
import time
import wave
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .speech_sanitizer import sanitize_for_speech

# Section de réglages (cf. DEFAULT_SETTINGS dans config.py).
SETTINGS_SECTION = "discord_call"

DEFAULT_VOICE = "fr-FR-HenriNeural"
DEFAULT_TICK_S = 20.0

# Audio Discord : 48 kHz, 16 bits, stéréo. Ce n'est pas un réglage mais le
# format du flux Opus décodé — le changer désynchroniserait la transcription.
SAMPLE_RATE = 48000
CHANNELS = 2
SAMPLE_WIDTH = 2

# Fin d'énoncé : Discord n'émet pas de paquet pendant un silence. Passé ce
# délai sans audio, la phrase est considérée terminée. 1,2 s laisse respirer
# entre deux mots sans faire attendre inutilement.
SILENCE_S = 1.2
MAX_UTTERANCE_S = 20.0

# Reports consécutifs autorisés avant que JARVIS n'impose le rapport.
#
# Sans plafond, chaque « plus tard » reprogramme un appel cinq minutes plus
# tard, indéfiniment. Le cas qui inquiète n'est pas l'utilisateur qui traîne —
# c'est la transcription : un bruit de fond entendu comme « attends » relance
# l'appel tout seul, et le même bruit peut le relancer encore. Trois reports
# suffisent à couvrir un vrai réveil difficile ; au-delà, l'automate se tait.
MAX_SNOOZES = 3

# Marge accordée au dialogue, en plus du temps d'attente dans le salon vocal.
# Au-delà, l'appel est considéré comme figé et annulé. Quinze minutes couvrent
# la lecture du rapport et les questions de suivi les plus bavardes.
CALL_BUDGET_MARGIN_S = 900.0


# ---------------------------------------------------------------------------
# 1. Interprétation de la réponse — fonctions pures, testables sans Discord
# ---------------------------------------------------------------------------
NOW_PATTERNS = (r"\bmaintenant\b", r"\btout de suite\b", r"\bvas?[- ]y\b", r"\bgo\b",
                r"\boui\b", r"\bje t.?ecoute\b", r"\bbalance\b", r"\bd.?accord\b",
                r"\bimmediatement\b", r"\bpret\b")
LATER_PATTERNS = (r"\bdans (cinq|5|dix|10) minutes?\b", r"\bplus tard\b", r"\btout a l.?heure\b",
                  r"\brappelle\b", r"\battends?\b", r"\bpas maintenant\b", r"\bsnooze\b",
                  r"\bencore (cinq|5|dix|10)\b", r"\bdans un moment\b")
CANCEL_PATTERNS = (r"\bannule\b", r"\blaisse tomber\b", r"\bpas aujourd.?hui\b",
                   r"\bpas de rapport\b", r"\bnon merci\b")
END_PATTERNS = (r"\bc.?est tout\b", r"\bmerci\b", r"\bau revoir\b", r"\btermine\b",
                r"\bstop\b", r"\barrete\b", r"\bbonne journee\b")


def plural(count: int, singular: str, plural_form: str = "") -> str:
    """« 1 tâche », « 33 tâches ». Jamais « tâche(s) ».

    Le rapport est d'abord LU : `edge-tts` prononce les parenthèses, et
    « trente-trois tâche parenthèse s parenthèse » à six heures du matin est
    exactement ce qu'il faut éviter.
    """
    word = singular if abs(int(count)) <= 1 else (plural_form or singular + "s")
    return f"{int(count)} {word}"


def shorten(text: str, limit: int = 110) -> str:
    """Coupe sur un mot. Une avancée de projet peut contenir la demande entière
    de l'utilisateur, URL comprise : illisible à l'écrit, interminable à l'oral."""
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    cut = clean[:limit].rsplit(" ", 1)[0]
    return (cut or clean[:limit]).rstrip(" ,;:.") + "…"


def _fold(text: str) -> str:
    """Minuscules sans accents : « arrête » et « arrete » se rejoignent."""
    import unicodedata

    s = str(text or "").lower()
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def interpret_answer(text: str) -> str:
    """Traduit une réponse parlée en intention : now | later | cancel | unclear.

    L'ordre compte. « pas maintenant » contient « maintenant » : reports et
    annulations sont donc testés AVANT l'acceptation, sinon JARVIS déroulerait
    son rapport à quelqu'un qui vient de demander le silence.
    """
    folded = _fold(text)
    if not folded.strip():
        return "unclear"
    if any(re.search(p, folded) for p in CANCEL_PATTERNS):
        return "cancel"
    if any(re.search(p, folded) for p in LATER_PATTERNS):
        return "later"
    if any(re.search(p, folded) for p in NOW_PATTERNS):
        return "now"
    return "unclear"


def wants_to_end(text: str) -> bool:
    """True si l'utilisateur clôt la conversation de suivi."""
    folded = _fold(text)
    return bool(folded.strip()) and any(re.search(p, folded) for p in END_PATTERNS)


# ---------------------------------------------------------------------------
# 2. Collecte du rapport
# ---------------------------------------------------------------------------
@dataclass
class ReportSection:
    """Un point du rapport. `lines` est déjà prêt à être lu à voix haute."""
    key: str
    title: str
    lines: list[str] = field(default_factory=list)

    def speech(self) -> str:
        return f"{self.title}. " + " ".join(self.lines)

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "title": self.title, "lines": list(self.lines)}


@dataclass
class MorningReport:
    generated_at: float = 0.0
    sections: list[ReportSection] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"generated_at": self.generated_at,
                "sections": [s.to_dict() for s in self.sections]}

    def as_text(self) -> str:
        """Rendu Markdown, pour le salon texte (repli sans voix)."""
        return "\n\n".join(f"**{s.title}**\n" + "\n".join(f"• {line}" for line in s.lines)
                           for s in self.sections)

    def as_speech(self) -> str:
        return " ".join(s.speech() for s in self.sections)


class MorningReportCollector:
    """Agrège l'état réel du système. Une source en panne est dite, pas masquée."""

    def __init__(self, core) -> None:
        self._core = core

    def collect(self) -> MorningReport:
        report = MorningReport(generated_at=time.time())
        for build in (self._mail, self._tasks, self._projects, self._notifications, self._system):
            try:
                section = build()
            except Exception as exc:              # une source cassée n'annule pas le rapport
                section = ReportSection(key="erreur", title="Source indisponible",
                                        lines=[f"Une source n'a pas répondu : {str(exc)[:160]}."])
            if section and section.lines:
                report.sections.append(section)
        return report

    # -- sources ----------------------------------------------------------
    def _mail(self) -> ReportSection:
        from .mail import CATEGORIES, CATEGORY_LABELS, MailProcessor

        # `use_mock=False` est impératif : un rapport matinal qui lit un fichier
        # de démonstration annoncerait des mails qui n'existent pas.
        result = MailProcessor(self._core).process(limit=30, unread_only=True, use_mock=False)
        if not result.get("ok"):
            reason = str(result.get("error") or "aucun connecteur configuré")
            # L'erreur du provider est déjà une phrase complète : la préfixer
            # d'un second « : » donne « non consultée : Aucune boîte... : ... ».
            return ReportSection(key="mail", title="Messagerie",
                                 lines=[f"Boîte mail non consultée. {shorten(reason, 150)}"])
        total = int(result.get("total") or 0)
        if not total:
            return ReportSection(key="mail", title="Messagerie",
                                 lines=["Aucun message non lu cette nuit."])
        counts = result.get("counts") or {}
        detail = ", ".join(f"{counts[c]} {CATEGORY_LABELS[c].lower()}"
                           for c in CATEGORIES if counts.get(c))
        lines = [f"{plural(total, 'message non lu', 'messages non lus')}."]
        if detail:
            lines.append(f"Répartition : {detail}.")
        return ReportSection(key="mail", title="Messagerie", lines=lines)

    def _tasks(self) -> ReportSection:
        stats = self._core.tasks.stats()
        active = int(stats.get("active") or 0)
        failed = int(stats.get("failed") or 0)
        waiting = int(stats.get("waiting_confirmation") or 0)
        if not (active or failed or waiting):
            return ReportSection(key="tasks", title="Tâches",
                                 lines=["Aucune tâche en cours ni en échec."])
        lines = []
        if active:
            lines.append(f"{plural(active, 'tâche')} encore en cours.")
        if waiting:
            lines.append(f"{plural(waiting, 'tâche')} "
                         + ("attend" if waiting <= 1 else "attendent") + " ta confirmation.")
        if failed:
            lines.append(f"{plural(failed, 'tâche')} en échec à revoir.")
        return ReportSection(key="tasks", title="Tâches", lines=lines)

    def _projects(self) -> ReportSection:
        data = self._core.project_status.collect(limit_projects=5)
        projects = data.get("projects") or []
        counts = data.get("counts") or {}
        lines: list[str] = []
        if not projects:
            lines.append("Aucun projet actif enregistré.")
        else:
            detailed = projects[:3]
            for item in detailed:
                progress = shorten(item.get("last_progress") or "") or "aucune avancée enregistrée"
                lines.append(f"{item.get('project')} : {item.get('state')}, {progress}.")
            # Seulement ceux qu'on n'a pas déjà détaillés : répéter « bloqué »
            # pour un projet dont on vient de lire l'état alourdit sans informer.
            named = {str(p.get("project")) for p in detailed}
            blocked = [str(p.get("project")) for p in projects
                       if p.get("state") == "bloqué" and str(p.get("project")) not in named]
            if blocked:
                lines.append("Également bloqués : " + ", ".join(blocked[:4]) + ".")
        if counts.get("open_errors"):
            lines.append(f"{plural(counts['open_errors'], 'erreur encore ouverte', 'erreurs encore ouvertes')}.")
        return ReportSection(key="projects", title="Projets et travaux", lines=lines)

    def _notifications(self) -> ReportSection:
        items = self._core.events.feed_items(limit=20, unread_only=True)
        if not items:
            return ReportSection(key="feed", title="Notifications",
                                 lines=["Aucune notification en attente."])
        lines = [f"{plural(len(items), 'notification non lue', 'notifications non lues')}."]
        for item in [i for i in items if str(i.get("level")) in ("warn", "error")][:3]:
            lines.append("À noter : "
                         + shorten(item.get("title") or item.get("message") or "", 140) + ".")
        return ReportSection(key="feed", title="Notifications", lines=lines)

    def _system(self) -> ReportSection:
        snap = self._core.monitor.snapshot(max_age=30.0)
        cpu = (snap.get("cpu") or {}).get("percent")
        memory = (snap.get("memory") or {}).get("percent")
        disk = (snap.get("disk") or {}).get("percent")
        lines: list[str] = []
        if isinstance(cpu, (int, float)) and isinstance(memory, (int, float)):
            lines.append(f"Machine : processeur à {cpu:.0f} pour cent, "
                         f"mémoire à {memory:.0f} pour cent.")
        if isinstance(disk, (int, float)) and disk >= 85:
            lines.append(f"Disque presque plein : {disk:.0f} pour cent occupés.")
        if not lines:
            lines.append("Vérification système indisponible sur cette machine.")
        return ReportSection(key="system", title="Vérifications système", lines=lines)


# ---------------------------------------------------------------------------
# 3. Capture audio Discord → WAV
# ---------------------------------------------------------------------------
def pcm_to_wav(pcm: bytes) -> bytes:
    """Emballe du PCM brut Discord (48 kHz, 16 bits, stéréo) en fichier WAV.

    `SpeechRecognizer.transcribe()` attend les octets d'un FICHIER, pas un flux
    nu : sans en-tête, Whisper ignore la fréquence et le nombre de voies, et
    transcrit du charabia.
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(SAMPLE_WIDTH)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm)
    return buffer.getvalue()


def voice_recv_module():
    """Extension de réception audio, ou None si elle n'est pas installée."""
    try:
        from discord.ext import voice_recv
        return voice_recv
    except Exception:
        return None


def make_user_sink(target_user_id: int):
    """Collecteur d'audio pour un seul locuteur, ou None sans l'extension.

    La classe est construite à l'appel, et non au chargement du module : elle
    hérite de `voice_recv.AudioSink`, qui n'existe pas si l'extension est
    absente — et ce module doit s'importer sans elle.
    """
    voice_recv = voice_recv_module()
    if voice_recv is None:
        return None

    class _UserSink(voice_recv.AudioSink):
        """Accumule le PCM du propriétaire et date le dernier paquet reçu."""

        def __init__(self) -> None:
            super().__init__()
            self.buffer = bytearray()
            self.last_at = 0.0
            self._lock = threading.Lock()

        def wants_opus(self) -> bool:
            return False                      # on veut du PCM décodé, pas de l'Opus

        def write(self, user, data) -> None:  # appelé depuis le thread audio
            uid = getattr(user, "id", None)
            if target_user_id and uid and int(uid) != int(target_user_id):
                return                        # quelqu'un d'autre dans le salon : ignoré
            chunk = getattr(data, "pcm", b"") or b""
            if not chunk:
                return
            with self._lock:
                self.buffer.extend(chunk)
                self.last_at = time.monotonic()

        def take(self) -> bytes:
            """Vide le tampon et le rend. Appelé depuis la boucle du bot."""
            with self._lock:
                data = bytes(self.buffer)
                self.buffer.clear()
                self.last_at = 0.0
            return data

        def idle_for(self) -> float:
            with self._lock:
                return (time.monotonic() - self.last_at) if self.last_at else 0.0

        def has_audio(self) -> bool:
            with self._lock:
                return bool(self.buffer)

        def cleanup(self) -> None:
            pass

    return _UserSink()


# ---------------------------------------------------------------------------
# 4. La session d'appel
# ---------------------------------------------------------------------------
GREETING = ("Il est {heure}.{bonjour} Souhaites-tu ton rapport maintenant, "
            "ou dans cinq minutes ?")


class CallOutcome:
    DELIVERED = "delivered"       # rapport lu à voix haute
    POSTED = "posted"             # rapport publié en texte (voix indisponible)
    SNOOZED = "snoozed"           # reporté à +N minutes
    CANCELLED = "cancelled"       # refusé pour aujourd'hui
    NO_SHOW = "no_show"           # personne n'a rejoint le salon vocal
    FAILED = "failed"


class MorningCallSession:
    """Un appel : ping, salon vocal, dialogue, rapport. Entièrement asynchrone.

    Une session ne lève pas : elle renvoie un dictionnaire de résultat. Un
    échec d'appel matinal ne doit ni tuer la boucle de planification, ni rester
    invisible — d'où le statut explicite et l'audit systématique.
    """

    def __init__(self, manager: "MorningCallManager", report: MorningReport,
                 *, reason: str = "planifié") -> None:
        self._manager = manager
        self._core = manager.core
        self._engine = manager.engine
        self._report = report
        self._reason = reason
        self._voice_client = None
        self._sink = None
        self._joined = asyncio.Event()

    # -- réglages ---------------------------------------------------------
    def _setting(self, key: str, default: Any) -> Any:
        return self._core.settings.get(SETTINGS_SECTION, key, default)

    # -- déroulé ----------------------------------------------------------
    async def run(self) -> dict[str, Any]:
        text_channel = None
        try:
            text_channel = self._engine._channel(self._setting("text_channel", ""))
        except Exception as exc:
            return self._done(CallOutcome.FAILED,
                              f"Salon texte introuvable : {self._engine.redact(exc)}")

        await self._ping(text_channel)

        voice_channel = self._resolve_voice_channel()
        if voice_channel is None:
            await self._post(text_channel, "Salon vocal introuvable — rapport écrit ci-dessous.")
            await self._post_report(text_channel)
            return self._done(CallOutcome.POSTED, "Salon vocal introuvable.")

        try:
            await self._connect(voice_channel)
        except Exception as exc:
            # PyNaCl absent, permissions manquantes, salon plein : on le dit et
            # on livre quand même l'information, par écrit.
            await self._post(text_channel,
                             f"Impossible de rejoindre le salon vocal ({self._engine.redact(exc)}). "
                             "Voici le rapport à l'écrit.")
            await self._post_report(text_channel)
            return self._done(CallOutcome.POSTED, f"Connexion vocale impossible : {exc}"[:300])

        try:
            if not await self._wait_for_user(voice_channel):
                await self._post(text_channel,
                                 "Personne n'a rejoint l'appel. Rapport laissé à l'écrit.")
                await self._post_report(text_channel)
                return self._done(CallOutcome.NO_SHOW, "Aucune arrivée dans le salon vocal.")
            return await self._converse(text_channel)
        finally:
            await self._disconnect()

    # -- étapes -----------------------------------------------------------
    async def _ping(self, channel) -> None:
        owner = str(self._setting("owner_user_id", "") or "").strip()
        mention = f"<@{owner}> " if owner.isdigit() else ""
        name = self._voice_channel_label()
        await self._post(channel, f"{mention}📞 **JARVIS demande un appel vocal.** "
                                  f"Rejoins {name} : ton rapport de la nuit est prêt.")

    def _voice_channel_label(self) -> str:
        ref = str(self._setting("voice_channel", "") or "").strip()
        return f"<#{ref}>" if ref.isdigit() else f"« {ref or 'le salon vocal'} »"

    def _resolve_voice_channel(self):
        """Salon vocal par identifiant ou par nom (« 📞-jarvis-line »).

        `DiscordEngine._channel()` ne résout par nom que les salons TEXTE : un
        salon vocal n'y figure pas, d'où cette recherche dédiée.
        """
        ref = str(self._setting("voice_channel", "") or "").strip().lstrip("#")
        bot = self._engine.bot
        if not ref or bot is None:
            return None
        if ref.isdigit():
            return bot.get_channel(int(ref))
        wanted = self._engine._slug(ref)
        for guild in getattr(bot, "guilds", []) or []:
            for channel in getattr(guild, "voice_channels", []) or []:
                if self._engine._slug(channel.name) == wanted:
                    return channel
        for guild in getattr(bot, "guilds", []) or []:
            for channel in getattr(guild, "voice_channels", []) or []:
                if wanted and wanted in self._engine._slug(channel.name):
                    return channel
        return None

    async def _connect(self, voice_channel) -> None:
        recv = voice_recv_module()
        existing = getattr(voice_channel.guild, "voice_client", None)
        if existing is not None:
            await existing.disconnect(force=True)
        if recv is not None:
            self._voice_client = await voice_channel.connect(cls=recv.VoiceRecvClient)
        else:
            self._voice_client = await voice_channel.connect()

    async def _wait_for_user(self, voice_channel) -> bool:
        """Attend l'arrivée du propriétaire — ou constate qu'il est déjà là."""
        owner = str(self._setting("owner_user_id", "") or "").strip()
        timeout = float(self._setting("wait_user_s", 300) or 300)

        def present() -> bool:
            members = [m for m in getattr(voice_channel, "members", []) or []
                       if not getattr(m, "bot", False)]
            if not owner.isdigit():
                return bool(members)
            return any(int(getattr(m, "id", 0)) == int(owner) for m in members)

        if present():
            return True

        bot = self._engine.bot
        loop = asyncio.get_running_loop()

        async def on_voice_state_update(member, before, after) -> None:
            if getattr(member, "bot", False):
                return
            if owner.isdigit() and int(getattr(member, "id", 0)) != int(owner):
                return
            if getattr(after, "channel", None) and after.channel.id == voice_channel.id:
                loop.call_soon_threadsafe(self._joined.set)

        bot.add_listener(on_voice_state_update, "on_voice_state_update")
        try:
            await asyncio.wait_for(self._joined.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            bot.remove_listener(on_voice_state_update, "on_voice_state_update")

    async def _converse(self, text_channel) -> dict[str, Any]:
        """Accroche, décision, puis rapport et questions de suivi."""
        heure = time.strftime("%Hh%M")
        prenom = str(self._core.settings.get("general", "user_name", "") or "").strip()
        spoken = await self._speak(GREETING.format(
            heure=heure, bonjour=f" Bonjour {prenom}." if prenom else " Bonjour."))
        if not spoken:
            # Sans voix, l'appel n'a plus d'objet : on livre par écrit plutôt
            # que de laisser quelqu'un attendre dans un salon muet.
            await self._post(text_channel,
                             "Je n'ai pas de voix disponible (PyNaCl ou FFmpeg manquant). "
                             "Rapport à l'écrit :")
            await self._post_report(text_channel)
            return self._done(CallOutcome.POSTED, "Synthèse vocale indisponible.")

        decision, heard = await self._ask_decision(text_channel)
        if decision == "later":
            minutes = int(self._setting("snooze_minutes", 5) or 5)
            if self._manager.snooze(minutes):
                await self._speak(f"Entendu. Je te rappelle dans {minutes} minutes.")
                await self._post(text_channel, f"⏰ Rapport reporté de {minutes} minutes.")
                return self._done(CallOutcome.SNOOZED, f"Reporté de {minutes} min (« {heard} »).")
            # Plafond atteint : on ne reprogramme plus rien. Le rapport est lu
            # maintenant, puis laissé à l'écrit — l'information arrive, et la
            # série de rappels s'arrête là, quoi qu'ait entendu la transcription.
            limite = self._manager.snooze_limit()
            await self._speak(f"Tu as déjà reporté {plural(limite, 'fois', 'fois')}. "
                              "Je te lis le rapport maintenant, puis je te laisse.")
            await self._post(text_channel,
                             f"⏰ Plafond de {plural(limite, 'report')} atteint : "
                             "rapport livré sans nouveau rappel.")
        if decision == "cancel":
            await self._speak("Très bien, je te laisse. Bonne journée.")
            await self._post(text_channel, "Rapport annulé pour ce matin. Il reste consultable ici :")
            await self._post_report(text_channel)
            return self._done(CallOutcome.CANCELLED, f"Annulé (« {heard} »).")

        await self._deliver()
        await self._follow_up()
        await self._speak("C'est tout pour ce matin. Bonne journée.")
        await self._post_report(text_channel)
        return self._done(CallOutcome.DELIVERED, "Rapport lu à voix haute.")

    async def _ask_decision(self, text_channel) -> tuple[str, str]:
        """Deux tentatives d'écoute, puis repli écrit, puis défaut prudent.

        Le défaut est « maintenant » : quelqu'un s'est levé et a rejoint le
        salon vocal ; le faire attendre en silence serait le pire des choix.
        """
        for attempt in (1, 2):
            heard = await self._listen()
            decision = interpret_answer(heard)
            if decision != "unclear":
                return decision, heard
            if attempt == 1:
                await self._speak("Je n'ai pas compris. Dis « maintenant » ou « dans cinq minutes ».")
        if self._sink is None:
            # Aucune écoute possible : on demande une réponse écrite, ce qui
            # reste gratuit et fonctionne sur téléphone.
            heard = await self._ask_in_text(text_channel)
            decision = interpret_answer(heard)
            if decision != "unclear":
                return decision, heard
        return "now", ""

    async def _ask_in_text(self, text_channel) -> str:
        """Repli : la réponse arrive par message, pas par la voix."""
        await self._speak("Je ne peux pas t'entendre. Réponds-moi par écrit dans le salon.")
        await self._post(text_channel,
                         "🎙️ Je n'ai pas de capture audio (`discord-ext-voice-recv` absent). "
                         "Réponds ici : **maintenant** ou **dans 5 minutes**.")
        owner = str(self._setting("owner_user_id", "") or "").strip()
        bot = self._engine.bot

        def check(message) -> bool:
            if message.channel.id != text_channel.id or message.author.bot:
                return False
            return not owner.isdigit() or int(message.author.id) == int(owner)

        try:
            message = await bot.wait_for("message", check=check, timeout=120.0)
        except asyncio.TimeoutError:
            return ""
        return str(message.content or "")

    async def _deliver(self) -> None:
        """Égrène le rapport point par point, avec une respiration entre deux."""
        await self._speak("Voici ton rapport.")
        for section in self._report.sections:
            await self._speak(section.speech())
            await asyncio.sleep(0.4)

    async def _follow_up(self) -> None:
        """Questions de suivi, dans la limite du nombre configuré."""
        if self._sink is None:
            return
        limit = int(self._setting("max_questions", 4) or 0)
        if limit <= 0:
            return
        await self._speak("Une question sur ce rapport ?")
        for _ in range(limit):
            heard = await self._listen()
            if not heard.strip():
                await self._speak("Je n'ai rien entendu.")
                return
            if wants_to_end(heard):
                return
            answer = await self._engine.llm(
                f"Rapport du matin :\n{self._report.as_text()}\n\nQuestion : {heard}",
                system=("Tu es JARVIS, au téléphone. Réponds en français, en deux phrases "
                        "maximum, uniquement à partir du rapport ci-dessus. Si le rapport ne "
                        "contient pas la réponse, dis-le franchement."),
                max_tokens=180)
            await self._speak(answer)

    # -- primitives -------------------------------------------------------
    async def _speak(self, text: str) -> bool:
        """Synthétise et joue `text` dans le salon vocal. False si c'est impossible."""
        text = sanitize_for_speech(str(text or "").strip())
        client = self._voice_client
        if not text or client is None or not getattr(client, "is_connected", lambda: False)():
            return False
        loop = asyncio.get_running_loop()
        voice = str(self._setting("voice", DEFAULT_VOICE) or DEFAULT_VOICE)

        def synthesize():
            return self._core.tts_fallback.synthesize(text, voice_id=voice)

        try:
            result = await loop.run_in_executor(None, synthesize)
        except Exception:
            result = None
        if not result:
            return False
        audio, _mime = result

        path = Path(tempfile.gettempdir()) / f"jarvis-call-{int(time.time() * 1000)}.mp3"
        path.write_bytes(audio)
        finished = asyncio.Event()
        try:
            import discord

            # `after` est appelé depuis un thread de discord.py : on repasse par
            # la boucle pour réveiller l'attente sans course.
            source = discord.FFmpegPCMAudio(str(path))
            while client.is_playing():
                await asyncio.sleep(0.1)
            client.play(source, after=lambda _e: loop.call_soon_threadsafe(finished.set))
            # Garde-fou : un FFmpeg qui ne rend jamais la main ne doit pas figer
            # l'appel. 180 s couvre largement la plus longue section du rapport.
            await asyncio.wait_for(finished.wait(), timeout=180.0)
            return True
        except Exception as exc:
            self._engine.audit("discord.call.speak_failed", status="error",
                               detail=self._engine.redact(exc))
            return False
        finally:
            try:
                path.unlink()
            except Exception:
                pass

    async def _listen(self) -> str:
        """Capture une phrase et la transcrit. Chaîne vide si rien n'est exploitable."""
        client = self._voice_client
        recv = voice_recv_module()
        if client is None or recv is None or not hasattr(client, "listen"):
            return ""
        if not self._core.stt.available():
            return ""
        if self._sink is None:
            owner = str(self._setting("owner_user_id", "") or "").strip()
            self._sink = make_user_sink(int(owner) if owner.isdigit() else 0)
            if self._sink is None:
                return ""
            client.listen(self._sink)
        self._sink.take()                       # on part d'un tampon propre

        deadline = time.monotonic() + MAX_UTTERANCE_S
        started = False
        while time.monotonic() < deadline:
            await asyncio.sleep(0.15)
            if self._sink.has_audio():
                started = True
            elif not started:
                continue
            if started and self._sink.idle_for() >= SILENCE_S:
                break
        pcm = self._sink.take()
        # Moins d'une demi-seconde : une porte qui claque, pas une phrase.
        if len(pcm) < SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH // 2:
            return ""

        loop = asyncio.get_running_loop()

        def transcribe():
            return self._core.stt.transcribe(pcm_to_wav(pcm), language="fr", suffix=".wav")

        try:
            result = await loop.run_in_executor(None, transcribe)
        except Exception as exc:
            self._engine.audit("discord.call.stt_failed", status="error",
                               detail=self._engine.redact(exc))
            return ""
        return str(result.get("text") or "").strip()

    async def _post(self, channel, text: str) -> None:
        try:
            await self._engine.send_message(channel.id, text)
        except Exception:
            pass                                 # un ping raté ne casse pas l'appel

    async def _post_report(self, channel) -> None:
        """Publie le rapport par blocs : Discord refuse au-delà de 2000 caractères."""
        body = self._report.as_text() or "Aucune donnée collectée."
        for chunk in _chunks(body, 1900):
            await self._post(channel, chunk)

    async def _disconnect(self) -> None:
        client, self._voice_client = self._voice_client, None
        if client is None:
            return
        try:
            if hasattr(client, "stop_listening"):
                client.stop_listening()
        except Exception:
            pass
        try:
            await client.disconnect(force=True)
        except Exception:
            pass
        self._sink = None

    def _done(self, outcome: str, detail: str) -> dict[str, Any]:
        self._engine.audit(f"discord.call.{outcome}", status="ok" if outcome in (
            CallOutcome.DELIVERED, CallOutcome.POSTED, CallOutcome.SNOOZED,
            CallOutcome.CANCELLED) else "warn",
            detail={"raison": self._reason, "detail": detail})
        self._engine.emit("discord.call.finished", {"outcome": outcome, "detail": detail})
        return {"ok": outcome != CallOutcome.FAILED, "outcome": outcome, "detail": detail,
                "report": self._report.to_dict()}


def _chunks(text: str, size: int) -> list[str]:
    """Découpe sur les sauts de ligne quand c'est possible, brutalement sinon."""
    out: list[str] = []
    for block in text.split("\n\n"):
        if out and len(out[-1]) + len(block) + 2 <= size:
            out[-1] = f"{out[-1]}\n\n{block}"
            continue
        while len(block) > size:
            out.append(block[:size])
            block = block[size:]
        out.append(block)
    return [c for c in out if c.strip()]


# ---------------------------------------------------------------------------
# 5. Le planificateur
# ---------------------------------------------------------------------------
class MorningCallManager:
    """Déclenche l'appel matinal : échéance quotidienne, report, exécution manuelle."""

    def __init__(self, core) -> None:
        self.core = core
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._in_call = False
        self._next_at: float | None = None
        self._snooze_at: float | None = None
        self._snooze_count = 0          # reports consécutifs, remis à zéro chaque matin
        self._last: dict[str, Any] = {}
        self.collector = MorningReportCollector(core)

    # -- moteur Discord ---------------------------------------------------
    @property
    def engine(self):
        """Moteur Discord du cœur, créé à la demande (comme les outils le font)."""
        engine = getattr(self.core, "discord", None)
        if engine is None:
            from .discord_engine import DiscordEngine
            engine = DiscordEngine(self.core)
            self.core.discord = engine
        return engine

    # -- réglages ---------------------------------------------------------
    def _setting(self, key: str, default: Any) -> Any:
        return self.core.settings.get(SETTINGS_SECTION, key, default)

    def _trigger(self) -> dict[str, Any]:
        return {"type": "daily", "hour": int(self._setting("hour", 6) or 0),
                "minute": int(self._setting("minute", 0) or 0)}

    def next_run(self, after: float | None = None) -> float:
        """Prochaine échéance quotidienne. Délègue le calendrier à AutomationManager."""
        try:
            nxt = self.core.automations.next_run(self._trigger(), after)
        except Exception:
            nxt = None
        return float(nxt) if nxt else (after or time.time()) + 86400

    # -- état -------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        from datetime import datetime

        recv = voice_recv_module()
        # `_next_at` n'existe qu'après `start()` : on calcule à la volée pour que
        # le statut reste honnête quand on l'interroge avant le démarrage.
        nxt = self._snooze_at or self._next_at or self.next_run()
        return {
            "enabled": bool(self._setting("enabled", False)),
            "hour": int(self._setting("hour", 6) or 0),
            "minute": int(self._setting("minute", 0) or 0),
            "text_channel": str(self._setting("text_channel", "")),
            "voice_channel": str(self._setting("voice_channel", "")),
            "owner_user_id": str(self._setting("owner_user_id", "")),
            "in_call": self._in_call,
            "next_run_at": nxt,
            "next_run_iso": datetime.fromtimestamp(nxt).isoformat(timespec="seconds") if nxt else "",
            "snoozed": bool(self._snooze_at),
            "snoozes": self._snooze_count,
            "max_snoozes": self.snooze_limit(),
            "last": dict(self._last),
            "capabilities": {
                "tts": bool(self.core.tts_fallback.available()),
                "voice_receive": recv is not None,
                "transcription": bool(self.core.stt.available()),
            },
            "hints": self._hints(recv),
        }

    def _hints(self, recv) -> list[str]:
        """Ce qui manque, et la commande exacte pour y remédier."""
        hints: list[str] = []
        if not self.core.tts_fallback.available():
            hints.append("Voix de synthèse absente : pip install -U edge-tts")
        if recv is None:
            hints.append("Écoute impossible (JARVIS parlera sans entendre) : "
                         "pip install -U discord-ext-voice-recv")
        if not self.core.stt.available():
            hints.append("Transcription absente : pip install -U faster-whisper")
        if not str(self._setting("text_channel", "")).strip():
            hints.append("Aucun salon texte configuré (réglages → discord_call.text_channel).")
        if not str(self._setting("voice_channel", "")).strip():
            hints.append("Aucun salon vocal configuré (réglages → discord_call.voice_channel).")
        return hints

    # -- déclenchement ----------------------------------------------------
    def snooze_limit(self) -> int:
        return max(0, int(self._setting("max_snoozes", MAX_SNOOZES) or 0))

    def snooze(self, minutes: int = 5) -> float:
        """Reprogramme un rappel dans `minutes`. Renvoie 0.0 si le plafond est atteint.

        L'appelant doit tester la valeur de retour : un report refusé signifie
        « livre le rapport maintenant », pas « réessaie ».
        """
        limit = self.snooze_limit()
        if self._snooze_count >= limit:
            self.core.events.emit("discord.call.snooze_refused",
                                  {"count": self._snooze_count, "limit": limit})
            self.engine.audit("discord.call.snooze_refused", status="warn",
                              detail={"reports": self._snooze_count, "plafond": limit})
            return 0.0
        self._snooze_count += 1
        self._snooze_at = time.time() + max(1, int(minutes)) * 60
        self.core.events.emit("discord.call.snoozed",
                              {"minutes": minutes, "at": self._snooze_at})
        return self._snooze_at

    def run_now(self, *, reason: str = "manuel") -> dict[str, Any]:
        """Lance un appel immédiatement. Bloque jusqu'à la fin de l'appel.

        Appelé depuis un thread tiers (outil, boucle de planification), jamais
        depuis la boucle du bot.
        """
        with self._lock:
            if self._in_call:
                return {"ok": False, "outcome": CallOutcome.FAILED,
                        "detail": "Un appel est déjà en cours."}
            self._in_call = True
            # Un appel qui n'est PAS un rappel ouvre une nouvelle série : le
            # compteur de reports ne doit pas traîner d'un matin sur l'autre.
            if reason != "rappel":
                self._snooze_count = 0
        try:
            engine = self.engine
            if not engine.available:
                return self._record({"ok": False, "outcome": CallOutcome.FAILED,
                                     "detail": "discord.py n'est pas installé."})
            if not engine.connected:
                started = engine.start()
                if not started.get("ok"):
                    return self._record({"ok": False, "outcome": CallOutcome.FAILED,
                                         "detail": "Bot Discord non connecté : "
                                                   + str(started.get("error") or "")})
            report = self.collector.collect()
            session = MorningCallSession(self, report, reason=reason)
            # L'appel dure des minutes : `engine.submit()` et son délai maximum
            # ne conviennent pas. On soumet la coroutine et on attend nous-mêmes,
            # avec une borne dérivée du temps d'attente configuré.
            budget = float(self._setting("wait_user_s", 300) or 300) + CALL_BUDGET_MARGIN_S
            future = engine.spawn(session.run())
            try:
                return self._record(future.result(timeout=budget))
            except FutureTimeout:
                # Abandonner l'attente ne suffit pas : sans `cancel()`, la
                # coroutine continue de tourner dans la boucle du bot, micro
                # ouvert et salon vocal occupé, pendant que le verrou `_in_call`
                # est déjà relâché — un second appel pourrait démarrer par
                # dessus. `cancel()` propage l'annulation jusqu'à la tâche
                # asyncio, dont le `finally` referme la connexion vocale.
                future.cancel()
                return self._record({
                    "ok": False, "outcome": CallOutcome.FAILED,
                    "detail": f"Appel interrompu : aucune issue après {budget / 60:.0f} minutes."})
        except Exception as exc:
            return self._record({"ok": False, "outcome": CallOutcome.FAILED,
                                 "detail": self.engine.redact(exc)[:300]})
        finally:
            with self._lock:
                self._in_call = False

    def _record(self, result: dict[str, Any]) -> dict[str, Any]:
        self._last = {"at": time.time(), "outcome": result.get("outcome"),
                      "detail": str(result.get("detail") or "")[:300]}
        return result

    # -- boucle de fond ---------------------------------------------------
    def start(self) -> threading.Thread:
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        self._next_at = self.next_run()

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    self._tick()
                except Exception as exc:          # la boucle ne meurt jamais
                    self.core.events.emit("discord.call.error", {"error": str(exc)[:300]})
                self._stop.wait(DEFAULT_TICK_S)

        self._thread = threading.Thread(target=loop, daemon=True, name="jarvis-morning-call")
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop.set()

    def _tick(self) -> None:
        if not self._setting("enabled", False):
            # Désactivé : on garde l'échéance à jour pour que le statut reste
            # honnête si l'utilisateur réactive la fonction.
            self._next_at = self.next_run()
            return
        now = time.time()
        if self._snooze_at and now >= self._snooze_at:
            self._snooze_at = None
            threading.Thread(target=self.run_now, kwargs={"reason": "rappel"},
                             daemon=True, name="jarvis-morning-call-snooze").start()
            return
        if self._next_at is None:
            self._next_at = self.next_run()
            return
        if now < self._next_at:
            return
        self._next_at = self.next_run(now)
        threading.Thread(target=self.run_now, kwargs={"reason": "planifié"},
                         daemon=True, name="jarvis-morning-call-run").start()
