"""Routage déterministe des actions Windows courtes, avant tout appel LLM."""
from __future__ import annotations

import re
import time
from typing import Any


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

    def __init__(self, core) -> None:
        self.core = core

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

    def execute(
        self, text: str, conversation_id: str = "",
        *, resolved_intent=None, execution_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if execution_policy is not None and execution_policy.get("fast_actions_allowed") is False:
            return None
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
