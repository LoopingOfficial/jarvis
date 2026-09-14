"""Synchronisation Sheet → Brainrot-Fortnite : plan, garde-fous, écriture vérifiée.

Chemin d'écriture retenu — après inspection des mécanismes réels du site :

* ``ajax/brainrots-create.php`` / ``brainrots-update.php`` existent mais visent
  la table SQL ``brainrots`` (131 lignes, jeu de données distinct) et exigent
  une session admin plus un jeton CSRF. Les employer appliquerait le diff à un
  magasin qui n'est pas celui comparé.
* Le magasin réellement comparé est ``data/wiki/all_brainrots_sheet_data.php``,
  un tableau PHP statique chargé par ``wiki_all_brainrots_sheet_data()``.

Le build applique donc un **patch contrôlé du magasin** : régénération de ce
fichier précis, par un sérialiseur au schéma figé (``name``, ``rarity``,
``income``, ``cost``). Aucun shell arbitraire, aucun outil d'écriture libre
exposé au modèle : la seule écriture possible est celle décrite par un plan
validé puis confirmée explicitement par l'utilisateur.
"""
from __future__ import annotations

import hashlib
import json
import uuid
import re
import time
from pathlib import Path
from typing import Any

from .sync_audit import (APPLIED, AuditWriteFailed, BACKUP_CREATED, CONFIRMED,
                         FAILED_EVENT, PREPARED, REFRESH_COMPLETED, REFRESH_STARTED,
                         ROLLED_BACK_EVENT, SyncAudit, VERIFIED_EVENT, WRITE_STARTED)
from .tools.runner import ConfirmationRequired
from .brainrot_compare import (CONFLICT, CREATE, FIELD_MAPPING, INVALID, NO_CHANGE,
                               SERVER_ONLY, SITE_DATA_PATH, UPDATE, normalize_number,
                               normalize_text, parse_site_php, slugify)

BUILD_ID = "JARVIS_BRAINROT_SYNC_V2"

# Les parcours CREATE et UPDATE ayant ete valides en production, le lot est
# ouvert. La securite ne vient plus d'une limite a une entree mais de la
# confirmation scopee, de la verification apres CHAQUE ecriture et de l'arret
# immediat au premier echec.
BATCH_ENABLED_DEFAULT = True

READY, READY_WITHOUT_IMAGE, NEEDS_REVIEW, BLOCKED = (
    "READY", "READY_WITHOUT_IMAGE", "NEEDS_REVIEW", "BLOCKED")
VERIFIED, FAILED, ROLLED_BACK = "VERIFIED", "FAILED", "ROLLED_BACK"
PENDING, APPLYING, SKIPPED = "PENDING", "APPLYING", "SKIPPED"
STALE_COMPARISON = "STALE_COMPARISON"
CONFIRMATION_SCOPE_MISMATCH = "CONFIRMATION_SCOPE_MISMATCH"
CONFIRMATION_EXPIRED = "CONFIRMATION_EXPIRED"
CONFIRMATION_ALREADY_USED = "CONFIRMATION_ALREADY_USED"
WRITE_SUCCEEDED_AUDIT_FAILED = "WRITE_SUCCEEDED_AUDIT_FAILED"
STALE_SELECTION = "STALE_SELECTION"

# Duree de vie d'une confirmation : assez pour lire la modale, trop court
# pour etre rejouee plus tard.
CONFIRMATION_TTL_SECONDS = 300

# Le magasin comparé ne porte aucun champ d'image : une image manquante ne peut
# donc pas bloquer une écriture ici. Le drapeau reste publié pour l'interface.
STORE_REQUIRES_IMAGE = False

APPLICABLE = {CREATE, UPDATE}
NEVER_APPLICABLE = {CONFLICT, INVALID, SERVER_ONLY, NO_CHANGE}

# Octets exacts relevés sur le fichier réel : BOM, "\r\r\n" après l'ouverture
# et avant la fermeture, "\r\n" sur chaque ligne. Les reproduire évite un diff
# parasite sur les 272 lignes inchangées.
STORE_PREFIX = "﻿<?php\r\r\nreturn [\r\n"
STORE_SUFFIX = "];\r\r\n"
STORE_ROW = "    ['name'=>'{name}','rarity'=>'{rarity}','income'=>{income},'cost'=>{cost}],\r\n"


