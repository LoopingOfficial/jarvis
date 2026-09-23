"""VelkoRecoveryManager : une mission bloquée n'est jamais un cul-de-sac.

tool.failed → classify_failure() → CAUSE / OUTIL REQUIS / ÉTAT / SOLUTION +
actions proposées (configurer, réessayer, changer de méthode, annuler).
``resume_blocked_task`` relance la mission d'origine sans la retaper.
"""
from __future__ import annotations

import re
from typing import Any

CATEGORIES = ("AUTH_REQUIRED", "CONNECTOR_MISSING", "NETWORK_ERROR", "PERMISSION_DENIED",
              "USER_CONFIRMATION_REQUIRED", "TOOL_UNAVAILABLE", "INVALID_CONFIGURATION",
              "EXTERNAL_SERVICE_ERROR", "UNKNOWN")

# Connecteurs qu'une demande peut nécessiter : type → (libellé, motif, besoin).
_NEEDS: dict[str, tuple[str, str, str]] = {
    "n8n": ("n8n", r"\b(n8n|workflows?)\b", "URL n8n + clé API valide"),
    "ssh": ("Serveur SSH", r"\b(ssh|sftp|serveurs?)\b", "hôte, utilisateur et clé SSH"),
    "github": ("GitHub", r"\bgithub\b", "token GitHub (PAT)"),
    "google": ("Google", r"\b(google drive|google docs|gmail|agenda google)\b", "compte Google autorisé"),
    "email": ("Email", r"\b(e-?mails?|courriels?|bo[iî]te mail|imap|smtp)\b", "serveur mail + mot de passe"),
    "mysql": ("Base de données", r"\b(mysql|mariadb|base de donn[ée]es|sql)\b", "hôte, base et identifiants"),
    "discord": ("Discord", r"\bdiscord\b", "token du bot Discord"),
    "docker": ("Docker", r"\b(docker|conteneurs?)\b", "moteur Docker accessible"),
}

_STATE = {"AUTH_REQUIRED": "Authentification requise ou clé invalide",
          "CONNECTOR_MISSING": "Non configuré",
          "NETWORK_ERROR": "Injoignable (réseau)",
          "PERMISSION_DENIED": "Permission refusée par la politique",
          "USER_CONFIRMATION_REQUIRED": "Votre décision est attendue",
          "TOOL_UNAVAILABLE": "Outil indisponible pour cette action",
          "INVALID_CONFIGURATION": "Configuration invalide",
          "EXTERNAL_SERVICE_ERROR": "Le service distant a renvoyé une erreur",
          "UNKNOWN": "Cause non déterminée"}

_SOLUTION = {"AUTH_REQUIRED": "Renseignez une clé/identifiant valide dans Paramètres, testez, puis reprenez.",
             "CONNECTOR_MISSING": "Configurez le connecteur dans Paramètres, testez-le, puis reprenez la mission.",
             "NETWORK_ERROR": "Vérifiez que le service est en ligne et l'URL correcte, puis réessayez.",
             "PERMISSION_DENIED": "Accordez la permission au connecteur ou reformulez en lecture seule.",
             "USER_CONFIRMATION_REQUIRED": "Précisez votre choix pour que VELKO continue.",
             "TOOL_UNAVAILABLE": "Changez de méthode (autre outil ou action manuelle).",
             "INVALID_CONFIGURATION": "Corrigez la configuration dans Paramètres, puis réessayez.",
             "EXTERNAL_SERVICE_ERROR": "Réessayez dans un instant ; si l'erreur persiste, consultez le service.",
             "UNKNOWN": "Réessayez, reformulez la demande ou lancez une autre mission."}


def classify_failure(text: str, http_status: int = 0) -> str:
    t = str(text or "")
    code = http_status or int((re.search(r"HTTP (\d{3})", t) or [0, 0])[1] or 0)
    if code in (401, 403) or re.search(r"unauthori[sz]ed|forbidden|invalid api key|authentif|token", t, re.I):
        return "AUTH_REQUIRED"
    if re.search(r"aucun connecteur|connecteur (manquant|absent|introuvable)|non configur", t, re.I):
        return "CONNECTOR_MISSING"
    if re.search(r"permission|refus[ée] par la politique|not allowed|denied", t, re.I):
        return "PERMISSION_DENIED"
    if re.search(r"confirmation|validation requise|votre d[ée]cision", t, re.I):
        return "USER_CONFIRMATION_REQUIRED"
    if re.search(r"timed out|timeout|refused|unreachable|injoignable|getaddrinfo|nodename|urlopen", t, re.I):
        return "NETWORK_ERROR"
    if code >= 500:
        return "EXTERNAL_SERVICE_ERROR"
    if code == 404 or re.search(r"invalide|manquante?|introuvable", t, re.I):
        return "INVALID_CONFIGURATION"
    if re.search(r"outil (indisponible|inconnu)|not available|unsupported", t, re.I):
        return "TOOL_UNAVAILABLE"
    return "UNKNOWN"


