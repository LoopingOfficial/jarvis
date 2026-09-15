"""Outils de présence : JARVIS peut décider de se déplacer ou de réagir.

Ces outils n'existent que pour les cas où le corps AJOUTE quelque chose :
aller consulter le Brain, revenir face à l'utilisateur, souligner une réponse.
Ils ne fabriquent jamais d'action fictive — ils ne font que demander une
mise en scène que le frontend exécute réellement.
"""
from __future__ import annotations

from ..avatar import GESTURES, PLACES
from ..permissions import READ_ONLY
from .base import ToolContext, ToolResult, registry


def _move(ctx: ToolContext) -> ToolResult:
    place = str(ctx.arguments.get("place") or "").strip().lower()
    if place not in PLACES:
        return ToolResult(False, f"Emplacement inconnu. Disponibles : {', '.join(PLACES)}.")
    reason = str(ctx.arguments.get("reason") or "")
    if not ctx.core.avatar.move_to(place, reason=reason):
        return ToolResult(False, "Déplacement impossible (locomotion désactivée ou trop rapproché).")
    return ToolResult(True, f"Je me place vers « {place} ».")


registry.add(
    id="avatar.move", name="Se déplacer dans la scène", category="Présence",
    description=(
        "Déplace physiquement JARVIS vers un emplacement de la scène "
        f"({', '.join(PLACES)}). À n'utiliser que si le déplacement a un sens "
        "visuel : aller consulter le Brain Atlas, revenir face à l'utilisateur. "
        "Jamais pour décorer une réponse."),
    handler=_move, risk=READ_ONLY, permissions=("read",), confirmation_policy="never",
    input_schema={
        "type": "object",
        "properties": {
            "place": {"type": "string", "enum": list(PLACES)},
            "reason": {"type": "string"},
        },
        "required": ["place"],
    },
)


def _gesture(ctx: ToolContext) -> ToolResult:
    gesture = str(ctx.arguments.get("gesture") or "").strip().lower()
    if gesture not in GESTURES:
        return ToolResult(False, f"Geste inconnu. Disponibles : {', '.join(GESTURES)}.")
    ok = ctx.core.avatar.gesture(
        gesture,
        emotion=str(ctx.arguments.get("emotion") or ""),
        intensity=float(ctx.arguments.get("intensity") or 0.5),
        reason=str(ctx.arguments.get("reason") or ""))
    return ToolResult(True, "Geste joué." if ok else "Geste ignoré (trop rapproché du précédent).")


registry.add(
    id="avatar.gesture", name="Accompagner d'un geste", category="Présence",
    description=(
        "Accompagne la réponse d'un geste corporel discret "
        f"({', '.join(GESTURES)}). Un geste par réponse au maximum."),
    handler=_gesture, risk=READ_ONLY, permissions=("read",), confirmation_policy="never",
    input_schema={
        "type": "object",
        "properties": {
            "gesture": {"type": "string", "enum": list(GESTURES)},
            "emotion": {"type": "string"},
            "intensity": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": ["gesture"],
    },
)


def _look(ctx: ToolContext) -> ToolResult:
    place = str(ctx.arguments.get("place") or "").strip().lower()
    if place not in PLACES:
        return ToolResult(False, f"Emplacement inconnu. Disponibles : {', '.join(PLACES)}.")
    ctx.core.avatar.look_at(place, reason=str(ctx.arguments.get("reason") or ""))
    return ToolResult(True, f"Je regarde vers « {place} ».")


registry.add(
    id="avatar.look", name="Diriger le regard", category="Présence",
    description="Oriente le regard de JARVIS vers une zone de l'interface.",
    handler=_look, risk=READ_ONLY, permissions=("read",), confirmation_policy="never",
    input_schema={
        "type": "object",
        "properties": {"place": {"type": "string", "enum": list(PLACES)},
                       "reason": {"type": "string"}},
        "required": ["place"],
    },
)