# --- Valeurs ----------------------------------------------------------------
def _php_string(value: Any) -> str:
    """Échappe pour une chaîne PHP à quotes simples. Rien d'autre n'est permis."""
    text = "" if value is None else str(value)
    return text.replace("\\", "\\\\").replace("'", "\\'")


def _php_number(value: Any) -> str:
    """Rend un entier quand la valeur l'est, sinon un flottant. Jamais de texte."""
    number = normalize_number(value)
    if number is None:
        raise ValueError(f"valeur non numérique : {value!r}")
    return str(int(number)) if float(number).is_integer() else repr(float(number))


def _identity_text(value: Any) -> str:
    if isinstance(value, float) and not isinstance(value, bool) and value.is_integer():
        return str(int(value))
    return "" if value is None else str(value).strip()


def serialize_store(records: list[dict[str, Any]]) -> str:
    """Régénère le fichier du magasin. Schéma figé, aucune clé supplémentaire."""
    rows = []
    for record in records:
        raw = record["raw"] if "raw" in record else record
        rows.append(STORE_ROW.format(
            name=_php_string(raw.get("name")), rarity=_php_string(raw.get("rarity")),
            income=_php_number(raw.get("income")), cost=_php_number(raw.get("cost"))))
    return STORE_PREFIX + "".join(rows) + STORE_SUFFIX


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- Plan -------------------------------------------------------------------
def _proposed_values(entry: dict[str, Any]) -> dict[str, Any]:
    """Valeurs cibles côté site.

    UPDATE ne touche QUE les champs réellement modifiés ; une cellule Sheet
    vide n'écrase jamais une valeur du site.
    """
    current = dict(entry.get("site_values") or {})
    sheet = entry.get("sheet_values") or {}
    if entry["status"] == CREATE:
        return {site_key: sheet.get(label) for label, site_key in FIELD_MAPPING.items()}
    proposed = dict(current)
    for change in entry.get("changed_fields") or []:
        proposed[change["site_field"]] = change["sheet"]
    return proposed


def _readiness(entry: dict[str, Any], proposed: dict[str, Any],
               image_url: str) -> tuple[str, str]:
    """Statut d'applicabilité et raison. Un doute donne NEEDS_REVIEW, jamais READY."""
    if entry["status"] in NEVER_APPLICABLE:
        return BLOCKED, f"Statut {entry['status']} : jamais appliqué automatiquement."
    if not _identity_text(entry.get("identity")):
        return BLOCKED, "Identité vide."
    for key in ("name", "rarity", "income", "cost"):
        value = proposed.get(key)
        if key in ("income", "cost"):
            if value is None or str(value).strip() == "":
                return NEEDS_REVIEW, f"Champ « {key} » absent : rien à écrire sans valeur."
            if normalize_number(value) is None:
                return BLOCKED, f"Champ « {key} » non numérique : {value!r}."
        elif value is None or str(value).strip() == "":
            return NEEDS_REVIEW, f"Champ « {key} » vide côté Sheet."
    if entry["status"] == CREATE and not image_url:
        if STORE_REQUIRES_IMAGE:
            return NEEDS_REVIEW, "Le magasin exige une image et aucune officielle n'existe."
        return READY_WITHOUT_IMAGE, ("Aucune image officielle pour ce Brainrot ; "
                                     "le magasin comparé n'en porte pas, l'écriture reste valide.")
    return READY, "Prêt à appliquer."


