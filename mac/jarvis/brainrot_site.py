"""brainrot-fortnite.com — analytics réelles et propositions d'action.

Trois règles tiennent tout ce module :

1. **Rien n'est inventé.** Chaque nombre vient d'une requête SQL exécutée sur
   la vraie base. Une métrique que le schéma ne permet pas de calculer est
   déclarée indisponible, jamais estimée.
2. **Lecture seule.** `AnalyticsRepository` n'émet que des SELECT ; toute autre
   requête est refusée ici, avant même la politique d'outils.
3. **Europe/Paris.** « aujourd'hui » suit le fuseau du site, pas celui du
   serveur qui exécute la requête.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
PROFILE = Path(__file__).resolve().parents[2] / "velko/config/projects/brainrot-fortnite.json"
_SELECT_ONLY = re.compile(r"^\s*(select|show|describe|desc|explain)\b", re.I)
_FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|truncate|create|replace|grant)\b", re.I)

UNAVAILABLE = "Cette donnée n'est pas disponible."


def load_profile() -> dict[str, Any]:
    """Profil du projet. Absent = audit à refaire, pas une valeur par défaut."""
    try:
        return json.loads(PROFILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


@dataclass
class Metric:
    """Une valeur mesurée, ou l'explication de son absence."""
    key: str
    label: str
    value: Any = None
    available: bool = True
    reason: str = ""
    previous: Any = None

    @property
    def change_pct(self) -> float | None:
        """Évolution vs période précédente, seulement si les deux existent."""
        if not self.available or self.previous in (None, 0) or self.value is None:
            return None
        try:
            return round((float(self.value) - float(self.previous)) / float(self.previous) * 100, 1)
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    def render(self) -> str:
        if not self.available:
            return f"{self.label} : {self.reason or UNAVAILABLE}"
        line = f"{self.label} : {self.value}"
        pct = self.change_pct
        if pct is not None:
            line += f" ({pct:+.1f} % vs période précédente)"
        return line

    def summary(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "value": self.value,
                "available": self.available, "reason": self.reason,
                "previous": self.previous, "change_pct": self.change_pct}