class VelkoRecoveryManager:
    def __init__(self, core) -> None:
        self._core = core

    def build(self, category: str, *, connector: str = "", detail: str = "", http_status: int = 0,
              task_id: str = "", text: str = "") -> dict[str, Any]:
        category = category if category in CATEGORIES else classify_failure(detail, http_status)
        label, _, need = _NEEDS.get(connector, (connector or "", "", ""))
        detail = self._core.vault.scrub(str(detail or ""))[:300]
        cause = self._cause(category, label, detail)
        actions = [{"id": "retry", "label": "Réessayer"}]
        if connector and category in ("AUTH_REQUIRED", "CONNECTOR_MISSING", "INVALID_CONFIGURATION",
                                      "NETWORK_ERROR", "PERMISSION_DENIED"):
            actions.insert(0, {"id": "configure", "label": f"Configurer {label}", "connector": connector})
        if category in ("TOOL_UNAVAILABLE", "EXTERNAL_SERVICE_ERROR", "UNKNOWN"):
            actions.append({"id": "change_method", "label": "Changer de méthode"})
        actions.append({"id": "cancel", "label": "Annuler la mission"})
        message = f"ACTION BLOQUÉE : {cause}"
        if label:
            message += f"\nConnecteur : {label} — {_STATE[category]}."
        if need and category in ("AUTH_REQUIRED", "CONNECTOR_MISSING", "INVALID_CONFIGURATION"):
            message += f"\nVELKO a besoin de : {need}."
        return {"category": category, "cause": cause, "connector": connector, "connector_label": label,
                "state": _STATE[category], "needs": need, "solution": _SOLUTION[category],
                "detail": detail, "http_status": http_status, "task_id": task_id,
                "actions": actions, "resumable": bool(task_id), "message": message}

    @staticmethod
    def _cause(category: str, label: str, detail: str) -> str:
        who = label or "l'outil"
        return {"AUTH_REQUIRED": f"{who} refuse l'authentification" + (f" ({detail[:80]})" if detail else "") + ".",
                "CONNECTOR_MISSING": f"Aucun connecteur {who} n'est configuré.",
                "NETWORK_ERROR": f"{who} est injoignable.",
                "PERMISSION_DENIED": f"La politique de sécurité refuse cette action sur {who}.",
                "USER_CONFIRMATION_REQUIRED": detail or "Une précision de votre part est nécessaire.",
                "TOOL_UNAVAILABLE": detail or f"Aucun outil disponible ne sait faire cette action avec {who}.",
                "INVALID_CONFIGURATION": detail or f"La configuration de {who} est invalide.",
                "EXTERNAL_SERVICE_ERROR": f"{who} a renvoyé une erreur" + (f" : {detail[:120]}" if detail else "."),
                }.get(category, detail or "Aucun outil n'a pu aboutir et la cause exacte n'a pas été remontée.")

    # -- missions bloquées génériques (chemin LLM) --------------------------
    def for_result(self, text: str, result: dict[str, Any]) -> dict[str, Any]:
        """Enrichit un résultat bloqué du moteur avec une cause lisible."""
        core = self._core
        tid = str(result.get("task_id") or "")
        for ctype, (_, pattern, _) in _NEEDS.items():
            if re.search(pattern, text or "", re.I) and not core.connectors.by_type(ctype):
                return self.build("CONNECTOR_MISSING", connector=ctype, task_id=tid, text=text)
        errors = []
        if tid:
            errors = [r["message"] for r in core.db.query(
                "SELECT message FROM task_logs WHERE task_id=? AND level='error' ORDER BY id DESC LIMIT 5", (tid,))
                if "ACTION BLOQUÉE" not in str(r["message"])]
        detail = errors[0] if errors else str(result.get("response") or "")
        detail = re.sub(r"^ACTION BLOQUÉE\s*:\s*", "", detail)
        category = classify_failure(detail)
        connector = next((c for c, (_, p, _) in _NEEDS.items() if re.search(p, text or "", re.I)), "")
        return self.build(category, connector=connector, detail=detail, task_id=tid, text=text)

    # -- reprise --------------------------------------------------------------
    def resume_blocked_task(self, task_id: str) -> dict[str, Any]:
        core = self._core
        task = core.tasks.get(task_id)
        if not task:
            return {"ok": False, "response": "Mission introuvable."}
        if task.get("status") not in ("blocked", "failed", "waiting_user", "cancelled"):
            return {"ok": False, "response": "Cette mission n'est pas en attente de reprise."}
        core.tasks.log(task_id, "Reprise de la mission demandée par l'utilisateur", level="info")
        core.tasks.set_status(task_id, "cancelled", error="Reprise dans une nouvelle exécution")
        result = core.orchestrator.handle(task["name"], conversation_id=task.get("conversation_id") or "",
                                          source="resume")
        result["resumed_from"] = task_id
        return result