def prepare_plan(comparison: dict[str, Any], *, sync_id: str = "",
                 source_hash: str = "") -> dict[str, Any]:
    """Plan déterministe dérivé de la comparaison. Ne lit ni n'écrit le serveur."""
    if not comparison.get("ok"):
        return {"ok": False, "error": comparison.get("error", "COMPARISON_UNAVAILABLE"),
                "entries": []}
    entries = []
    for entry in comparison.get("entries", []):
        if entry["status"] in NEVER_APPLICABLE and entry["status"] != NO_CHANGE:
            pass  # conflits/invalides/serveur-seul : publiés mais bloqués
        elif entry["status"] == NO_CHANGE:
            continue
        proposed = _proposed_values(entry)
        image_url = str(entry.get("image_url") or "")
        readiness, reason = _readiness(entry, proposed, image_url)
        entries.append({
            "identity": _identity_text(entry.get("identity")),
            "slug": entry.get("slug", ""),
            "action": entry["status"] if entry["status"] in APPLICABLE else entry["status"],
            "sheet_values": dict(entry.get("sheet_values") or {}),
            "current_site_values": dict(entry.get("site_values") or {}),
            "proposed_values": proposed,
            "changed_fields": list(entry.get("changed_fields") or []),
            "missing_fields": list(entry.get("missing_fields") or []),
            "sheet_evidence": entry.get("evidence_sheet", ""),
            "site_evidence": entry.get("evidence_site", ""),
            "image_status": ("OFFICIAL" if image_url else "NONE"),
            "image_url": image_url,
            "readiness_status": readiness,
            "readiness_reason": reason,
            "site_index": entry.get("site_index"),
            "sheet_row": entry.get("sheet_row"),
        })
    counts = {
        "creates": sum(1 for e in entries if e["action"] == CREATE),
        "updates": sum(1 for e in entries if e["action"] == UPDATE),
        "without_image": sum(1 for e in entries if e["readiness_status"] == READY_WITHOUT_IMAGE),
        "needs_review": sum(1 for e in entries if e["readiness_status"] == NEEDS_REVIEW),
        "blocked": sum(1 for e in entries if e["readiness_status"] == BLOCKED),
        "applicable": sum(1 for e in entries
                          if e["readiness_status"] in (READY, READY_WITHOUT_IMAGE)),
        "deletes": 0,
    }
    plan = {"ok": True, "build": BUILD_ID,
            "sync_id": sync_id or f"sync_{int(time.time())}",
            "store_path": SITE_DATA_PATH,
            "source_hash": source_hash,
            "entries": entries, "counts": counts,
            "batch_enabled": BATCH_ENABLED_DEFAULT,
            "requires_confirmation": True,
            "applied": False}
    plan["plan_hash"] = content_hash(json.dumps(
        [(e["identity"], e["action"], e["proposed_values"]) for e in entries],
        ensure_ascii=False, sort_keys=True, default=str))
    return plan


def validate_plan(plan: dict[str, Any], selection: list[str] | None = None) -> dict[str, Any]:
    """Refuse tout ce qui n'est pas explicitement applicable."""
    if not plan.get("ok"):
        return {"ok": False, "error": plan.get("error", "PLAN_UNAVAILABLE"), "selected": []}
    wanted = set(selection) if selection is not None else None
    selected, refused = [], []
    for entry in plan["entries"]:
        key = entry["identity"]
        if wanted is not None and key not in wanted:
            continue
        if entry["action"] not in APPLICABLE:
            refused.append({"identity": key, "reason": f"action {entry['action']} non applicable"})
        elif entry["readiness_status"] not in (READY, READY_WITHOUT_IMAGE):
            refused.append({"identity": key, "reason": entry["readiness_reason"]})
        else:
            selected.append(entry)
    if wanted is not None:
        known = {e["identity"] for e in plan["entries"]}
        refused += [{"identity": k, "reason": "absent du plan"} for k in sorted(wanted - known)]
    return {"ok": True, "selected": selected, "refused": refused,
            "deletes": 0, "plan_hash": plan["plan_hash"]}



def selection_hash(plan: dict[str, Any], selection: list[str]) -> str:
    """Empreinte deterministe de la selection EXACTE qui sera appliquee.

    Elle couvre l'identite, l'action, les valeurs proposees et les champs
    modifies : si l'un d'eux bouge apres la confirmation, l'empreinte change et
    l'application est refusee.
    """
    wanted = set(selection)
    payload = []
    for entry in plan.get("entries", []):
        if entry["identity"] not in wanted:
            continue
        payload.append({
            "identity": entry["identity"], "action": entry["action"],
            "proposed_values": {k: str(v) for k, v in sorted(entry["proposed_values"].items())},
            "changed_fields": sorted(
                f"{c['site_field']}={c['sheet']}" for c in entry.get("changed_fields") or []),
        })
    payload.sort(key=lambda item: item["identity"])
    return content_hash(json.dumps(payload, ensure_ascii=False, sort_keys=True))


