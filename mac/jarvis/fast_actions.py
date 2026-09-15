"""Routage deterministe, avant tout appel LLM.

Deux familles d'intentions sont servies ici, sans jamais reveiller
l'orchestrateur ni Ollama :

  - les actions Windows courtes (« ouvre Edge ») ;
  - les echanges conversationnels fermes (salutation, identite, nom de
    l'utilisateur), dont la reponse est entierement contenue dans le profil
    local.

Regle de surete : une regle ne se declenche que si elle couvre le message
ENTIER. « bonjour » passe par le fast path, « bonjour, ouvre Edge et
resume mes mails » part a l'agent. Une salutation suivie d'une demande
reste une demande.
"""
from __future__ import annotations

import re
import time
import unicodedata
from datetime import datetime
from typing import Any


def _normalize(text: str) -> str:
    """Minuscules sans accent ni ponctuation, pour comparer des formulations.

    « Présente-toi ! » et « presente toi » deviennent la meme chaine, ce qui
    evite d'enumerer toutes les variantes typographiques dans les motifs.
    """
    folded = unicodedata.normalize("NFD", (text or "").strip().casefold())
    folded = "".join(c for c in folded if unicodedata.category(c) != "Mn")
    folded = re.sub(r"[^\w\s]", " ", folded)
    return re.sub(r"\s+", " ", folded).strip()


# Prefixe vocal / reveil : ignore « Jarvis », « ok Jarvis » en tete.
_WAKE = r"(?:(?:ok |dis |hey |salut )?jarvis )?"

# Signaux d'action : si l'un d'eux est present, le LLM recoit les outils.
_TOOL_HINT = re.compile(
    r"\b(?:ouvre|ouvrir|lance|lancer|ferme|fermer|quitte|quitter|"
    r"execute|executer|exécute|exécuter|"
    r"cherche|recherch|google|internet|\bweb\b|"
    r"affiche|montre|lis|lire|contenu|"
    r"connecte|connexion|\bssh\b|\bftp\b|sftp|docker|cpanel|"
    r"envoie|envoi|\bmails?\b|email|gmail|"
    r"genere|génère|génér|"
    r"\bimages?\b|blender|\b3d\b|avatar|"
    r"audit|deploy|déploie|corrige|modifie|patch|installe|"
    r"telecharge|télécharge|navigue|scrape|sync|synchronis|"
    r"compare|onglet|\bsheets?\b|classeur|"
    r"serveur|\bcrm\b|facture|invoice|\bn8n\b|workflow|"
    r"\bcode\b|fichier|\bfile\b|dossier|repertoire|répertoire|"
    r"marketplace\.php|index\.php|"
    r"publie|publier|publication|publique|poste|poster|"
    r"discord|canal|\bsalon\b)\b",
    re.I,
)
_FILE_EXT = re.compile(
    r"\b[\w.-]+\.(?:php|html?|css|js|json|py|ya?ml|txt|log|blend|glb|png|jpe?g)\b",
    re.I,
)
_URL = re.compile(r"https?://|docs\.google\.com", re.I)


def tools_needed(text: str) -> bool:
    """True si le message a une chance de requerir un outil.

    Conservateur : un faux negatif (pas d'outils alors qu'il en fallait)
    casse une action reelle. Un faux positif ne coute que de la latence.
    """
    raw = (text or "").strip()
    if not raw:
        return False
    if _FILE_EXT.search(raw) or _URL.search(raw):
        return True
    return bool(_TOOL_HINT.search(_normalize(raw)))


