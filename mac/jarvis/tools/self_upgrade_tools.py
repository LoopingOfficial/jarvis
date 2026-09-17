"""Outil d'auto-amélioration : déclenche SelfUpgradeService depuis la conversation."""
from __future__ import annotations

import re

from .base import ToolContext, ToolResult, registry
from ..permissions import SAFE_WRITE


def _detect_mode(instruction: str) -> str:
    low = (instruction or "").lower()
    if re.search(r"\b(?:planifie|prepare|pr[ée]pare|plan)\b", low) and not re.search(
            r"\b(?:et\s+(?:installe|lance|applique)|puis\s+(?:installe|lance|applique))\b", low):
        return "plan"
    if re.search(r"\b(?:cr[ée]e|lance|fais)\s+(?:le\s+)?(?:build|chantier)s?\b", low) \
            or (re.search(r"\b(?:sans\s+installer|ne\s+pas\s+installer|build\s+seul)\b", low)):
        return "build"
    if re.search(r"\b(?:auto|installe|mets\s+en\s+production|deploy|d[ée]ploie)\b", low) \
            or re.search(r"\bsi\s+(?:les\s+)?tests\s+(?:passent|succ[èe]dent)\b", low):
        return "auto"
    return "auto"


def _self_upgrade(ctx: ToolContext) -> ToolResult:
    self_core = ctx.core
    service = getattr(self_core, "self_upgrade", None)
    if service is None:
        return ToolResult(False, "SelfUpgradeService non disponible.")
    instruction = str(ctx.arguments.get("instruction") or ctx.arguments.get("prompt") or "")
    if not instruction.strip():
        return ToolResult(False, "instruction manquante.")
    mode = _detect_mode(instruction)
    if not self_core.settings.get("self_upgrade", "enabled", True):
        return ToolResult(False, "Self Upgrade désactivé dans les réglages.")
    result = service.run(instruction, mode=mode, conversation_id=ctx.conversation_id)
    if not result.get("ok"):
        return ToolResult(False, result.get("error", "échec de lancement."))
    return ToolResult(
        True,
        f"Upgrade lancé.\nID     : {result['upgrade_id']}\nMode   : {result['mode']}"
        f"\nSuivi  : ouvrir la page Self Upgrades ou /api/self-upgrade/upgrades/{result['upgrade_id']}",
        data=result, risk=SAFE_WRITE,
    )


registry.add(
    id="self.upgrade",
    name="Améliorer JARVIS lui-même",
    category="Système",
    description=("Lance une amélioration autonome de JARVIS : il inspecte son code, planifie, modifie "
                 "dans un workspace Git isolé, teste, vérifie la candidate, puis demande l'installation "
                 "au Supervisor si tout passe. instruction: l'objectif (ex. « Ajoute telle fonctionnalité », "
                 "« Corrige ce bug », « Construis Brainrot Sync et installe-le si les tests passent »)."),
    handler=_self_upgrade, risk=SAFE_WRITE, permissions=("write",),
    agents=("jarvis",),
    input_schema={"type": "object", "properties": {
        "instruction": {"type": "string", "description": "L'amélioration ou la correction demandée."}},
        "required": ["instruction"]},
)