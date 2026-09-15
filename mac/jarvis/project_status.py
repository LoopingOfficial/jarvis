"""ProjectStatusService — récapitulatif ancré sur les données internes réelles.

« Fais le point sur mes tâches en cours et ce qui bloque. » ne doit JAMAIS être
répondu par le modèle seul : JARVIS possède déjà les faits (tâches, mémoire,
activité, agents, erreurs ouvertes). Ce module les collecte de façon
déterministe et produit un récapitulatif dont chaque ligne est traçable vers un
enregistrement de la base. Le modèle n'intervient pas ; il n'y a donc rien à
halluciner.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

# Statuts considérés comme « en cours » côté TaskManager.
ACTIVE_STATUSES = ("queued", "planning", "running", "waiting_confirmation")

# Fenêtre d'historique pour « dernière avancée » / erreurs ouvertes.
RECENT_WINDOW_S = 7 * 24 * 3600

_PROJECT_STATUS_RE = re.compile(
    r"(?:"
    r"fais?\s+le\s+point|point\s+sur\s+(?:mes|les)\s+(?:t[âa]ches?|projets?|travaux)|"
    r"o[uù]\s+en\s+(?:sont|est)\s+(?:mes|mon|les?)\s+(?:projets?|t[âa]ches?|travail|chantiers?)|"
    r"qu[''`]est[- ]ce\s+qui\s+bloque|qu[''`]est[- ]ce\s+que\s+[çc]a\s+bloque|"
    r"quels?\s+sont\s+les\s+blocages?|ce\s+qui\s+bloque|"
    r"que\s+(?:me\s+)?reste[- ]t[- ]il\s+[àa]\s+faire|"
    r"qu[''`]est[- ]ce\s+qu[''`]il\s+(?:me\s+)?reste\s+[àa]\s+faire|"
    r"mes\s+t[âa]ches?\s+en\s+cours|t[âa]ches?\s+en\s+cours|"
    r"[ée]tat\s+(?:d[''`]avancement|des\s+projets?)|"
    r"work\s+recap|project\s+status|status\s+report"
    r")",
    re.IGNORECASE,
)

# Un récap ne doit pas être confondu avec une demande d'action sur une tâche.
_NOT_STATUS_RE = re.compile(
    r"\b(?:cr[ée]e|ajoute|supprime|annule|lance|d[ée]marre|relance|termine)\b"
    r"\s+(?:une?\s+)?(?:t[âa]che|projet)",
    re.IGNORECASE,
)


def detect_project_status(text: str) -> bool:
    """Vrai si la demande porte sur l'état réel des travaux en cours."""
    value = (text or "").strip()
    if not value or _NOT_STATUS_RE.search(value):
        return False
    return bool(_PROJECT_STATUS_RE.search(value))


@dataclass
class ProjectEntry:
    """Un projet et l'état réellement observé dans la base."""
    name: str
    state: str = "en cours"
    last_progress: str = ""
    last_progress_at: float = 0.0
    blockers: list[str] = field(default_factory=list)
    next_action: str = ""
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.name,
            "state": self.state,
            "last_progress": self.last_progress,
            "last_progress_at": self.last_progress_at,
            "blockers": list(self.blockers),
            "next_action": self.next_action,
            "sources": list(self.sources),
        }


def _loads(raw: Any) -> Any:
    try:
        return json.loads(raw) if isinstance(raw, str) and raw else {}
    except (TypeError, ValueError):
        return {}


def _project_of_task(row: Any) -> str:
    meta = _loads(row["meta"] if "meta" in row.keys() else "{}")
    for key in ("project", "workspace", "kind"):
        value = str((meta or {}).get(key) or "").strip()
        if value:
            return value
    kind = str(row["kind"] or "").strip()
    return kind or "JARVIS"


def _ago(ts: float) -> str:
    if not ts:
        return ""
    delta = max(0.0, time.time() - float(ts))
    if delta < 90:
        return "à l'instant"
    if delta < 3600:
        return f"il y a {int(delta // 60)} min"
    if delta < 86400:
        return f"il y a {int(delta // 3600)} h"
    return f"il y a {int(delta // 86400)} j"