class FastActionRouter:
    APPS = {
        "edge": "edge", "microsoft edge": "edge", "navigateur edge": "edge",
        "chrome": "chrome", "google chrome": "chrome", "spotify": "spotify",
        "notepad": "notepad", "bloc-notes": "notepad", "bloc note": "notepad",
        "calculatrice": "calculatrice", "calculator": "calculatrice",
        "explorateur": "explorer", "explorer": "explorer",
        "paramètres windows": "settings", "parametres windows": "settings", "réglages": "settings", "settings": "settings",
    }
    _OPEN = re.compile(r"^(?:ouvre|ouvrir|lance|lancer|démarre|demarre|start)\s+(.+?)\s*[.!?]*$", re.I)
    _CLOSE = re.compile(r"^(?:ferme|fermer|quitte|quitter)\s+(.+?)\s*[.!?]*$", re.I)

    # ------------------------------------------------------------------
    # Intentions conversationnelles fermees
    # ------------------------------------------------------------------
    # `fullmatch` sur le message normalise : tout mot en trop fait echouer la
    # regle et renvoie la demande a l'agent. C'est volontairement strict,
    # un faux negatif ne coute qu'une latence, un faux positif coute une
    # reponse a cote.
    _CONVERSATION = (
        ("greeting", re.compile(
            _WAKE
            + r"(?:re)?(?:bonjour|bonsoir|salut|coucou|hello|hey|yo|bonne nuit)"
            + r"(?: jarvis| a toi| tout le monde)?"
            + r"(?: (?:ca va|comment (?:ca va|tu vas|vous allez|allez vous)))?")),
        ("how_are_you", re.compile(
            _WAKE
            + r"(?:comment (?:ca va|tu vas|vas tu|allez vous|vous allez)"
            + r"|ca va(?: bien)?"
            + r"|tu vas bien|vous allez bien)")),
        ("identity", re.compile(
            _WAKE
            + r"(?:qui es(?: |-)?tu|tu es qui|t es qui|c est quoi ton nom"
            + r"|quel est ton nom|comment tu t appelles|comment t appelles tu"
            + r"|presente toi|tu es quoi|c est qui)")),
        ("user_name", re.compile(
            _WAKE
            + r"(?:comment je m appelle|comment m appelle je|quel est mon nom"
            + r"|c est quoi mon nom|je m appelle comment|tu (?:te )?souviens"
            + r"(?: de)? mon nom|tu connais mon nom|dis moi mon nom"
            + r"|qui suis je|je suis qui)")),
        ("thanks", re.compile(
            _WAKE
            + r"(?:merci|merci beaucoup|merci bien|nickel merci|super merci)"
            + r"(?: jarvis| a toi| bien)?")),
        ("capabilities", re.compile(
            _WAKE
            + r"(?:que (?:peux|sais) tu faire|tu (?:peux|sais) faire quoi"
            + r"|c est quoi tes (?:capacites|fonctions)|aide moi"
            + r"|a quoi tu sers|\baide\b)")),
        ("time", re.compile(
            _WAKE
            + r"(?:quelle heure (?:est[- ]ce )?il|il est quelle heure"
            + r"|on est quel jour|quelle est la date"
            + r"|c est quel jour|date d aujourd hui)")),
    )

    def __init__(self, core) -> None:
        self.core = core

    # -- profil local : unique source de verite, aucune inference ----------
    def _profile(self) -> dict[str, str]:
        get = self.core.settings.get
        user = str(get("general", "user_name", "") or "").strip()
        if not user:
            user = self._name_from_memory()
        return {
            "assistant": str(get("general", "assistant_name", "JARVIS") or "JARVIS"),
            "user": user,
            "title": str(get("general", "operator_title", "") or "").strip(),
        }

    def _name_from_memory(self) -> str:
        """Prenom deja extrait en memoire locale, sans embedding ni LLM."""
        try:
            row = self.core.db.one(
                "SELECT content FROM memories WHERE content LIKE ? OR content LIKE ? "
                "ORDER BY created_at DESC LIMIT 1",
                ("Prénom :%", "Prenom :%"),
            )
        except Exception:
            return ""
        if not row:
            return ""
        _, _, name = str(row["content"] or "").partition(":")
        return name.strip()

    def match_conversation(self, text: str) -> str | None:
        """Nom de l'intention conversationnelle, ou None si l'agent doit voir le message."""
        clean = _normalize(text)
        # Un message long n'est jamais une simple salutation : garde-fou bon
        # marche avant meme d'evaluer les expressions regulieres.
        if not clean or len(clean) > 80:
            return None
        for name, pattern in self._CONVERSATION:
            if pattern.fullmatch(clean):
                return name
        return None

    def _greeting_reply(self, profile: dict[str, str]) -> str:
        hour = datetime.now().hour
        opening = "Bonjour" if 5 <= hour < 18 else "Bonsoir"
        user = profile["user"]
        return f"{opening}, {user}." if user else f"{opening}."

    def _identity_reply(self, profile: dict[str, str]) -> str:
        who = profile["assistant"]
        user = profile["user"]
        suffix = f" de {user}" if user else ""
        return (f"Je suis {who}, l'assistant{suffix} sur ce PC. "
                "Je peux piloter tes applications, tes fichiers et tes agents.")

    def _user_name_reply(self, profile: dict[str, str]) -> str:
        user = profile["user"]
        if not user:
            return ("Je n'ai pas ton nom en memoire. Tu peux le renseigner "
                    "dans Reglages > General.")
        title = profile["title"]
        if title:
            return f"Tu t'appelles {user}, {title}."
        return f"Tu t'appelles {user}."

    def _how_are_you_reply(self, profile: dict[str, str]) -> str:
        user = profile["user"]
        if user:
            return f"Tout va bien, {user}. Je suis operationnel."
        return "Tout va bien, je suis operationnel."

    def _capabilities_reply(self, profile: dict[str, str]) -> str:
        who = profile["assistant"]
        return (f"Je suis {who}. Je peux ouvrir tes applications, lire tes fichiers, "
                "piloter SSH, chercher sur le web, gerer tes taches et tes agents. "
                "Dis-moi simplement ce dont tu as besoin.")

    def _time_reply(self) -> str:
        now = datetime.now()
        days = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
        months = ("janvier", "fevrier", "mars", "avril", "mai", "juin",
                  "juillet", "aout", "septembre", "octobre", "novembre", "decembre")
        return (f"Il est {now:%H:%M}, nous sommes le {days[now.weekday()]} "
                f"{now.day} {months[now.month - 1]} {now.year}.")

    def _conversation_reply(self, intent: str) -> str:
        profile = self._profile()
        if intent == "greeting":
            return self._greeting_reply(profile)
        if intent == "identity":
            return self._identity_reply(profile)
        if intent == "user_name":
            return self._user_name_reply(profile)
        if intent == "how_are_you":
            return self._how_are_you_reply(profile)
        if intent == "capabilities":
            return self._capabilities_reply(profile)
        if intent == "time":
            return self._time_reply()
        return "Avec plaisir."

    def match(self, text: str) -> dict[str, Any] | None:
        clean = (text or "").strip()
        m = self._OPEN.match(clean)
        if m:
            raw = re.sub(r"\s+(?:sur|dans)\s+.+$", "", m.group(1), flags=re.I).strip().casefold()
            if raw in self.APPS:
                return {"intent": "open_app", "name": self.APPS[raw], "raw": raw}
        m = self._CLOSE.match(clean)
        # Fermeture : exposée dès qu'un outil app.close sera enregistré.
        if m and "app.close" in {t.id for t in self.core.registry.all()} and m.group(1).strip().casefold() in self.APPS:
            return {"intent": "close_app", "name": self.APPS[m.group(1).strip().casefold()], "raw": m.group(1).strip()}
        return None

    def execute_conversation(self, text: str, conversation_id: str = "") -> dict[str, Any] | None:
        """Repond depuis le profil local, sans LLM ni outil. None = pas concerne."""
        intent = self.match_conversation(text)
        if intent is None:
            return None
        started = time.perf_counter()
        response = self._conversation_reply(intent)
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        print(f"FAST INTENT: {intent} ({elapsed}ms)", flush=True)
        # L'interface suit ces evenements : sans eux l'avatar resterait fige
        # sur l'etat precedent malgre une reponse deja affichee.
        self.core.events.emit("jarvis.state", {"state": "SPEAKING", "reason": "fast_reply"})
        self.core.events.emit("llm.delta", {
            "text": response, "chunk": response, "conversation_id": conversation_id,
            "fast_intent": intent,
        }, cache=False)
        self.core.conversations.add_message(
            conversation_id, "assistant", response,
            meta={"fast_intent": intent, "latency_ms": elapsed})
        return {"ok": True, "response": response, "action": "fast.reply",
                "fast_intent": intent, "latency_ms": elapsed,
                "conversation_id": conversation_id, "tools_used": []}

    def execute(
        self, text: str, conversation_id: str = "",
        *, resolved_intent=None, execution_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if execution_policy is not None and execution_policy.get("fast_actions_allowed") is False:
            return None

        # Voie conversationnelle : evaluee en premier car c'est la plus courte
        # et la seule qui n'execute aucun outil. Elle ne depend donc pas de
        # `tools_allowed`, contrairement aux actions applicatives ci-dessous.
        conversation = self.execute_conversation(text, conversation_id)
        if conversation is not None:
            return conversation

        if execution_policy is not None and execution_policy.get("tools_allowed") is False:
            return None
        hit = self.match(text)
        if not hit:
            return None
        started = time.perf_counter(); intent = hit["intent"]
        print(f"FAST INTENT: {intent}", flush=True)
        self.core.events.emit("jarvis.state", {"state": "ACTING", "reason": "fast_action"})
        if intent == "open_app":
            result = self.core.runner.run("app.open", {"name": hit["name"]}, agent="jarvis", conversation_id=conversation_id)
        else:
            result = self.core.runner.run("app.close", {"name": hit["name"]}, agent="jarvis", conversation_id=conversation_id)
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        print(f"APP RESOLVED: {hit['name']}\nACTION: app.{intent[0:4]}\nSUCCESS: {result.ok}\nTOTAL FAST ACTION TIME: {elapsed}ms", flush=True)
        labels = {"edge": "Edge", "chrome": "Chrome", "spotify": "Spotify", "notepad": "Le bloc-notes", "calculatrice": "La calculatrice", "explorer": "L’explorateur", "settings": "Les paramètres"}
        label = labels.get(hit["name"], hit["name"])
        response = result.output or (f"{label} est ouvert." if result.ok else f"{label} est introuvable.")
        self.core.conversations.add_message(conversation_id, "assistant", response, meta={"fast_intent": intent, "latency_ms": elapsed})
        return {"ok": result.ok, "response": response, "action": "app.open", "fast_intent": intent,
                "latency_ms": elapsed, "conversation_id": conversation_id, "tools_used": ["app.open"]}