class AnalyticsRepository:
    """Accès READ ONLY aux données réelles du site."""

    def __init__(self, core: Any, connector_id: str = "") -> None:
        self.core = core
        profile = load_profile()
        self.profile = profile
        self.connector_id = (connector_id
                             or (profile.get("database") or {}).get("connector_id")
                             or "mysql-mariadb")
        self._columns: dict[str, set[str]] = {}

    # -- exécution --------------------------------------------------------
    def query(self, sql: str) -> tuple[bool, list[dict[str, Any]], str]:
        """(ok, lignes, erreur). Refuse tout ce qui n'est pas une lecture."""
        if not _SELECT_ONLY.match(sql) or _FORBIDDEN.search(sql):
            return False, [], "AnalyticsRepository est en lecture seule : requête refusée."
        result = self.core.runner.run("db.query", {
            "connector_id": self.connector_id, "query": sql}, agent="jarvis")
        if not result.ok:
            return False, [], str(result.output)[:300]
        return True, self._parse(result.output), ""

    @staticmethod
    def _parse(output: str) -> list[dict[str, Any]]:
        """Sortie tabulée du connecteur → lignes exploitables."""
        lines = [l for l in str(output or "").splitlines() if l.strip()]
        if len(lines) < 2:
            return []
        headers = lines[0].split("\t")
        rows: list[dict[str, Any]] = []
        for raw in lines[1:]:
            if raw.startswith("…"):
                continue
            cells = raw.split("\t")
            if len(cells) != len(headers):
                continue
            rows.append({h: (None if c == "NULL" else c) for h, c in zip(headers, cells)})
        return rows

    def scalar(self, sql: str, field_name: str = "") -> tuple[bool, Any, str]:
        ok, rows, err = self.query(sql)
        if not ok or not rows:
            return False, None, err or "aucune ligne"
        row = rows[0]
        key = field_name or next(iter(row))
        value = row.get(key)
        try:
            return True, int(value), ""
        except (TypeError, ValueError):
            return True, value, ""

    # -- introspection ----------------------------------------------------
    def columns(self, table: str) -> set[str]:
        """Colonnes RÉELLES d'une table. On ne suppose jamais un nom."""
        if table in self._columns:
            return self._columns[table]
        schema = (self.profile.get("database") or {}).get("schema", "")
        ok, rows, err = self.query(
            "SELECT column_name FROM information_schema.columns "
            f"WHERE table_schema='{schema}' AND table_name='{table}'")
        if not ok:
            # Une base injoignable n'est PAS une colonne absente : sans cette
            # distinction, une coupure faisait déclarer toutes les métriques
            # « indisponibles » alors que les données existent.
            self._db_error = err or "base injoignable"
            return set()
        found = {str(r.get("column_name") or r.get("COLUMN_NAME")) for r in rows}
        self._columns[table] = found
        return found

    @property
    def db_error(self) -> str:
        return getattr(self, "_db_error", "")

    def has(self, table: str, *cols: str) -> bool:
        available = self.columns(table)
        return bool(available) and all(c in available for c in cols)

    def missing_reason(self, table: str, what: str) -> str:
        """Pourquoi la donnée manque : panne ou schéma. Jamais ambigu."""
        if self.db_error:
            return f"base de données injoignable ({self.db_error[:120]})"
        return what

    # -- fenêtres temporelles (Europe/Paris) ------------------------------
    @staticmethod
    def now() -> datetime:
        return datetime.now(PARIS)

    def windows(self) -> dict[str, tuple[str, str]]:
        """Bornes SQL des périodes comparées, en heure de Paris."""
        now = self.now()
        start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        fmt = "%Y-%m-%d %H:%M:%S"
        def span(a: datetime, b: datetime) -> tuple[str, str]:
            return a.strftime(fmt), b.strftime(fmt)
        return {
            "today": span(start_today, now),
            "yesterday": span(start_today - timedelta(days=1), start_today),
            "d7": span(now - timedelta(days=7), now),
            "d7_prev": span(now - timedelta(days=14), now - timedelta(days=7)),
            "d30": span(now - timedelta(days=30), now),
            "d30_prev": span(now - timedelta(days=60), now - timedelta(days=30)),
        }

    def _count_between(self, table: str, column: str, span: tuple[str, str]) -> int | None:
        ok, value, _ = self.scalar(
            f"SELECT COUNT(*) AS n FROM {table} "
            f"WHERE {column} >= '{span[0]}' AND {column} < '{span[1]}'", "n")
        return value if ok else None

    # -- métriques --------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """Photographie réelle du site. Chaque métrique porte sa disponibilité."""
        w = self.windows()
        metrics: list[Metric] = []

        if self.has("users", "created_at"):
            ok, total, err = self.scalar("SELECT COUNT(*) AS n FROM users", "n")
            metrics.append(Metric("membres", "Membres", total, ok, err))
            today = self._count_between("users", "created_at", w["today"])
            yesterday = self._count_between("users", "created_at", w["yesterday"])
            metrics.append(Metric("inscriptions_jour", "Nouveaux aujourd'hui", today,
                                  today is not None, "", yesterday))
            d7 = self._count_between("users", "created_at", w["d7"])
            d7p = self._count_between("users", "created_at", w["d7_prev"])
            metrics.append(Metric("inscriptions_7j", "Inscriptions 7 jours", d7,
                                  d7 is not None, "", d7p))
            d30 = self._count_between("users", "created_at", w["d30"])
            d30p = self._count_between("users", "created_at", w["d30_prev"])
            metrics.append(Metric("inscriptions_30j", "Inscriptions 30 jours", d30,
                                  d30 is not None, "", d30p))
        else:
            metrics.append(Metric("membres", "Membres", available=False,
                                  reason=self.missing_reason(
                                      "users", "table users introuvable ou sans created_at")))

        if self.has("users", "email_verified_at"):
            ok, confirmed, _ = self.scalar(
                "SELECT COUNT(*) AS n FROM users WHERE email_verified_at IS NOT NULL", "n")
            ok2, pending, _ = self.scalar(
                "SELECT COUNT(*) AS n FROM users WHERE email_verified_at IS NULL", "n")
            metrics.append(Metric("emails_confirmes", "Emails confirmés", confirmed, ok))
            metrics.append(Metric("emails_non_confirmes", "Emails non confirmés", pending, ok2))
            # Garde-fou §16 : la colocation de confirmation date de la migration
            # 2026-09-01_044_auth_runtime_schema_foundation. Les comptes créés AVANT
            # n'ont jamais reçu d'email de vérification : les « relancer » serait une
            # fausse action (ils ignorent même le mécanisme). La vraie file d'attente
            # n'est donc que les non-confirmés créés depuis — 2 comptes au 2026-09-23.
            ok3, awaiting, _ = self.scalar(
                "SELECT COUNT(*) AS n FROM users "
                "WHERE email_verified_at IS NULL AND created_at >= '2026-09-01 00:00:00'", "n")
            ok4, legacy, _ = self.scalar(
                "SELECT COUNT(*) AS n FROM users "
                "WHERE email_verified_at IS NULL AND created_at < '2026-09-01 00:00:00'", "n")
            metrics.append(Metric("emails_attente_reelle", "Emails réellement en attente",
                                  awaiting, ok3))
            metrics.append(Metric("emails_heritage_preintroduction",
                                  "Emails non confirmés pré-vérification (jamais invités)",
                                  legacy, ok4))
        else:
            metrics.append(Metric("emails_non_confirmes", "Emails non confirmés", available=False,
                                  reason=self.missing_reason(
                                      "users", "colonne de confirmation absente du schéma")))

        # Actifs : présence réelle. Ce n'est PAS un compteur de connexions.
        if self.has("activity_presence", "last_seen_at"):
            for key, label, span in (("actifs_jour", "Actifs aujourd'hui", w["today"]),
                                     ("actifs_7j", "Actifs 7 jours", w["d7"]),
                                     ("actifs_30j", "Actifs 30 jours", w["d30"])):
                value = self._count_between("activity_presence", "last_seen_at", span)
                metrics.append(Metric(key, label, value, value is not None))
        else:
            metrics.append(Metric("actifs_jour", "Actifs aujourd'hui", available=False,
                                  reason=self.missing_reason(
                                      "activity_presence", "aucune table de présence exploitable")))

        # Connexions : volontairement déclarées indisponibles.
        metrics.append(Metric(
            "connexions_jour", "Connexions aujourd'hui", available=False,
            reason=(self.profile.get("unavailable_metrics", {}).get("connexions_par_jour")
                    or "le schéma n'enregistre pas les connexions")))

        if self.has("blog_posts", "published_at", "status"):
            ok, last, _ = self.scalar(
                "SELECT MAX(published_at) AS d FROM blog_posts WHERE status='published'", "d")
            days = None
            if ok and last:
                try:
                    published = datetime.strptime(str(last), "%Y-%m-%d %H:%M:%S").replace(tzinfo=PARIS)
                    days = (self.now() - published).days
                except Exception:
                    days = None
            metrics.append(Metric("blog_dernier_article", "Dernier article publié",
                                  f"il y a {days} jour(s)" if days is not None else "aucun",
                                  True))
            report = {"last_published_days": days}
        else:
            report = {"last_published_days": None}
            metrics.append(Metric("blog_dernier_article", "Dernier article publié",
                                  available=False,
                                  reason=self.missing_reason(
                                      "blog_posts", "table blog_posts inattendue")))

        if self.has("brainrots", "id"):
            ok, count, _ = self.scalar("SELECT COUNT(*) AS n FROM brainrots", "n")
            metrics.append(Metric("brainrots", "Brainrots au catalogue", count, ok))

        return {"metrics": [m.summary() for m in metrics],
                "rendered": [m.render() for m in metrics],
                "generated_at": self.now().isoformat(timespec="seconds"),
                "timezone": "Europe/Paris", **report}