class ProjectStatusService:
    """Collecte déterministe de l'état réel des travaux.

    Aucune donnée n'est inventée : si la base est vide, le rapport le dit.
    """

    def __init__(self, core) -> None:
        self._core = core

    # -- collecte ---------------------------------------------------------
    def collect(self, *, limit_projects: int = 12) -> dict[str, Any]:
        core = self._core
        db = core.db
        since = time.time() - RECENT_WINDOW_S
        entries: dict[str, ProjectEntry] = {}

        def entry(name: str) -> ProjectEntry:
            return entries.setdefault(name, ProjectEntry(name=name))

        # 1. Tâches actives -------------------------------------------------
        placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        # La tâche de récapitulatif elle-même est exclue : elle est en cours au
        # moment de la collecte et n'est pas un travail de l'utilisateur.
        active = db.query(
            f"SELECT id, name, kind, status, progress, agent, meta, created_at, started_at "
            f"FROM tasks WHERE status IN ({placeholders}) AND kind <> 'project_status' "
            f"ORDER BY created_at DESC LIMIT 60",
            ACTIVE_STATUSES,
        )
        for row in active:
            item = entry(_project_of_task(row))
            item.state = "en cours"
            item.sources.append(f"task:{row['id']}")
            pct = int(round(float(row["progress"] or 0) * 100))
            item.last_progress = f"{row['name']} — {row['status']} ({pct}%)"
            item.last_progress_at = float(row["started_at"] or row["created_at"] or 0)
            if row["status"] == "waiting_confirmation":
                item.blockers.append(f"En attente de confirmation : {row['name']}")

        # 2. Échecs récents = blocages réels --------------------------------
        failed = db.query(
            "SELECT id, name, kind, error, meta, completed_at FROM tasks "
            "WHERE status='failed' AND kind <> 'project_status' AND COALESCE(completed_at,0) >= ? "
            "ORDER BY completed_at DESC LIMIT 40",
            (since,),
        )
        for row in failed:
            item = entry(_project_of_task(row))
            item.sources.append(f"task:{row['id']}")
            err = (row["error"] or "").strip().splitlines()
            detail = err[0][:180] if err else "échec sans message"
            item.blockers.append(f"{row['name']} a échoué : {detail}")

        # 3. Dernière avancée réelle via completions récentes ---------------
        done = db.query(
            "SELECT id, name, kind, meta, result, completed_at FROM tasks "
            "WHERE status='completed' AND kind <> 'project_status' AND COALESCE(completed_at,0) >= ? "
            "ORDER BY completed_at DESC LIMIT 60",
            (since,),
        )
        for row in done:
            item = entry(_project_of_task(row))
            ts = float(row["completed_at"] or 0)
            if ts > item.last_progress_at:
                item.last_progress_at = ts
                item.last_progress = f"{row['name']} — terminé"
                item.sources.append(f"task:{row['id']}")
            if item.state != "en cours":
                item.state = "à jour"

        # 4. Mémoire projet : notes et prochaines actions -------------------
        try:
            memories = db.query(
                "SELECT id, content, project, tags, updated_at FROM memories "
                "WHERE TRIM(COALESCE(project,'')) <> '' ORDER BY updated_at DESC LIMIT 120",
            )
        except Exception:  # pragma: no cover - schéma ancien
            memories = []
        for row in memories:
            name = str(row["project"] or "").strip()
            if not name:
                continue
            item = entry(name)
            item.sources.append(f"memory:{row['id']}")
            content = (row["content"] or "").strip()
            low = content.casefold()
            if not item.next_action and re.search(
                r"\b(?:prochaine?\s+[ée]tape|next|[àa]\s+faire|todo|reste\s+[àa])\b", low
            ):
                item.next_action = content[:200]
            if re.search(r"\b(?:bloqu|blocker|en\s+attente|impossible|manque)\w*\b", low):
                item.blockers.append(content[:200])
            ts = float(row["updated_at"] or 0)
            if not item.last_progress and ts:
                item.last_progress_at = max(item.last_progress_at, ts)
                item.last_progress = content[:160]

        # 5. Erreurs ouvertes remontées au feed -----------------------------
        open_errors: list[dict[str, Any]] = []
        try:
            rows = db.query(
                "SELECT ts, title, detail, source FROM feed "
                "WHERE level='error' AND read=0 AND ts >= ? ORDER BY ts DESC LIMIT 20",
                (since,),
            )
            open_errors = [
                {"ts": float(r["ts"] or 0), "title": r["title"] or "",
                 "detail": (r["detail"] or "")[:200], "source": r["source"] or ""}
                for r in rows
            ]
        except Exception:  # pragma: no cover
            open_errors = []

        # 6. Agents réellement actifs ---------------------------------------
        agents: list[dict[str, Any]] = []
        try:
            for a in core.agents.list():
                if str(a.get("status") or "") in {"active", "running"} or a.get("last_error"):
                    agents.append({
                        "id": a.get("id", ""), "status": a.get("status", ""),
                        "action": a.get("current_action", ""), "error": a.get("last_error", ""),
                    })
        except Exception:  # pragma: no cover
            agents = []

        # 7. Activité récente (trace) ---------------------------------------
        activity: list[dict[str, Any]] = []
        try:
            rows = db.query(
                "SELECT ts, kind, title, state FROM activity_trace WHERE ts >= ? "
                "ORDER BY ts DESC LIMIT 15", (since,),
            )
            activity = [{"ts": float(r["ts"] or 0), "kind": r["kind"], "title": r["title"],
                         "state": r["state"]} for r in rows]
        except Exception:  # pragma: no cover
            activity = []

        ordered = sorted(
            entries.values(),
            key=lambda e: (len(e.blockers) > 0, e.last_progress_at),
            reverse=True,
        )[:limit_projects]
        for item in ordered:
            # Déduplication stable, ordre préservé.
            item.blockers = list(dict.fromkeys(item.blockers))[:6]
            item.sources = list(dict.fromkeys(item.sources))[:10]
            if item.blockers and item.state == "en cours":
                item.state = "bloqué"

        return {
            "projects": [e.to_dict() for e in ordered],
            "open_errors": open_errors,
            "agents": agents,
            "activity": activity,
            "counts": {
                "active_tasks": len(active),
                "failed_recent": len(failed),
                "completed_recent": len(done),
                "open_errors": len(open_errors),
            },
            "generated_at": time.time(),
            "grounded": True,
        }

    # -- rendu ------------------------------------------------------------
    def render(self, data: dict[str, Any] | None = None) -> str:
        data = data if data is not None else self.collect()
        projects = data.get("projects") or []
        counts = data.get("counts") or {}
        lines: list[str] = []

        if not projects:
            lines.append("Aucun projet ni tâche en cours n'est enregistré dans JARVIS.")
            if counts.get("open_errors"):
                lines.append("")
            else:
                return "\n".join(lines)
        else:
            for item in projects:
                lines.append(f"**PROJET** — {item['project']}")
                lines.append(f"ÉTAT : {item['state']}")
                progress = item.get("last_progress") or "aucune avancée enregistrée"
                stamp = _ago(item.get("last_progress_at") or 0)
                lines.append(
                    f"DERNIÈRE AVANCÉE : {progress}" + (f" ({stamp})" if stamp else ""))
                if item["blockers"]:
                    lines.append("BLOCAGE :")
                    lines.extend(f"  - {b}" for b in item["blockers"])
                else:
                    lines.append("BLOCAGE : aucun blocage enregistré")
                lines.append(
                    "PROCHAINE ACTION : "
                    + (item.get("next_action") or "non définie dans la mémoire projet"))
                lines.append("")

        errors = data.get("open_errors") or []
        if errors:
            lines.append("**ERREURS OUVERTES**")
            for e in errors[:5]:
                lines.append(f"  - {e['title']}" + (f" — {e['detail']}" if e["detail"] else ""))
            lines.append("")

        agents = data.get("agents") or []
        if agents:
            lines.append("**AGENTS ACTIFS**")
            for a in agents[:5]:
                label = a.get("action") or a.get("status") or ""
                lines.append(f"  - {a['id']} : {label}"
                             + (f" (erreur : {a['error']})" if a.get("error") else ""))
            lines.append("")

        lines.append(
            "_Source : {active} tâche(s) active(s), {failed} échec(s) récent(s), "
            "{errors} erreur(s) ouverte(s)._".format(
                active=counts.get("active_tasks", 0),
                failed=counts.get("failed_recent", 0),
                errors=counts.get("open_errors", 0),
            ))
        return "\n".join(lines).strip()
