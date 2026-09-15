"""Journal métier de la synchronisation Brainrot.

Le journal technique du runner trace déjà les appels d'outils. Il ne dit pas
QUOI a été appliqué, ni si la vérification a réussi. Ce module publie les
événements métier correspondants, avec un contrat de champs stable.

Deux règles :

* ``applied`` n'est émis qu'après une écriture réussie, ``verified`` qu'après
  relecture et comparaison réussies. Jamais par anticipation.
* Un échec d'écriture du journal n'est plus avalé : il remonte, pour que
  l'appelant puisse signaler ``WRITE_SUCCEEDED_AUDIT_FAILED`` au lieu de
  laisser croire à une traçabilité complète.
"""
from __future__ import annotations

import json
import time
from typing import Any

# Événements du cycle de vie. L'ordre reflète le déroulement réel.
PREPARED = "prepared"
CONFIRMED = "confirmed"
BACKUP_CREATED = "backup_created"
WRITE_STARTED = "write_started"
APPLIED = "applied"
VERIFIED_EVENT = "verified"
FAILED_EVENT = "failed"
ROLLED_BACK_EVENT = "rolled_back"
REFRESH_STARTED = "refresh_started"
REFRESH_COMPLETED = "refresh_completed"

# Champs publiés quand ils sont connus. Aucun secret n'y figure : ni mot de
# passe, ni clé, ni contenu de fichier — uniquement des identités et empreintes.
FIELDS = ("timestamp", "request_id", "sync_id", "plan_id", "confirmation_id",
          "selection_hash", "entry_identity", "action", "changed_fields",
          "before_hash", "after_hash", "verification_status", "reason",
          "backup_path", "idempotency_key", "applied_count", "selected_count")

SECRET_HINTS = ("password", "passphrase", "private_key", "secret", "token", "api_key")


class AuditWriteFailed(RuntimeError):
    """Le journal métier n'a pas pu être écrit."""


def _short(value: Any, limit: int = 120) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    return value


def scrub(payload: dict[str, Any]) -> dict[str, Any]:
    """Ne conserve que les champs du contrat, et jamais un secret."""
    clean: dict[str, Any] = {}
    for key in FIELDS:
        if key not in payload or payload[key] in (None, ""):
            continue
        if any(hint in key.lower() for hint in SECRET_HINTS):
            continue
        value = payload[key]
        if isinstance(value, (list, tuple)):
            value = [_short(str(v), 60) for v in value][:12]
        clean[key] = _short(value)
    return clean


class SyncAudit:
    """Écrit les événements métier. Chaque appel peut lever AuditWriteFailed."""

    def __init__(self, core: Any) -> None:
        self.core = core
        self.events: list[dict[str, Any]] = []

    def emit(self, event: str, payload: dict[str, Any], *, status: str = "ok") -> dict[str, Any]:
        record = scrub({**payload, "timestamp": payload.get("timestamp") or time.time()})
        self.events.append({"event": event, "status": status, **record})
        try:
            self.core.audit.record(
                action=f"brainrot_sync.{event}", tool="brainrot.sync", status=status,
                agent="jarvis",
                detail=json.dumps(record, ensure_ascii=False, default=str)[:900])
        except Exception as exc:  # pragma: no cover - dépend du stockage
            # Volontairement non avalé : une écriture réussie sans trace doit
            # être signalée, jamais masquée.
            raise AuditWriteFailed(f"{event}: {exc}") from exc
        return record
