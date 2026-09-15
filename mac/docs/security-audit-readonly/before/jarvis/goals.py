"""Objectifs utilisateur déterministes et conditions de complétion."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class UserGoal:
    type: str
    target: str = ""
    scope: str = ""
    requirements: list[str] = field(default_factory=list)
    state: dict[str, bool] = field(default_factory=dict)


# Une demande d'ÉCRITURE n'est jamais un objectif de lecture, même quand elle
# contient le mot « contenu » (« crée X avec ce contenu », « écris Y dont le
# contenu est ... »). Sans ce garde-fou, une création était classée
# get_file_contents puis routée vers ssh.read_file : le fichier n'était
# jamais écrit et JARVIS répondait en ouvrant un fichier sans rapport.
_WRITE_INTENT = re.compile(
    "\\b(?:"
    "cr[eé]e|cr[eé]er|cr[eé][eé]|"
    "[eé]cris|[eé]crire|[eé]crit|"
    "ajoute|ajouter|ins[eè]re|ins[eé]rer|"
    "modifie|modifier|remplace|remplacer|corrige|corriger|"
    "renomme|renommer|supprime|supprimer|efface|effacer|"
    "sauvegarde|sauvegarder|enregistre|enregistrer|"
    "d[eé]ploie|d[eé]ployer|patch|touch|write"
    ")\\b|\\bmets?\\s+[aà]\\s+jour\\b", re.I)

# Chemins et noms de fichiers : « jarvis-write-test.php » contient « write »,
# « create_user.py » contient « create ». Ces occurrences NE SONT PAS une
# intention d'écriture : on les retire du texte avant de chercher le verbe.
_FILE_TOKEN = re.compile(
    r"[\w.@~/\\-]*[\w@-]+\.[A-Za-z0-9]{1,8}\b"   # nom de fichier, avec ou sans chemin
    r"|[/~][\w./\\@-]+")                          # chemin absolu sans extension


def has_write_intent(text: str) -> bool:
    """Vrai si l'utilisateur demande une écriture, hors noms de fichiers."""
    return bool(_WRITE_INTENT.search(_FILE_TOKEN.sub(" ", text or "")))


class GoalCompletionChecker:
    def detect(self, text: str, context: dict[str, Any] | None = None) -> UserGoal | None:
        text = (text or "").strip()
        if has_write_intent(text):
            return None
        wants_contents = bool(re.search(r"\b(?:contenu|affiche|montre|lis|lire)\b", text, re.I))
        wants_search = bool(re.search(r"\b(?:trouve|chercher|cherche|localise|rep[èe]re)\b", text, re.I))
        target = ""
        m = re.search(r"\b([\w.-]+\.(?:php|html?|css|js|json|ya?ml|py|txt|log|sql|sh))\b", text, re.I)
        if m: target = m.group(1)
        if not target and context: target = str(context.get("last_remote_path") or context.get("last_file") or "")
        if wants_contents and target:
            return UserGoal("get_file_contents", target, "active_remote_context" if context and context.get("target_type") == "remote_server" else "", ["file_located", "file_read"])
        if wants_search and target:
            return UserGoal("search_file", target, "active_remote_context" if context and context.get("target_type") == "remote_server" else "", ["file_located"])
        return None

    def observe(self, goal: UserGoal, tool_name: str, result: Any) -> bool:
        data = getattr(result, "data", None) or {}
        output = getattr(result, "output", "") or ""
        ok = bool(getattr(result, "ok", False))
        if tool_name in {"ssh.run", "ssh.search", "fs.search"} and ok and (goal.target.casefold() in output.casefold() or data.get("path")):
            goal.state["file_located"] = True
        if tool_name in {"ssh.read_file", "fs.read"} and ok and bool(output.strip()):
            goal.state["file_read"] = True
        return all(goal.state.get(req, False) for req in goal.requirements)