class ConfirmationScope:
    """Accord utilisateur borne a une selection precise, a usage unique."""

    def __init__(self, plan: dict[str, Any], selection: list[str], *,
                 expected_sha256: str, ttl: int = CONFIRMATION_TTL_SECONDS) -> None:
        wanted = set(selection)
        entries = [e for e in plan.get("entries", []) if e["identity"] in wanted]
        self.id = f"scope_{uuid.uuid4().hex[:12]}"
        self.plan_id = plan.get("plan_hash", "")
        self.expected_sha256 = expected_sha256
        self.selected_entry_ids = sorted(e["identity"] for e in entries)
        self.actions = {e["identity"]: e["action"] for e in entries}
        self.changed_fields = {e["identity"]: [c["site_field"] for c in e.get("changed_fields") or []]
                               for e in entries}
        self.proposed_values = {e["identity"]: dict(e["proposed_values"]) for e in entries}
        self.selection_hash = selection_hash(plan, selection)
        self.created_at = time.time()
        self.expires_at = self.created_at + ttl
        self.used = False

    def as_dict(self) -> dict[str, Any]:
        return {"confirmation_id": self.id, "plan_id": self.plan_id,
                "expected_sha256": self.expected_sha256,
                "selected_entry_ids": list(self.selected_entry_ids),
                "actions": dict(self.actions), "changed_fields": dict(self.changed_fields),
                "proposed_values": dict(self.proposed_values),
                "selection_hash": self.selection_hash,
                "expires_at": self.expires_at, "used": self.used}

    def validate(self, plan: dict[str, Any], selection: list[str],
                 store_hash: str) -> str:
        """Retourne un code d'erreur, ou une chaine vide si la portee tient."""
        if self.used:
            return CONFIRMATION_ALREADY_USED
        if time.time() > self.expires_at:
            return CONFIRMATION_EXPIRED
        if self.plan_id != plan.get("plan_hash", ""):
            return CONFIRMATION_SCOPE_MISMATCH
        if sorted(set(selection)) != self.selected_entry_ids:
            return CONFIRMATION_SCOPE_MISMATCH
        if selection_hash(plan, selection) != self.selection_hash:
            return CONFIRMATION_SCOPE_MISMATCH
        if self.expected_sha256 and store_hash and self.expected_sha256 != store_hash:
            return STALE_COMPARISON
        return ""


# --- Application ------------------------------------------------------------
class StoreWriter:
    """Accès au magasin : lecture, sauvegarde locale, écriture, relecture.

    Toutes les écritures passent par cette classe et par elle seule. Elle n'a
    qu'une opération d'écriture, sur un seul chemin, avec un contenu produit
    par ``serialize_store``.
    """

    def __init__(self, core: Any, *, connector_id: str = "ssh",
                 path: str = SITE_DATA_PATH, backup_dir: str | Path = "data/backups/brainrot_sync"):
        self.core = core
        self.connector_id = connector_id
        self.path = path
        self.backup_dir = Path(backup_dir)

    def read(self) -> dict[str, Any]:
        result = self.core.runner.run(
            "ssh.read_file", {"connector_id": self.connector_id, "path": self.path},
            agent="jarvis", execution_policy={"read_only": True})
        if not result.ok:
            return {"ok": False, "error": "STORE_READ_FAILED", "detail": result.output[:300]}
        content = result.output
        return {"ok": True, "content": content, "hash": content_hash(content),
                "records": parse_site_php(content)}

    def backup(self, content: str, sync_id: str) -> dict[str, Any]:
        """Copie horodatée conservée côté JARVIS : c'est la source du rollback."""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = self.backup_dir / f"{stamp}-{sync_id}-all_brainrots_sheet_data.php"
        # newline="" : les fins de ligne du magasin sont conservees telles quelles,
        # sinon la sauvegarde ne restaure pas les octets d'origine.
        with target.open("w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        return {"ok": True, "path": str(target), "hash": content_hash(content),
                "bytes": len(content.encode("utf-8"))}

    def write(self, content: str, *, expected_sha256: str = "",
              confirmation_id: str = "") -> dict[str, Any]:
        """Écrit le magasin.

        ``expected_sha256`` fait revalider par le runner que le fichier distant
        est toujours celui qu'on croit : une modification concurrente annule
        l'écriture au lieu de l'écraser.

        L'écriture reste soumise à la confirmation utilisateur du runner. Cette
        demande n'est jamais approuvée ici : elle est REMONTÉE à l'appelant, qui
        la présente à l'utilisateur. Une fois approuvée, l'identifiant revient
        par ``confirmation_id`` et la même écriture est rejouée à l'identique.
        """
        arguments = {"connector_id": self.connector_id, "path": self.path,
                     "content": content, "mode": "write"}
        if expected_sha256:
            arguments["expected_sha256"] = expected_sha256
        try:
            result = self.core.runner.run("ssh.write_file", dict(arguments), agent="jarvis",
                                          confirmation_id=confirmation_id)
        except ConfirmationRequired as required:
            pending = required.pending
            return {"ok": False, "error": "CONFIRMATION_REQUIRED",
                    "confirmation": {"id": pending.id, "action": pending.action,
                                     "reason": pending.reason, "risk": pending.risk},
                    "detail": str(required)[:300]}
        if not result.ok:
            return {"ok": False, "error": "STORE_WRITE_FAILED", "detail": result.output[:300]}
        return {"ok": True, "detail": result.output[:200]}


