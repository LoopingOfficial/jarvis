"""Score d'expertise — la fiabilité réelle de JARVIS avec un outil.

L'expertise N'EST PAS le nombre d'appels ni le nombre de sessions Idle Learning :
c'est un score 0-100 factuel, composé de preuves indépendantes.

Formule (pondérations documentées) :

    Documentation coverage      20 %   docs vérifiées, version connue, curriculum
    Validated knowledge         25 %   fiches liées, vérifiées, confiance, validations
    Successful real usage       25 %   volume ET taux de succès réels
    Error recovery knowledge    15 %   échecs résolus, causes documentées
    Recency / freshness         10 %   vieillissement des connaissances et docs
    Tested workflows             5 %   procédures testées / workflows validés

Le score brut est ensuite plafonné par la fiabilité observée : un outil très
utilisé mais criblé d'échecs ne peut jamais atteindre un haut score, et un
outil jamais utilisé est plafonné (pas de « 100 % uniquement grâce à la doc »).

    cap = 30 si aucun usage
    cap = 15 + success_rate * 80 si usage observé

Tous les composants sont retournés pour que l'UI puisse expliquer le score
d'un clic (explicabilité). Le calcul est pur : aucune dépendance au core,
uniquement des dictionnaires de faits -> testable.
"""
from __future__ import annotations

import time
from typing import Any
from .db import loads


def evidence(entry: dict) -> dict:
    value = entry.get("evidence") or {}
    return value if isinstance(value, dict) else loads(value, {})

# Pondérations (somme = 100).
WEIGHTS = {
    "documentation": 0.20,
    "knowledge": 0.25,
    "usage": 0.25,
    "error_recovery": 0.15,
    "freshness": 0.10,
    "workflows": 0.05,
}
KNOWN_KINDS = {
    "API_REFERENCE", "HOW_TO", "ERROR_FIX", "BEST_PRACTICE",
    "VERSION_CHANGE", "DEPRECATION", "WORKFLOW", "USER_ENVIRONMENT_SOLUTION",
    "documentation", "procedure", "note",
}
VERIFIED_METHODS = {"official_source", "verified", "tested", "user_environment", "manual"}


def _is_verified(entry: dict[str, Any]) -> bool:
    method = str(entry.get("verification_method") or "").strip()
    return entry.get("status", "active") == "active" and method in VERIFIED_METHODS


#: Alias public : une fiche Knowledge compte comme « réellement validée ».
is_verified = _is_verified


def _cap(total: int, success_rate: float) -> int:
    return 30 if total == 0 else min(100, round(15 + success_rate * 80))


def _component_documentation(usage: dict[str, Any]) -> float:
    score = 0.0
    if usage.get("docs_checked_at"):
        score += 35.0
    if usage.get("docs_version") not in (None, "", "latest", "stable", "unknown"):
        score += 20.0
    total = int(usage.get("coverage_total") or 0)
    mastered = int(usage.get("coverage_mastered") or 0)
    if total:
        score += 45.0 * min(1.0, mastered / total)
    return min(100.0, score)


def _component_knowledge(entries: list[dict[str, Any]]) -> float:
    active = [e for e in entries if e.get("status", "active") == "active"]
    if not active:
        return 0.0
    scores = []
    for entry in active:
        verified = _is_verified(entry)
        base = 0.75 if verified else 0.4
        confidence = min(1.0, max(0.0, float(entry.get("confidence_score", 0.5))))
        validations = min(3, int(entry.get("validation_count") or 0))
        validation_factor = 0.4 + 0.3 * validations
        # Une connaissance non vérifiée pèse nettement moins.
        scores.append(base * confidence * validation_factor)
    avg = sum(scores) / len(scores)
    count_factor = min(1.0, len(active) / 3.0)
    return min(100.0, 100.0 * avg * (0.35 + 0.65 * count_factor))


def _component_usage(usage: dict[str, Any]) -> float:
    total = int(usage.get("total_calls") or 0)
    if total == 0:
        return 0.0
    success = int(usage.get("success_calls") or 0)
    success_rate = success / total
    usage_share = min(1.0, total / 30.0)
    return min(100.0, 100.0 * success_rate * (0.25 + 0.75 * usage_share))


def _component_error_recovery(usage: dict[str, Any], entries: list[dict[str, Any]]) -> float:
    error_docs = [e for e in entries
                  if e.get("kind") in {"ERROR_FIX", "USER_ENVIRONMENT_SOLUTION"}
                  and _is_verified(e) and evidence(e).get("tests_passed", 0) > 0
                  and evidence(e).get("error_reason")]
    return min(100.0, sum(50 * float(e.get("confidence_score") or 0) for e in error_docs))


def _component_freshness(usage: dict[str, Any], entries: list[dict[str, Any]], now: float) -> float:
    candidates = [usage.get("docs_checked_at")]
    for entry in entries:
        if _is_verified(entry):
            candidates.append(entry.get("last_validated_at") or entry.get("updated_at"))
    candidates = [c for c in candidates if c]
    if not candidates:
        return 0.0
    scores = [100 * 0.5 ** (max(0, now - float(c)) / (180 * 86400)) for c in candidates]
    mismatch = (usage.get("tool_version") and usage.get("docs_version") != usage["tool_version"])
    return sum(scores) / len(scores) * (0.5 if mismatch else 1.0)


def _component_workflows(entries: list[dict[str, Any]]) -> float:
    workflows = [e for e in entries
                 if e.get("kind") in {"WORKFLOW", "BEST_PRACTICE", "USER_ENVIRONMENT_SOLUTION"}
                 and _is_verified(e) and evidence(e).get("tests_passed", 0) > 0]
    if not workflows:
        return 0.0
    validated = [w for w in workflows if float(w.get("confidence_score") or 0) >= 0.6]
    return min(100.0, (len(validated) / 2.0) * 100.0)


def compute_expertise(*, usage: dict[str, Any], knowledge: list[dict[str, Any]],
                      now: float | None = None) -> tuple[int, dict[str, Any]]:
    """Calcule l'expertise 0-100 et les composants explicables.

    `usage`    : ligne tool_usage (toutes clés acceptées, valeurs par défaut sûres).
    `knowledge`: fiches Knowledge liées au tool (clés kind, status,
                 verification_method, confidence_score, validation_count, …).
    Retourne   : (score_total, {composant: score, ponderation, total, cap}).
    """
    now = now if now is not None else time.time()
    components: dict[str, float] = {
        "documentation": _component_documentation(usage),
        "knowledge": _component_knowledge(knowledge),
        "usage": _component_usage(usage),
        "error_recovery": _component_error_recovery(usage, knowledge),
        "freshness": _component_freshness(usage, knowledge, now),
        "workflows": _component_workflows(knowledge),
    }
    total = int(usage.get("total_calls") or 0)
    success = int(usage.get("success_calls") or 0)
    success_rate = success / total if total else 0.0

    raw = sum(components[name] * WEIGHTS[name] for name in WEIGHTS)
    cap = _cap(total, success_rate)
    final = max(0, min(100, round(raw), cap))

    breakdown = {
        name: {
            "score": round(components[name], 1),
            "weight": WEIGHTS[name],
            "weighted": round(components[name] * WEIGHTS[name], 1),
        }
        for name in WEIGHTS
    }
    breakdown["_meta"] = {
        "formula": "doc*20% + knowledge*25% + usage*25% + error_recovery*15% + freshness*10% + workflows*5%",
        "raw": round(raw, 1),
        "cap": cap,
        "success_rate": round(success_rate, 3),
        "total_calls": total,
    }
    return final, breakdown
