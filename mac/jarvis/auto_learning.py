"""Auto-Learning — JARVIS apprend de ses réussites vérifiées.

Cycle : RECALL → ACT → VERIFY → LEARN.

* RECALL  : avant une tâche complexe, recherche ciblée dans le Brain.
* VERIFY  : après une tâche réussie, juger si le résultat est durable/réutilisable.
* LEARN   : déduplication, création ou mise à jour de la connaissance, ajustement
            de confiance, suivi des échecs, dépréciation.

Confiance : une procédure validée plusieurs fois devient plus fiable,
une procédure qui échoue perd de la confiance, une procédure remplacée devient
`deprecated`. Aucun secret n'est jamais stocké dans le Brain.
"""
from __future__ import annotations

import re
import time
from typing import Any

from .db import dumps, loads, new_id
from .intents import resolve_connector_intent

# Termes qui signalent qu'un résultat est durable et réutilisable.
_DURABLE_HINTS = (
    "le problème", "corrigé", "résolu", "réglé", "solution", "procédure",
    "le bug", "fix", "solution", "diagnostic", "configuré", "déployé",
    "procédure", "correctement", "réussi", "je l'ai", "voilà", "c'était",
)
# Termes qui signalent un résultat éphémère (ne pas stocker).
_EPHEMERAL_HINTS = (
    "aujourd'hui", "ce matin", "ce soir", "météo", "heure", "raconte",
    "une blague", "bonjour", "salut",
)
_HEURISTIC_MIN = 140


class AutoLearning:
    def __init__(self, core) -> None:
        self._core = core

    # ------------------------------------------------------------- RECALL
    def recall(self, text: str, limit: int = 5) -> dict[str, Any]:
        """Recherche ciblée avant une tâche. Retourne les connaissances
        vraiment pertinentes (jamais toute la base)."""
        core = self._core
        results = core.memory.knowledge_search(text, limit=limit)
        if not results:
            return {"found": False, "items": [], "summary": ""}
        core.brain.emit_search(
            "knowledge", query=text,
            ids=[r["id"] for r in results],
            labels=[r["title"][:80] for r in results],
        )
        return {"found": True, "items": results, "summary": "\n".join(
            f"- [{r['kind']}] {r['title']} (confiance {r['confidence_score']:.0%})" for r in results)}

    def _build_recall_section(self, text: str) -> str:
        recall = self.recall(text)
        if not recall["found"]:
            return ""
        return f"\n\nProcédures déjà connues à appliquer si pertinentes :\n{recall['summary']}"

    # ------------------------------------------------------------- VERIFY
    def is_learnable(self, request: str, response: str, tools_used: list[str]) -> bool:
        """Une réponse longue, durable, qui a mobilisé des outils et réussi."""
        if not response:
            return False
        # Anti-hallucination : une action connecteur claire qui se termine SANS
        # outil exécuté est un résultat inventé. Jamais appris.
        if resolve_connector_intent(self._core, request) and not tools_used:
            return False
        if len(response) < _HEURISTIC_MIN and not tools_used:
            return False
        low = response.casefold()
        if any(h in low for h in _EPHEMERAL_HINTS) and len(response) < 240:
            return False
        if tools_used and len(tools_used) >= 1:
            return True
        return any(h in low for h in _DURABLE_HINTS)

    # ------------------------------------------------------------- LEARN
    def learn(
        self, *, request: str, response: str, tools_used: list[str],
        project: str = "", sink: None = None,
    ) -> dict[str, Any]:
        """Enregistre une nouvelle connaissance ou en améliore une existante."""
        core = self._core
        title = self._title_for(request, response)
        from .tool_identity import ensure_identity
        tool_ids = list(dict.fromkeys(ensure_identity().canonical(t) for t in tools_used))
        content = response.strip()
        tags = self._tags_for(request, tools_used)

        existing = self._find_duplicate(title, content)
        if existing:
            kid = existing["id"]
            core.events.emit("knowledge.learn.updated", {"id": kid, "title": title})
            updated = core.memory.knowledge_update(
                kid, content=content,
                tags=list(dict.fromkeys(existing.get("tags", []) + tags)),
                project=project or existing.get("project", ""),
                tools=list(dict.fromkeys([*existing.get("tools", []), *tool_ids])),
            )
            return {"action": "updated", "id": kid, "item": updated}

        item = core.memory.knowledge_add(
            title=title, content=content, kind="procedure",
            tags=tags, source="auto-learn", project=project,
            tools=tool_ids,
        )
        kid = item["id"]
        core.brain.add_node(
            kind="procedure", family="KNOWLEDGE", label=title[:120],
            ref_type="knowledge", ref_id=kid, meta={"tags": tags, "project": project},
        )
        core.events.emit("knowledge.learn.created", {"id": kid, "title": title, "tags": tags})
        return {"action": "created", "id": kid, "item": item}

    # ------------------------------------------------------------- helpers
    def _title_for(self, request: str, response: str) -> str:
        req = (request or "").strip()
        if req:
            return req[:180]
        first = (response or "").strip().splitlines()[0] if response else ""
        return (re.sub(r"[\*\`#]", "", first))[:180] or "Procédure JARVIS"

    def _tags_for(self, request: str, tools_used: list[str]) -> list[str]:
        tags = ["auto-learn"]
        for tool in tools_used or []:
            parts = tool.split(".")
            if len(parts) >= 2:
                tags.append(f"tool:{parts[0]}")
        low = (request or "").casefold()
        for kw in ("ssh", "serveur", "fichier", "déploiement", "deploiement", "n8n",
                   "couleur", "ollama", "database", "git"):
            if kw in low:
                tags.append(kw)
        return list(dict.fromkeys(tags))

    def _find_duplicate(self, title: str, content: str) -> dict[str, Any] | None:
        core = self._core
        exact = core.memory.knowledge_get_by_title(title)
        if exact:
            return exact
        words = set(re.findall(r"[\wàâäéèêëîïôöùûüç'-]{4,}", title.casefold()))
        if len(words) < 3:
            return None
        for item in core.memory.knowledge_list(limit=200):
            if item["status"] == "deprecated":
                continue
            title_words = set(re.findall(r"[\wàâäéèêëîïôöùûüç'-]{4,}", item["title"].casefold()))
            overlap = len(words & title_words) / max(1, len(words))
            if overlap >= 0.5:
                return item
        return None

    # ------------------------------------------------------- maintenance
    def maintenance(self) -> dict[str, Any]:
        """Détecte : doublons, vieilles fiches, statuts incohérents."""
        core = self._core
        now = time.time()
        dropped, archived = 0, 0
        for item in core.memory.knowledge_list(limit=500):
            # Une fiche jamais validée et abandonnée peut être archivée.
            if item["validation_count"] == 0 and (now - (item["updated_at"] or now)) > 60 * 86400:
                core.memory.knowledge_update(item["id"], status="archived")
                archived += 1
            # Une fiche remplacée par une plus récente de même titre est dépréciée.
            siblings = core.memory.knowledge_search(item["title"].split("\n")[0][:40], limit=10)
            newer = [s for s in siblings if s["id"] != item["id"] and s["updated_at"] > item["updated_at"]]
            if newer and item["status"] == "active":
                core.memory.knowledge_update(item["id"], status="deprecated")
                dropped += 1
        return {"archived": archived, "deprecated": dropped}

    # ------------------------------------------------------ logs interne
    def _log(self, kind: str, detail: str) -> None:
        if self._core.settings.get("developer", "debug", False):
            print(f"AUTO-LEARN {kind}: {detail}", flush=True)