def apply_entry(records: list[dict[str, Any]], entry: dict[str, Any]) -> dict[str, Any]:
    """Applique UNE entrée à la liste en mémoire. Aucune suppression possible."""
    proposed = entry["proposed_values"]
    identity = normalize_text(entry["identity"])
    slug = slugify(entry["identity"])
    updated = [dict(r, raw=dict(r["raw"])) for r in records]
    if entry["action"] == CREATE:
        if any(normalize_text(r["raw"].get("name")) == identity or r.get("slug") == slug
               for r in updated):
            return {"ok": False, "error": "ALREADY_PRESENT"}
        updated.append({"index": len(updated) + 1, "line": 0,
                        "raw": {"name": _identity_text(proposed.get("name")),
                                "rarity": proposed.get("rarity"),
                                "income": normalize_number(proposed.get("income")),
                                "cost": normalize_number(proposed.get("cost"))},
                        "identity": identity, "slug": slug})
        return {"ok": True, "records": updated, "action": CREATE}
    targets = [r for r in updated
               if normalize_text(r["raw"].get("name")) == identity or r.get("slug") == slug]
    if len(targets) != 1:
        return {"ok": False, "error": "TARGET_NOT_UNIQUE", "found": len(targets)}
    target = targets[0]
    for change in entry["changed_fields"]:
        field = change["site_field"]
        value = change["sheet"]
        # Une cellule Sheet vide n'efface jamais : le garde-fou est ici aussi,
        # pas seulement dans le diff.
        if value is None or str(value).strip() == "":
            continue
        target["raw"][field] = (normalize_number(value) if field in ("income", "cost")
                                else _identity_text(value))
    return {"ok": True, "records": updated, "action": UPDATE}


def verify_entry(records: list[dict[str, Any]], entry: dict[str, Any],
                 before: list[dict[str, Any]]) -> dict[str, Any]:
    """Relit le résultat : l'entrée visée est conforme, et rien d'autre n'a bougé."""
    identity = normalize_text(entry["identity"])
    slug = slugify(entry["identity"])
    found = [r for r in records
             if normalize_text(r["raw"].get("name")) == identity or r.get("slug") == slug]
    if len(found) != 1:
        return {"ok": False, "status": FAILED,
                "reason": f"{len(found)} entrée(s) portant cette identité après écriture."}
    raw = found[0]["raw"]
    mismatches = []
    for field, expected in entry["proposed_values"].items():
        actual = raw.get(field)
        if field in ("income", "cost"):
            if normalize_number(expected) != normalize_number(actual):
                mismatches.append((field, expected, actual))
        elif normalize_text(expected) != normalize_text(actual):
            mismatches.append((field, expected, actual))
    if mismatches:
        return {"ok": False, "status": FAILED, "reason": "champs non conformes",
                "mismatches": mismatches}
    # Aucune autre entrée ne doit avoir changé.
    def snapshot(rows):
        return {normalize_text(r["raw"].get("name")): (
            normalize_text(r["raw"].get("rarity")),
            normalize_number(r["raw"].get("income")),
            normalize_number(r["raw"].get("cost"))) for r in rows}
    before_map, after_map = snapshot(before), snapshot(records)
    touched = {k for k in set(before_map) | set(after_map)
               if before_map.get(k) != after_map.get(k)}
    unexpected = touched - {identity}
    if unexpected:
        return {"ok": False, "status": FAILED, "reason": "entrées collatérales modifiées",
                "unexpected": sorted(unexpected)[:5]}
    if entry["action"] == CREATE and len(records) != len(before) + 1:
        return {"ok": False, "status": FAILED, "reason": "le nombre d'entrées n'a pas augmenté de 1"}
    if entry["action"] == UPDATE and len(records) != len(before):
        return {"ok": False, "status": FAILED, "reason": "le nombre d'entrées a changé"}
    return {"ok": True, "status": VERIFIED}