# ---------------------------------------------------------------------------
@dataclass
class Opportunity:
    """Une observation réelle, sa raison d'être, et une action concrète."""
    key: str
    observation: str
    why: str
    action_label: str
    tool: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    confirm: bool = True
    count: int | None = None

    def summary(self) -> dict[str, Any]:
        return {"key": self.key, "observation": self.observation, "why": self.why,
                "action": {"label": self.action_label, "tool": self.tool,
                           "arguments": self.arguments, "confirm": self.confirm},
                "count": self.count}


class VelkoOpportunityEngine:
    """Transforme des données RÉELLES en actions proposées.

    Aucune proposition n'est produite sans le fait chiffré qui la motive : si
    la métrique est indisponible, il n'y a pas de proposition — pas de conseil
    générique de remplissage.
    """

    def __init__(self, repo: AnalyticsRepository) -> None:
        self.repo = repo

    def detect(self, snapshot: dict[str, Any] | None = None) -> list[Opportunity]:
        snap = snapshot or self.repo.snapshot()
        by_key = {m["key"]: m for m in snap["metrics"]}
        out: list[Opportunity] = []

        pending = by_key.get("emails_attente_reelle") or {}
        members = by_key.get("membres") or {}
        if pending.get("available") and isinstance(pending.get("value"), int) and pending["value"] > 0:
            share = ""
            if members.get("available") and members.get("value"):
                share = f" soit {round(pending['value'] / members['value'] * 100)} % des comptes,"
            out.append(Opportunity(
                key="relance_emails",
                observation=f"{pending['value']} membres créés depuis l'introduction de la "
                            "vérification n'ont toujours pas confirmé leur adresse email,"
                            f"{share} d'après la base.",
                why="Un compte créé après la vérification et non confirmé ne reçoit rien "
                    "et ne revient presque jamais.",
                action_label="Préparer l'email de relance",
                tool="brainrot.email.prepare_campaign",
                arguments={"audience": "unverified"}, confirm=True,
                count=pending["value"]))
        else:
            legacy = by_key.get("emails_heritage_preintroduction") or {}
            if (pending.get("available") and pending.get("value") == 0
                    and legacy.get("available") and int(legacy.get("value") or 0) > 0):
                out.append(Opportunity(
                    key="pas_de_relance_legacy",
                    observation=f"{legacy['value']} comptes non confirmés datent d'avant "
                                "l'introduction de la vérification : ils n'ont jamais reçu "
                                "d'email et ne font pas partie d'une liste de relance.",
                    why="Compiler l'ensemble des comptes non confirmés (y compris pré-vérification) "
                        "gonflerait une campagne de 307 à un seul chiffre : aucune relance massive "
                        "ne doit viser ces comptes qui ignorent le mécanisme.",
                    action_label="Ne préparer aucune campagne vers les comptes pré-vérification",
                    tool="brainrot.email.prepare_campaign",
                    arguments={"audience": "unverified"}, confirm=False,
                    count=int(legacy["value"])))

        signups = by_key.get("inscriptions_7j") or {}
        if signups.get("available") and signups.get("change_pct") is not None:
            pct = signups["change_pct"]
            if pct <= -10:
                out.append(Opportunity(
                    key="baisse_inscriptions",
                    observation=f"Les inscriptions ont baissé de {abs(pct):.1f} % sur 7 jours "
                                f"({signups['value']} contre {signups['previous']} la semaine précédente).",
                    why="Une baisse de cette ampleur vient en général d'une source de trafic ou d'une page d'entrée.",
                    action_label="Analyser l'origine de la baisse",
                    tool="brainrot.analytics.registrations",
                    arguments={"days": 30}, confirm=False,
                    count=signups["value"]))
            elif pct >= 25:
                out.append(Opportunity(
                    key="hausse_inscriptions",
                    observation=f"Les inscriptions ont augmenté de {pct:.1f} % sur 7 jours.",
                    why="Une hausse nette mérite d'être comprise pour être reproduite.",
                    action_label="Détailler les inscriptions sur 30 jours",
                    tool="brainrot.analytics.registrations",
                    arguments={"days": 30}, confirm=False,
                    count=signups["value"]))

        days = snap.get("last_published_days")
        if isinstance(days, int) and days >= 14:
            out.append(Opportunity(
                key="blog_silencieux",
                observation=f"Le blog n'a rien publié depuis {days} jours.",
                why="Le contenu récent porte le référencement et le retour des membres.",
                action_label="Chercher un sujet et préparer un brouillon",
                tool="brainrot.blog.create_draft",
                arguments={}, confirm=True))

        active = by_key.get("actifs_7j") or {}
        if (active.get("available") and members.get("available")
                and isinstance(active.get("value"), int) and members.get("value")):
            ratio = active["value"] / members["value"]
            if ratio < 0.15:
                out.append(Opportunity(
                    key="faible_activite",
                    observation=f"{active['value']} membres actifs sur 7 jours pour "
                                f"{members['value']} comptes, soit {round(ratio*100)} %.",
                    why="La base grandit plus vite que l'usage : les nouveaux ne reviennent pas.",
                    action_label="Analyser l'activité des 30 derniers jours",
                    tool="brainrot.analytics.activity",
                    arguments={"days": 30}, confirm=False,
                    count=active["value"]))
        return out

    def briefing(self) -> dict[str, Any]:
        """« Fais-moi le point » : état, changements, à surveiller, je peux faire."""
        snap = self.repo.snapshot()
        opportunities = self.detect(snap)
        by_key = {m["key"]: m for m in snap["metrics"]}

        etat = [by_key[k]["value"] and f"{by_key[k]['label']} : {by_key[k]['value']}" or None
                for k in ("membres", "inscriptions_jour", "actifs_7j", "emails_non_confirmes")
                if k in by_key and by_key[k]["available"]]
        changements = [m["label"] + f" {m['change_pct']:+.1f} %"
                       for m in snap["metrics"]
                       if m["available"] and m.get("change_pct") is not None]
        indisponibles = [m["label"] + " — " + (m["reason"] or UNAVAILABLE)
                         for m in snap["metrics"] if not m["available"]]

        lines = ["BRAINROT FORTNITE — " + snap["generated_at"][:16].replace("T", " ") + " (Europe/Paris)", ""]
        lines.append("ÉTAT")
        if self.repo.db_error:
            lines.append("  BASE DE DONNÉES INJOIGNABLE — aucune métrique ne peut être lue.")
            lines.append("  " + self.repo.db_error[:160])
        else:
            lines += ["  " + e for e in etat if e] or ["  Aucune métrique disponible."]
        if changements:
            lines += ["", "CHANGEMENTS"] + ["  " + c for c in changements]
        lines += ["", "À SURVEILLER"]
        lines += ["  " + o.observation for o in opportunities[:4]] or ["  Rien d'anormal dans les données lues."]
        if opportunities:
            lines += ["", "JE PEUX FAIRE"]
            lines += [f"  • {o.action_label.lower()} — {o.observation[:80]}"
                      for o in opportunities[:3]]
        if indisponibles:
            lines += ["", "NON MESURABLE AVEC LE SCHÉMA ACTUEL"] + ["  " + i for i in indisponibles]
        return {"text": "\n".join(lines), "snapshot": snap,
                "opportunities": [o.summary() for o in opportunities]}
