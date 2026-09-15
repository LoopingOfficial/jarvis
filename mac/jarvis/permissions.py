"""Permission Manager : niveaux de risque et politique de confirmation."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

# Niveaux de risque, du plus bénin au plus dangereux.
READ_ONLY = "read_only"
SAFE_WRITE = "safe_write"
SENSITIVE = "sensitive"
DESTRUCTIVE = "destructive"

RISK_ORDER = {READ_ONLY: 0, SAFE_WRITE: 1, SENSITIVE: 2, DESTRUCTIVE: 3}
RISK_LABELS = {
    READ_ONLY: "lecture seule",
    SAFE_WRITE: "écriture sûre",
    SENSITIVE: "sensible",
    DESTRUCTIVE: "destructif",
}

# Permissions attribuables à un connecteur.
PERMISSIONS = ("read", "write", "execute", "admin", "destructive")

# Motifs de commandes shell/SSH considérés comme destructifs quel que soit l'outil.
DESTRUCTIVE_PATTERNS = [
    r"\b(del|erase|rmdir|rd|Remove-Item|Clear-Disk|Format-Volume|format)\b",
    r"\b(Stop-Computer|Restart-Computer)\b",
    r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*[rf]", r"\brm\s+-rf\b", r"\bmkfs\b", r"\bdd\s+if=",
    r"\bshutdown\b", r"\breboot\b", r"\bhalt\b", r":\(\)\{.*\};:",
    r"\bDROP\s+(DATABASE|TABLE|SCHEMA)\b", r"\bTRUNCATE\s+TABLE\b", r"\bDELETE\s+FROM\b(?!.*\bWHERE\b)",
    r"\bgit\s+push\s+.*--force\b", r"\bgit\s+reset\s+--hard\b", r"\bchmod\s+-R\s+777\b",
    r"\buserdel\b", r"\bkillall\s+-9\b", r"\bdocker\s+system\s+prune\b", r"\b>\s*/dev/sd[a-z]",
]

SENSITIVE_PATTERNS = [
    r"\b(taskkill|Stop-Process|Stop-Service|Restart-Service|Set-Service|Set-ExecutionPolicy|Move-Item|Rename-Item|icacls)\b",
    r"\bwinget\s+(install|uninstall|upgrade)\b",
    r"\bsystemctl\s+(restart|stop|disable)\b", r"\bservice\s+\w+\s+(restart|stop)\b",
    r"\bdocker\s+(stop|restart|rm)\b", r"\bkill\b", r"\bpkill\b",
    r"\bgit\s+push\b", r"\bnpm\s+publish\b", r"\bapt(-get)?\s+(install|remove|purge)\b",
    r"\bbrew\s+(install|uninstall)\b", r"\bpip\s+install\b", r"\bchown\b", r"\bchmod\b",
    r"\bmv\s+", r"\bALTER\s+TABLE\b", r"\bUPDATE\s+\w+\s+SET\b",
]


def classify_command(command: str) -> str:
    """Classe une commande shell/SQL par son niveau de risque réel."""
    if not command:
        return READ_ONLY
    text = command.strip()
    for pattern in DESTRUCTIVE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return DESTRUCTIVE
    for pattern in SENSITIVE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return SENSITIVE
    read_only_starts = (
        "ls", "cat", "head", "tail", "grep", "find", "df", "du", "free", "top", "ps", "uptime",
        "uname", "whoami", "pwd", "date", "hostname", "stat", "wc", "which", "echo", "env",
        "git status", "git log", "git diff", "git branch", "docker ps", "docker logs",
        "systemctl status", "curl -s", "netstat", "ss ", "ping", "nslookup", "dig", "select",
    )
    lowered = text.lower()
    if any(lowered.startswith(p) for p in read_only_starts):
        return READ_ONLY
    return SAFE_WRITE


def max_risk(*risks: str) -> str:
    best = READ_ONLY
    for r in risks:
        if RISK_ORDER.get(r, 0) > RISK_ORDER.get(best, 0):
            best = r
    return best


@dataclass
class PendingConfirmation:
    id: str
    tool: str
    action: str
    risk: str
    reason: str
    arguments: dict[str, Any]
    task_id: str = ""
    # Formulation orale courte (« Envoyer un e-mail à Pierre. Tu confirmes ? »).
    # `action` reste la description complète affichée à l'écran : on ne fait
    # pas lire une charge utile entière ni un chemin de fichier à voix haute.
    speech: str = ""
    created_at: float = field(default_factory=time.time)
    resolved: bool = False
    approved: bool = False


class PermissionManager:
    """Décide si une action doit être confirmée par l'utilisateur."""

    def __init__(self, settings, audit=None) -> None:
        self._settings = settings
        self._audit = audit
        self._pending: dict[str, PendingConfirmation] = {}

    # -- politique ---------------------------------------------------------
    def requires_confirmation(self, risk: str, tool_policy: str = "auto") -> bool:
        """tool_policy: always | never | auto (auto = selon le niveau de risque)."""
        if tool_policy == "always":
            return True
        if tool_policy == "never":
            return False
        sec = self._settings.section("security")
        if risk == DESTRUCTIVE:
            return bool(sec.get("confirm_destructive", True))
        if risk == SENSITIVE:
            return bool(sec.get("confirm_sensitive", True))
        return False

    def connector_allows(self, connector_permissions: list[str], risk: str) -> tuple[bool, str]:
        perms = set(connector_permissions or [])
        if "admin" in perms:
            perms |= {"read", "write", "execute"}
        if risk == READ_ONLY:
            ok = bool(perms & {"read", "write", "execute", "admin", "destructive"})
            return ok, "" if ok else "Ce connecteur n'a pas la permission de lecture."
        if risk == SAFE_WRITE:
            ok = bool(perms & {"write", "execute", "admin", "destructive"})
            return ok, "" if ok else "Ce connecteur n'a pas la permission d'écriture."
        if risk == SENSITIVE:
            ok = bool(perms & {"execute", "admin", "destructive"})
            return ok, "" if ok else "Ce connecteur n'a pas la permission d'exécution."
        ok = bool(perms & {"destructive", "admin"})
        return ok, "" if ok else "Ce connecteur n'autorise pas les actions destructives."

    # -- confirmations en attente ------------------------------------------
    def create_pending(
        self, *, tool: str, action: str, risk: str, reason: str,
        arguments: dict[str, Any], task_id: str = "", speech: str = "",
    ) -> PendingConfirmation:
        from .db import new_id

        pending = PendingConfirmation(
            id=new_id("cfm"), tool=tool, action=action, risk=risk,
            reason=reason, arguments=arguments, task_id=task_id,
            speech=speech,
        )
        self._pending[pending.id] = pending
        return pending

    def resolve(self, confirmation_id: str, approved: bool) -> PendingConfirmation | None:
        pending = self._pending.get(confirmation_id)
        if not pending or pending.resolved:
            return pending
        pending.resolved = True
        pending.approved = approved
        return pending

    def get(self, confirmation_id: str) -> PendingConfirmation | None:
        return self._pending.get(confirmation_id)

    def pending_list(self) -> list[dict[str, Any]]:
        timeout = float(self._settings.get("security", "confirmation_timeout_s", 600))
        now = time.time()
        out = []
        for p in list(self._pending.values()):
            if p.resolved or (now - p.created_at) > timeout:
                if p.resolved and (now - p.created_at) > timeout:
                    self._pending.pop(p.id, None)
                continue
            out.append({
                "id": p.id, "tool": p.tool, "action": p.action, "risk": p.risk,
                "risk_label": RISK_LABELS.get(p.risk, p.risk), "reason": p.reason,
                "task_id": p.task_id, "created_at": p.created_at,
            })
        return out

    def cleanup(self) -> None:
        timeout = float(self._settings.get("security", "confirmation_timeout_s", 600))
        now = time.time()
        for pid, p in list(self._pending.items()):
            if p.resolved or (now - p.created_at) > timeout:
                self._pending.pop(pid, None)