class SyncService:
    """Orchestration d'une application : portée, sauvegarde, batch, vérification.

    Le lot est autorisé, mais chaque entrée reste une transaction à part :
    écriture, relecture, vérification. Au premier échec, l'exécution s'arrête,
    la sauvegarde est restaurée et les entrées restantes sont marquées SKIPPED.
    """

    #: Résultats déjà produits, indexés par clé d'idempotence (rejeu réseau,
    #: double clic, double POST : la même clé renvoie le même résultat).
    _results: dict[str, dict[str, Any]] = {}
    #: Portées de confirmation vivantes, à usage unique.
    _scopes: dict[str, Any] = {}

    def __init__(self, core: Any, writer: StoreWriter | None = None) -> None:
        self.core = core
        self.writer = writer or StoreWriter(core)
        self.audit = SyncAudit(core)

    # --- confirmation -------------------------------------------------------
    def open_confirmation(self, plan: dict[str, Any], selection: list[str], *,
                          request_id: str = "") -> dict[str, Any]:
        """Crée la portée liée à la sélection exacte présentée à l'utilisateur."""
        validated = validate_plan(plan, selection)
        if not validated["selected"]:
            return {"ok": False, "error": "NOTHING_APPLICABLE",
                    "refused": validated["refused"]}
        current = self.writer.read()
        if not current.get("ok"):
            return {"ok": False, "error": current.get("error")}
        scope = ConfirmationScope(plan, [e["identity"] for e in validated["selected"]],
                                  expected_sha256=current["hash"])
        SyncService._scopes[scope.id] = scope
        self._safe_audit(CONFIRMED, {
            "request_id": request_id, "sync_id": plan.get("sync_id"),
            "plan_id": plan.get("plan_hash"), "confirmation_id": scope.id,
            "selection_hash": scope.selection_hash,
            "selected_count": len(scope.selected_entry_ids),
            "before_hash": current["hash"]})
        return {"ok": True, "scope": scope.as_dict(),
                "store_hash": current["hash"], "refused": validated["refused"]}

    def _safe_audit(self, event: str, payload: dict[str, Any], *, status: str = "ok") -> bool:
        """Émet un événement ; retourne False si le journal a échoué."""
        try:
            self.audit.emit(event, payload, status=status)
            return True
        except AuditWriteFailed:
            return False

    # --- application --------------------------------------------------------
    def apply(self, plan: dict[str, Any], selection: list[str], *,
              confirmation: dict[str, Any], request_id: str = "",
              confirmation_id: str = "", scope_id: str = "",
              idempotency_key: str = "") -> dict[str, Any]:
        """Applique la sélection, entrée par entrée, avec vérification à chaque pas."""
        sync_id = plan.get("sync_id", "")
        plan_id = plan.get("plan_hash", "")
        base: dict[str, Any] = {
            "sync_id": sync_id, "plan_id": plan_id, "request_id": request_id,
            "store_path": self.writer.path, "results": [], "applied": 0,
            "batch_enabled": bool(plan.get("batch_enabled")),
            "idempotency_key": idempotency_key, "audit_complete": True,
        }

        # Rejeu : la même clé ne réécrit jamais, elle renvoie le résultat connu.
        if idempotency_key and idempotency_key in SyncService._results:
            return {**SyncService._results[idempotency_key], "replayed": True}

        if not (confirmation or {}).get("approved"):
            return {**base, "ok": False, "error": "CONFIRMATION_REQUIRED_USER"}
        if (confirmation or {}).get("plan_hash") != plan_id:
            return {**base, "ok": False, "error": "CONFIRMATION_PLAN_MISMATCH"}

        validated = validate_plan(plan, selection)
        selected = validated["selected"]
        base["refused"] = validated["refused"]
        if not selected:
            return {**base, "ok": False, "error": "NOTHING_APPLICABLE"}

        current = self.writer.read()
        if not current.get("ok"):
            return {**base, "ok": False, "error": current.get("error")}
        base["site_total_before"] = len(current["records"])
        base["site_total_after"] = len(current["records"])
        base["before_hash"] = current["hash"]

        # Portée de confirmation : elle borne l'écriture à CETTE sélection.
        scope = SyncService._scopes.get(scope_id) if scope_id else None
        if scope_id and not scope:
            return {**base, "ok": False, "error": CONFIRMATION_SCOPE_MISMATCH,
                    "detail": "Portée de confirmation inconnue ou expirée."}
        if scope:
            problem = scope.validate(plan, [e["identity"] for e in selected], current["hash"])
            if problem:
                self._safe_audit(FAILED_EVENT, {
                    "request_id": request_id, "sync_id": sync_id, "plan_id": plan_id,
                    "confirmation_id": scope.id, "reason": problem}, status="denied")
                return {**base, "ok": False, "error": problem}
            base["selection_hash"] = scope.selection_hash
            base["confirmation_id"] = scope.id
        elif plan.get("source_hash") and current["hash"] != plan["source_hash"]:
            self._safe_audit(FAILED_EVENT, {
                "request_id": request_id, "sync_id": sync_id, "plan_id": plan_id,
                "reason": STALE_COMPARISON, "before_hash": current["hash"]}, status="denied")
            return {**base, "ok": False, "error": STALE_COMPARISON,
                    "detail": "Le magasin a changé depuis la comparaison."}

        backup = self.writer.backup(current["content"], sync_id)
        base["backup_path"] = backup["path"]
        ok_audit = self._safe_audit(BACKUP_CREATED, {
            "request_id": request_id, "sync_id": sync_id, "plan_id": plan_id,
            "confirmation_id": scope.id if scope else confirmation_id,
            "selection_hash": base.get("selection_hash"),
            "backup_path": backup["path"], "before_hash": backup["hash"],
            "selected_count": len(selected)})
        base["audit_complete"] = base["audit_complete"] and ok_audit

        records = current["records"]
        before_content = current["content"]
        statuses = {entry["identity"]: PENDING for entry in selected}
        stopped = False

        for index, entry in enumerate(selected):
            identity = entry["identity"]
            if stopped:
                statuses[identity] = SKIPPED
                base["results"].append({"identity": identity, "action": entry["action"],
                                        "status": SKIPPED, "reason": "arrêt après échec"})
                continue
            statuses[identity] = APPLYING
            before_records = [dict(r, raw=dict(r["raw"])) for r in records]

            staged = apply_entry(records, entry)
            if not staged.get("ok"):
                stopped = True
                statuses[identity] = FAILED
                base["results"].append({"identity": identity, "action": entry["action"],
                                        "status": FAILED, "reason": staged.get("error")})
                self._safe_audit(FAILED_EVENT, {
                    "request_id": request_id, "sync_id": sync_id, "plan_id": plan_id,
                    "entry_identity": identity, "action": entry["action"],
                    "reason": staged.get("error")}, status="failed")
                continue

            content = serialize_store(staged["records"])
            ok_audit = self._safe_audit(WRITE_STARTED, {
                "request_id": request_id, "sync_id": sync_id, "plan_id": plan_id,
                "confirmation_id": scope.id if scope else confirmation_id,
                "entry_identity": identity, "action": entry["action"],
                "changed_fields": [c["site_field"] for c in entry["changed_fields"]],
                "before_hash": content_hash(before_content)})
            base["audit_complete"] = base["audit_complete"] and ok_audit

            written = self.writer.write(content, expected_sha256=content_hash(before_content),
                                        confirmation_id=confirmation_id)
            if written.get("error") == "CONFIRMATION_REQUIRED":
                # Rien n'est écrit : la demande remonte à l'interface. Le plan,
                # la portée et la sauvegarde restent valables pour la reprise.
                return {**base, "ok": False, "error": "CONFIRMATION_REQUIRED",
                        "confirmation": written["confirmation"],
                        "pending_entry": identity}
            if not written.get("ok"):
                stopped = True
                statuses[identity] = FAILED
                base["results"].append({"identity": identity, "action": entry["action"],
                                        "status": FAILED, "reason": written.get("error")})
                self._safe_audit(FAILED_EVENT, {
                    "request_id": request_id, "sync_id": sync_id, "plan_id": plan_id,
                    "entry_identity": identity, "action": entry["action"],
                    "reason": written.get("error")}, status="failed")
                continue

            # L'écriture a réussi : c'est seulement ici qu'on émet `applied`.
            ok_audit = self._safe_audit(APPLIED, {
                "request_id": request_id, "sync_id": sync_id, "plan_id": plan_id,
                "confirmation_id": scope.id if scope else confirmation_id,
                "selection_hash": base.get("selection_hash"),
                "entry_identity": identity, "action": entry["action"],
                "changed_fields": [c["site_field"] for c in entry["changed_fields"]],
                "before_hash": content_hash(before_content),
                "after_hash": content_hash(content),
                "backup_path": backup["path"], "idempotency_key": idempotency_key})
            base["audit_complete"] = base["audit_complete"] and ok_audit

            reread = self.writer.read()
            if not reread.get("ok"):
                stopped = True
                statuses[identity] = FAILED
                base["results"].append({"identity": identity, "action": entry["action"],
                                        "status": FAILED, "reason": "relecture impossible"})
                continue

            check = verify_entry(reread["records"], entry, before_records)
            result = {"identity": identity, "action": entry["action"],
                      "changed_fields": [c["site_field"] for c in entry["changed_fields"]],
                      "status": check["status"], "after_hash": reread["hash"][:16]}
            if not check["ok"]:
                stopped = True
                result["reason"] = check.get("reason", "")
                rollback = self.writer.write(before_content, confirmation_id=confirmation_id)
                result["status"] = ROLLED_BACK if rollback.get("ok") else FAILED
                result["rollback"] = bool(rollback.get("ok"))
                statuses[identity] = result["status"]
                base["results"].append(result)
                self._safe_audit(ROLLED_BACK_EVENT if rollback.get("ok") else FAILED_EVENT, {
                    "request_id": request_id, "sync_id": sync_id, "plan_id": plan_id,
                    "entry_identity": identity, "action": entry["action"],
                    "reason": result["reason"], "backup_path": backup["path"],
                    "verification_status": result["status"]}, status="failed")
                continue

            # Vérification passée : `verified` peut être émis.
            ok_audit = self._safe_audit(VERIFIED_EVENT, {
                "request_id": request_id, "sync_id": sync_id, "plan_id": plan_id,
                "entry_identity": identity, "action": entry["action"],
                "changed_fields": result["changed_fields"],
                "after_hash": reread["hash"], "verification_status": VERIFIED})
            base["audit_complete"] = base["audit_complete"] and ok_audit

            statuses[identity] = VERIFIED
            records = reread["records"]
            before_content = reread["content"]
            base["site_total_after"] = len(records)
            base["applied"] += 1
            base["results"].append(result)

        base["statuses"] = statuses
        base["verified_count"] = sum(1 for v in statuses.values() if v == VERIFIED)
        base["failed_count"] = sum(1 for v in statuses.values() if v in (FAILED, ROLLED_BACK))
        base["skipped_count"] = sum(1 for v in statuses.values() if v == SKIPPED)
        base["after_hash"] = before_content and content_hash(before_content)
        base["ok"] = base["failed_count"] == 0 and base["applied"] > 0

        if scope and base["applied"]:
            scope.used = True           # usage unique, même en cas d'échec partiel
        if base["ok"] and not base["audit_complete"]:
            # L'écriture a bien eu lieu : on le dit, sans jamais la rejouer.
            base["ok"] = False
            base["error"] = WRITE_SUCCEEDED_AUDIT_FAILED
            base["detail"] = ("Les modifications sont appliquées et vérifiées, "
                              "mais le journal métier est incomplet. Aucun rejeu.")
        if idempotency_key:
            SyncService._results[idempotency_key] = dict(base)
        return base

    def rollback(self, backup_path: str, *, sync_id: str = "",
                 confirmation_id: str = "") -> dict[str, Any]:
        """Restaure une sauvegarde. Opération explicite, jamais automatique hors échec."""
        path = Path(backup_path)
        if not path.is_file():
            return {"ok": False, "error": "BACKUP_NOT_FOUND", "path": backup_path}
        with path.open(encoding="utf-8", newline="") as handle:
            content = handle.read()
        written = self.writer.write(content, confirmation_id=confirmation_id)
        self._safe_audit(ROLLED_BACK_EVENT,
                         {"sync_id": sync_id, "backup_path": backup_path,
                          "after_hash": content_hash(content)},
                         status="ok" if written.get("ok") else "failed")
        if not written.get("ok"):
            return {"ok": False, "error": written.get("error"), "path": backup_path}
        reread = self.writer.read()
        return {"ok": bool(reread.get("ok")) and reread.get("hash") == content_hash(content),
                "path": backup_path, "restored_hash": reread.get("hash", "")[:16],
                "status": ROLLED_BACK}
