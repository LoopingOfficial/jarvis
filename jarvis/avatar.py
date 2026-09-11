"""Présence corporelle de JARVIS — côté serveur.

Ce module ne dessine rien : il traduit ce que JARVIS fait RÉELLEMENT en
intentions corporelles, puis les publie sur le bus d'événements. Le frontend
les transforme en mouvement.

Règle non négociable (spec §43) : une animation spécifique correspond toujours
à une action réelle. RECALLING n'est émis que si la mémoire est consultée,
WALKING que si un déplacement est demandé, SUCCESS qu'après une vraie réussite.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any

# Emplacements nommés de la scène (le backend ne connaît aucune coordonnée).
PLACES = ("home", "call", "desk", "brain", "center", "left_panel", "right_panel", "offstage")

# Gestes que le planificateur peut demander. Toute autre valeur est ignorée
# côté client : le modèle ne peut donc jamais produire un mouvement aberrant.
GESTURES = (
    "neutral", "explain", "open_hand", "point", "acknowledge", "agree", "disagree",
    "thinking", "success", "concern", "welcome", "question", "wait", "reassure",
    "arms_cross",
)

EMOTIONS = ("neutral", "friendly", "focused", "thinking", "concerned", "positive",
            "pleased", "attentive")

# Correspondance outil → attitude corporelle. Seuls les outils réellement
# exécutés déclenchent ces états.
TOOL_STATES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(memory|knowledge)\."), "RECALLING"),
    (re.compile(r"^(fs|terminal|code|git)\."), "CODING"),
    (re.compile(r"^(web|http)\."), "BROWSING"),
    (re.compile(r"^(deploy|docker|ssh|ftp|cpanel|whm)\."), "DEPLOYING"),
    (re.compile(r"^image\."), "ACTING"),
    (re.compile(r"^blender\."), "CODING"),
]


def state_for_tool(tool_id: str) -> str:
    for pattern, state in TOOL_STATES:
        if pattern.search(tool_id or ""):
            return state
    return "ACTING"


class AvatarDirector:
    """Metteur en scène : décide de l'attitude, jamais de la chorégraphie."""

    def __init__(self, core) -> None:
        self._core = core
        self._lock = threading.RLock()
        self.state = "IDLE"
        self.place = "home"
        self.view = "CALL"
        self._last_gesture = 0.0
        self._last_move = 0.0

    # -- réglages ---------------------------------------------------------
    @property
    def settings(self) -> dict[str, Any]:
        return self._core.settings.section("avatar")

    def enabled(self) -> bool:
        return bool(self.settings.get("enabled", True))

    # -- état -------------------------------------------------------------
    def set_state(self, state: str, *, reason: str = "", **extra) -> None:
        if not self.enabled():
            return
        with self._lock:
            if state == self.state and not extra:
                return
            self.state = state
        self._core.events.emit("avatar.state", {
            "state": state, "reason": reason[:160], **extra,
        })

    # -- gestes -----------------------------------------------------------
    def gesture(self, gesture: str, *, emotion: str = "", intensity: float = 0.5,
                reason: str = "") -> bool:
        """Demande un geste. Cadencé : un humain ne gesticule pas en continu."""
        if not self.enabled() or gesture not in GESTURES:
            return False
        now = time.time()
        min_gap = float(self.settings.get("gesture_min_gap_s", 2.5))
        with self._lock:
            if now - self._last_gesture < min_gap:
                return False
            self._last_gesture = now
        self._core.events.emit("avatar.gesture", {
            "gesture": gesture,
            "emotion": emotion if emotion in EMOTIONS else "",
            "intensity": max(0.05, min(1.0, float(intensity))),
            "reason": reason[:160],
        })
        return True

    # -- déplacements -----------------------------------------------------
    def move_to(self, place: str, *, reason: str = "", face: str = "") -> bool:
        """Déplacement réel vers un emplacement nommé de la scène."""
        if not self.enabled() or place not in PLACES:
            return False
        if not self.settings.get("locomotion", True):
            return False
        now = time.time()
        with self._lock:
            if now - self._last_move < 1.5:
                return False
            self._last_move = now
            self.place = place
        self._core.events.emit("avatar.move_requested", {
            "place": place, "face": face, "reason": reason[:160],
        })
        return True

    def look_at(self, place: str, *, reason: str = "") -> bool:
        if not self.enabled() or place not in PLACES:
            return False
        self._core.events.emit("avatar.look", {"place": place, "reason": reason[:160]})
        return True

    def set_view(self, view: str) -> bool:
        view = (view or "").upper()
        if view not in {"CALL", "FULL_BODY", "PORTRAIT", "HALF_BODY", "FOCUS", "BRAIN_VIEW"}:
            return False
        self.view = view
        self._core.events.emit("avatar.view", {"view": view})
        return True

    # -- réactions aux événements réels -----------------------------------
    def on_tool_started(self, tool_id: str, name: str = "") -> None:
        self.set_state(state_for_tool(tool_id), reason=name or tool_id)
        if tool_id.startswith(("memory.", "knowledge.")):
            # Consultation réelle de la mémoire : le corps peut s'orienter
            # vers le Brain Atlas, et s'en approcher si la scène le permet.
            self.look_at("brain", reason=tool_id)
            if self.settings.get("walk_to_brain", True):
                self.move_to("brain", reason="consultation mémoire")

    def on_tool_completed(self, tool_id: str, ok: bool) -> None:
        if ok:
            self.gesture("acknowledge", emotion="focused", intensity=0.3, reason=tool_id)
        else:
            self.gesture("concern", emotion="concerned", intensity=0.5, reason=tool_id)

    def on_turn_finished(self, *, ok: bool, tools_used: list[str], text: str) -> None:
        """Fin d'un échange : retour à la position de conversation."""
        if self.place != "home" and self.settings.get("return_home", True):
            self.move_to("home", reason="retour conversation")
        self.set_state("SPEAKING" if ok else "ERROR", reason="réponse")

    def plan_for_response(self, text: str, *, ok: bool, tools_used: list[str]
                          ) -> dict[str, Any]:
        """Traduit une réponse réelle en intention corporelle.

        Volontairement sobre : un geste par réponse au plus, choisi sur des
        indices factuels (échec, question posée, réussite d'un outil).
        """
        low = (text or "").casefold()
        if not ok:
            return {"gesture": "concern", "emotion": "concerned", "intensity": 0.55}
        if low.rstrip().endswith("?"):
            return {"gesture": "question", "emotion": "attentive", "intensity": 0.45}
        if tools_used and re.search(r"\b(fait|terminé|prêt|ok|réussi|déployé)\b", low):
            return {"gesture": "success", "emotion": "pleased", "intensity": 0.5}
        if len(text or "") > 220:
            return {"gesture": "explain", "emotion": "friendly", "intensity": 0.45}
        return {"gesture": "neutral", "emotion": "friendly", "intensity": 0.3}

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled(),
            "state": self.state,
            "place": self.place,
            "view": self.view,
            "places": list(PLACES),
            "gestures": list(GESTURES),
            "settings": self.settings,
            "model": "/assets/avatar/jarvis.glb",
        }
