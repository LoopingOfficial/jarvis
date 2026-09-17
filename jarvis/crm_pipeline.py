"""CRM étendu : sociétés, opportunités, interactions, intention et scoring.

Complète `jarvis/crm.py`, qui reste responsable des contacts et de la levée
d'ambiguïté. On ne duplique pas `CrmStore` : `crm_contacts` demeure la table
pivot, enrichie de colonnes (migration 12).

Deux règles tenues ici
----------------------
1. **L'historique est écrit par ceux qui agissent.** Chaque interaction porte
   l'agent et l'outil qui l'ont créée, plus un `source_ref` vers la pièce
   d'origine (mail, tâche, document). Sans cette traçabilité, une fiche
   enrichie automatiquement devient invérifiable.

2. **L'intention et le score sont déterministes et explicables.** Ce sont des
   règles lisibles, pas un modèle opaque : `score_detail` conserve le détail du
   calcul, et l'intention est accompagnée d'une confiance et des motifs
   déclencheurs. Un score que l'utilisateur ne peut pas auditer ne mérite pas
   qu'il fonde une relance commerciale dessus.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from .crm import fold
from .db import new_id

# ---------------------------------------------------------------------------
# Intention : motifs FR/EN observés dans des mails entrants réels.
#
# ATTENTION : les motifs sont comparés au texte passé par `fold()`, donc SANS
# accents et en minuscules. Les écrire ici accentués (« problème ») les rendrait
# inopérants — ils ne matcheraient jamais. On évite aussi de clore sur `\b`
# quand une terminaison varie : « interesse / interesses / interessee ».
# ---------------------------------------------------------------------------
INTENT_RULES: dict[str, dict[str, Any]] = {
    "achat": {
        "label": "Intention d'achat",
        "weight": 30,
        "patterns": [r"\bdevis\b", r"\btarifs?\b", r"\bprix\b", r"\bcombien\b", r"\bbon de commande\b",
                     r"\bcommander\b", r"\bsouscrire\b", r"\bquote\b", r"\bpricing\b", r"\bbuy\b"],
    },
    "demo": {
        "label": "Demande de démonstration",
        "weight": 22,
        "patterns": [r"\bdemos?\b", r"\bdemonstration", r"\bessai\b", r"\bpresentation",
                     r"\brendez-?vous\b", r"\btrial\b"],
    },
    "support": {
        "label": "Demande de support",
        "weight": 8,
        "patterns": [r"\bprobleme", r"\bbug\b", r"\bne fonctionne pas\b", r"\berreur",
                     r"\bpanne\b", r"\bincident", r"\bsupport\b"],
    },
    "negociation": {
        "label": "Négociation",
        "weight": 26,
        "patterns": [r"\bremise\b", r"\bnegoci", r"\bgeste commercial\b", r"\bbudget\b",
                     r"\bcontrat", r"\bconditions\b", r"\bdiscount\b"],
    },
    "relance": {
        "label": "Relance / suivi",
        "weight": 10,
        "patterns": [r"\brelance", r"\bou en est\b", r"\bsuite a\b", r"\bpour rappel\b", r"\bfollow ?up\b"],
    },
    "desinteret": {
        "label": "Désintérêt",
        "weight": -25,
        "patterns": [r"\bpas interess", r"\bplus interess", r"\bplus tard\b", r"\bdesabonn",
                     r"\bne souhaite pas\b", r"\bsans suite\b", r"\bstop\b", r"\bunsubscribe\b",
                     r"\bnot interested\b"],
    },
}

STAGES = ("nouveau", "qualifie", "proposition", "negociation", "gagne", "perdu")


def _now() -> float:
    return time.time()


def _row(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def detect_intent(text: str) -> dict[str, Any]:
    """Classe un texte libre (objet + corps d'un mail, note d'appel).

    Renvoie l'intention dominante, une confiance bornée à [0,1] et les motifs
    qui l'ont déclenchée. Aucune intention n'est renvoyée si rien ne matche :
    « inconnu » est une réponse honnête, contrairement à un choix par défaut.
    """
    haystack = fold(text)
    if not haystack.strip():
        return {"intent": "", "label": "", "confidence": 0.0, "matches": []}
    scores: dict[str, list[str]] = {}
    for key, rule in INTENT_RULES.items():
        hits = [p for p in rule["patterns"] if re.search(p, haystack)]
        if hits:
            scores[key] = hits
    if not scores:
        return {"intent": "", "label": "", "confidence": 0.0, "matches": []}
    best = max(scores, key=lambda k: (len(scores[k]), abs(INTENT_RULES[k]["weight"])))
    hits = scores[best]
    # Confiance : croît avec le nombre de motifs, plafonnée — deux mots-clés ne
    # valent pas une certitude, et on ne prétendra jamais l'inverse.
    confidence = min(0.9, 0.45 + 0.15 * (len(hits) - 1))
    return {"intent": best, "label": INTENT_RULES[best]["label"],
            "confidence": round(confidence, 2), "matches": hits}


class CompanyStore:
    """Sociétés. Le rapprochement se fait par domaine puis par nom replié."""

    def __init__(self, db) -> None:
        self._db = db

    def upsert(self, data: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        cid = str(data.get("id") or "").strip() or self._match(data) or new_id("co")
        existing = self.get(cid)
        fields = {
            "name": str(data.get("name") or (existing or {}).get("name") or "").strip(),
            "domain": str(data.get("domain") or (existing or {}).get("domain") or "").strip().lower(),
            "industry": str(data.get("industry") or (existing or {}).get("industry") or "").strip(),
            "size": str(data.get("size") or (existing or {}).get("size") or "").strip(),
            "website": str(data.get("website") or (existing or {}).get("website") or "").strip(),
            "address": str(data.get("address") or (existing or {}).get("address") or "").strip(),
            "vat_number": str(data.get("vat_number") or (existing or {}).get("vat_number") or "").strip(),
            "notes": str(data.get("notes") or (existing or {}).get("notes") or "").strip(),
            "tags": json.dumps(data.get("tags") or (existing or {}).get("tags") or [], ensure_ascii=False),
        }
        if not fields["name"]:
            raise ValueError("Le nom de la société est obligatoire.")
        if existing:
            self._db.execute(
                "UPDATE crm_companies SET name=?, domain=?, industry=?, size=?, website=?, address=?, "
                "vat_number=?, notes=?, tags=?, updated_at=? WHERE id=?", (*fields.values(), now, cid))
        else:
            self._db.execute(
                "INSERT INTO crm_companies(id, name, domain, industry, size, website, address, vat_number, "
                "notes, tags, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, *fields.values(), now, now))
        return self.get(cid) or {}

    def _match(self, data: dict[str, Any]) -> str:
        """Évite de créer un doublon quand la société existe déjà."""
        domain = str(data.get("domain") or "").strip().lower()
        if domain:
            row = self._db.one("SELECT id FROM crm_companies WHERE domain=?", (domain,))
            if row:
                return row["id"]
        name = fold(data.get("name"))
        if name:
            for row in self._db.query("SELECT id, name FROM crm_companies"):
                if fold(row["name"]) == name:
                    return row["id"]
        return ""

    def get(self, company_id: str) -> dict[str, Any] | None:
        row = self._db.one("SELECT * FROM crm_companies WHERE id=?", (company_id,))
        if not row:
            return None
        out = _row(row)
        try:
            out["tags"] = json.loads(out.get("tags") or "[]")
        except Exception:
            out["tags"] = []
        return out

    def all(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._db.query("SELECT id FROM crm_companies ORDER BY name COLLATE NOCASE LIMIT ?", (limit,))
        return [self.get(r["id"]) or {} for r in rows]

    def delete(self, company_id: str) -> bool:
        # Les contacts ne sont pas supprimés : ils perdent seulement le rattachement.
        self._db.execute("UPDATE crm_contacts SET company_id='' WHERE company_id=?", (company_id,))
        return bool(self._db.execute("DELETE FROM crm_companies WHERE id=?", (company_id,)).rowcount)


class DealStore:
    """Opportunités commerciales."""

    def __init__(self, db) -> None:
        self._db = db

    def upsert(self, data: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        did = str(data.get("id") or "").strip() or new_id("deal")
        existing = self.get(did)
        stage = str(data.get("stage") or (existing or {}).get("stage") or "nouveau")
        if stage not in STAGES:
            raise ValueError(f"Étape inconnue : {stage} (attendu : {', '.join(STAGES)})")
        status = str(data.get("status") or (existing or {}).get("status") or "open")
        # Le statut suit l'étape : une opportunité en « gagne » qui resterait
        # « open » fausserait tous les agrégats du pipeline.
        if stage == "gagne":
            status = "won"
        elif stage == "perdu":
            status = "lost"
        closed_at = (existing or {}).get("closed_at")
        if status in ("won", "lost") and not closed_at:
            closed_at = now
        if status == "open":
            closed_at = None

        fields = {
            "title": str(data.get("title") or (existing or {}).get("title") or "").strip(),
            "contact_id": str(data.get("contact_id") or (existing or {}).get("contact_id") or ""),
            "company_id": str(data.get("company_id") or (existing or {}).get("company_id") or ""),
            "stage": stage, "status": status,
            "amount": float(data.get("amount", (existing or {}).get("amount") or 0) or 0),
            "currency": str(data.get("currency") or (existing or {}).get("currency") or "EUR"),
            "probability": int(data.get("probability", (existing or {}).get("probability") or 0) or 0),
            "source": str(data.get("source") or (existing or {}).get("source") or ""),
            "notes": str(data.get("notes") or (existing or {}).get("notes") or ""),
            "expected_close_at": data.get("expected_close_at", (existing or {}).get("expected_close_at")),
            "closed_at": closed_at,
        }
        if not fields["title"]:
            raise ValueError("Le titre de l'opportunité est obligatoire.")
        if existing:
            self._db.execute(
                "UPDATE crm_deals SET title=?, contact_id=?, company_id=?, stage=?, status=?, amount=?, "
                "currency=?, probability=?, source=?, notes=?, expected_close_at=?, closed_at=?, updated_at=? "
                "WHERE id=?", (*fields.values(), now, did))
        else:
            self._db.execute(
                "INSERT INTO crm_deals(id, title, contact_id, company_id, stage, status, amount, currency, "
                "probability, source, notes, expected_close_at, closed_at, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (did, *fields.values(), now, now))
        return self.get(did) or {}

    def get(self, deal_id: str) -> dict[str, Any] | None:
        row = self._db.one("SELECT * FROM crm_deals WHERE id=?", (deal_id,))
        return _row(row) if row else None

    def list(self, *, contact_id: str = "", status: str = "", limit: int = 200) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM crm_deals WHERE 1=1", []
        if contact_id:
            sql += " AND contact_id=?"; params.append(contact_id)
        if status:
            sql += " AND status=?"; params.append(status)
        sql += " ORDER BY updated_at DESC LIMIT ?"; params.append(limit)
        return [_row(r) for r in self._db.query(sql, params)]

    def delete(self, deal_id: str) -> bool:
        return bool(self._db.execute("DELETE FROM crm_deals WHERE id=?", (deal_id,)).rowcount)

    def pipeline(self) -> dict[str, Any]:
        """Agrégat par étape + valeur pondérée par la probabilité."""
        stages = {s: {"count": 0, "amount": 0.0} for s in STAGES}
        weighted = 0.0
        for d in self.list(limit=1000):
            bucket = stages.setdefault(d["stage"], {"count": 0, "amount": 0.0})
            bucket["count"] += 1
            bucket["amount"] += float(d["amount"] or 0)
            if d["status"] == "open":
                weighted += float(d["amount"] or 0) * (int(d["probability"] or 0) / 100)
        return {"stages": stages, "weighted_open": round(weighted, 2)}


class InteractionStore:
    """Historique d'activité, alimenté à la main ou par les agents."""

    def __init__(self, db, events=None) -> None:
        self._db = db
        self._events = events

    def add(self, data: dict[str, Any]) -> dict[str, Any]:
        """Enregistre une interaction et en déduit l'intention si absente."""
        now = _now()
        iid = str(data.get("id") or "").strip() or new_id("int")
        text = " ".join(str(data.get(k) or "") for k in ("subject", "summary"))
        intent = {"intent": str(data.get("intent") or ""), "confidence": float(data.get("intent_confidence") or 0)}
        if not intent["intent"]:
            detected = detect_intent(text)
            intent = {"intent": detected["intent"], "confidence": detected["confidence"]}
        fields = (
            iid,
            str(data.get("contact_id") or ""), str(data.get("company_id") or ""), str(data.get("deal_id") or ""),
            str(data.get("kind") or "note"), str(data.get("direction") or ""),
            str(data.get("subject") or "")[:500], str(data.get("summary") or "")[:4000],
            intent["intent"], intent["confidence"], str(data.get("sentiment") or ""),
            str(data.get("agent") or ""), str(data.get("tool") or ""), str(data.get("source_ref") or ""),
            json.dumps(data.get("meta") or {}, ensure_ascii=False),
            float(data.get("occurred_at") or now), now,
        )
        self._db.execute(
            "INSERT INTO crm_interactions(id, contact_id, company_id, deal_id, kind, direction, subject, summary, "
            "intent, intent_confidence, sentiment, agent, tool, source_ref, meta, occurred_at, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", fields)
        contact_id = str(data.get("contact_id") or "")
        if contact_id:
            self._db.execute("UPDATE crm_contacts SET last_interaction_at=? WHERE id=?",
                             (float(data.get("occurred_at") or now), contact_id))
        if self._events is not None:
            try:
                self._events.emit("crm.interaction.added",
                                  {"id": iid, "contact_id": contact_id, "kind": fields[4], "intent": intent["intent"]})
            except Exception:
                pass
        return self.get(iid) or {}

    def get(self, interaction_id: str) -> dict[str, Any] | None:
        row = self._db.one("SELECT * FROM crm_interactions WHERE id=?", (interaction_id,))
        if not row:
            return None
        out = _row(row)
        try:
            out["meta"] = json.loads(out.get("meta") or "{}")
        except Exception:
            out["meta"] = {}
        return out

    def timeline(self, contact_id: str = "", *, limit: int = 100) -> list[dict[str, Any]]:
        if contact_id:
            rows = self._db.query(
                "SELECT id FROM crm_interactions WHERE contact_id=? ORDER BY occurred_at DESC LIMIT ?",
                (contact_id, limit))
        else:
            rows = self._db.query(
                "SELECT id FROM crm_interactions ORDER BY occurred_at DESC LIMIT ?", (limit,))
        return [self.get(r["id"]) or {} for r in rows]

    def delete(self, interaction_id: str) -> bool:
        return bool(self._db.execute("DELETE FROM crm_interactions WHERE id=?", (interaction_id,)).rowcount)


class LeadScorer:
    """Score 0-100 explicable, recalculé à partir des faits enregistrés.

    Les poids sont volontairement simples et visibles : le détail complet est
    stocké dans `crm_contacts.score_detail`, si bien qu'on peut toujours
    répondre à « pourquoi ce lead est à 72 ? ».
    """

    RECENCY_POINTS = ((3, 25), (7, 18), (30, 10), (90, 4))   # (jours, points)

    def __init__(self, db, interactions: InteractionStore, deals: DealStore) -> None:
        self._db = db
        self._interactions = interactions
        self._deals = deals

    def compute(self, contact_id: str) -> dict[str, Any]:
        history = self._interactions.timeline(contact_id, limit=200)
        deals = self._deals.list(contact_id=contact_id, limit=100)
        detail: dict[str, Any] = {}
        score = 0

        # 1. Récence du dernier échange.
        if history:
            age_days = (_now() - float(history[0]["occurred_at"] or 0)) / 86400
            points = 0
            for limit_days, value in self.RECENCY_POINTS:
                if age_days <= limit_days:
                    points = value
                    break
            score += points
            detail["recence"] = {"jours": round(age_days, 1), "points": points}

        # 2. Volume d'échanges (plafonné : 40 mails ne valent pas 40 fois 1).
        volume = min(15, len(history) * 3)
        score += volume
        detail["volume"] = {"interactions": len(history), "points": volume}

        # 3. Réciprocité : un prospect qui répond vaut plus qu'un prospect arrosé.
        inbound = sum(1 for h in history if h["direction"] == "in")
        reciprocity = min(20, inbound * 5)
        score += reciprocity
        detail["reciprocite"] = {"entrants": inbound, "points": reciprocity}

        # 4. Intentions exprimées (la plus forte compte, pas la somme).
        intents = [h["intent"] for h in history if h["intent"]]
        if intents:
            best = max(intents, key=lambda i: INTENT_RULES.get(i, {}).get("weight", 0))
            points = int(INTENT_RULES.get(best, {}).get("weight", 0))
            score += points
            detail["intention"] = {"intention": best, "points": points}

        # 5. Opportunités ouvertes.
        open_deals = [d for d in deals if d["status"] == "open"]
        if open_deals:
            points = min(20, 8 + int(max(d["probability"] or 0 for d in open_deals) / 10))
            score += points
            detail["opportunites"] = {"ouvertes": len(open_deals), "points": points}
        if any(d["status"] == "won" for d in deals):
            score += 10
            detail["client"] = {"points": 10}

        final = max(0, min(100, score))
        detail["total"] = final
        return {"score": final, "detail": detail}

    def apply(self, contact_id: str) -> dict[str, Any]:
        """Calcule, persiste, et met à jour le statut et l'intention du contact."""
        result = self.compute(contact_id)
        history = self._interactions.timeline(contact_id, limit=10)
        intent, confidence = "", 0.0
        for h in history:                       # la plus récente qui porte une intention
            if h["intent"]:
                intent, confidence = h["intent"], float(h["intent_confidence"] or 0)
                break
        status = self._status_for(contact_id, result["score"])
        self._db.execute(
            "UPDATE crm_contacts SET score=?, score_detail=?, intent=?, intent_confidence=?, status=?, updated_at=? "
            "WHERE id=?",
            (result["score"], json.dumps(result["detail"], ensure_ascii=False), intent, confidence,
             status, _now(), contact_id))
        return {**result, "intent": intent, "intent_confidence": confidence, "status": status}

    def _status_for(self, contact_id: str, score: int) -> str:
        if any(d["status"] == "won" for d in self._deals.list(contact_id=contact_id, limit=50)):
            return "client"
        if score >= 55:
            return "prospect"
        # Six mois sans le moindre échange : le lead est inactif, le dire évite
        # de le laisser gonfler les statistiques de pipeline.
        last = self._db.scalar("SELECT last_interaction_at FROM crm_contacts WHERE id=?", (contact_id,))
        if last and (_now() - float(last)) > 180 * 86400:
            return "inactif"
        return "lead"

    def rescore_all(self, limit: int = 500) -> int:
        rows = self._db.query("SELECT id FROM crm_contacts LIMIT ?", (limit,))
        for r in rows:
            self.apply(r["id"])
        return len(rows)


def auto_tags(contact: dict[str, Any]) -> list[str]:
    """Étiquettes dérivées de faits, pas de suppositions."""
    tags: list[str] = []
    score = int(contact.get("score") or 0)
    if score >= 70:
        tags.append("chaud")
    elif score >= 40:
        tags.append("tiede")
    else:
        tags.append("froid")
    if contact.get("intent"):
        tags.append(str(contact["intent"]))
    if contact.get("status") == "client":
        tags.append("client")
    last = contact.get("last_interaction_at")
    if last and (_now() - float(last)) > 60 * 86400:
        tags.append("a-relancer")
    return tags


class CrmPipeline:
    """Façade : un seul objet à brancher sur le cœur (`core.crm_pipeline`)."""

    def __init__(self, db, events=None) -> None:
        self.db = db
        self.companies = CompanyStore(db)
        self.deals = DealStore(db)
        self.interactions = InteractionStore(db, events=events)
        self.scorer = LeadScorer(db, self.interactions, self.deals)

    def log_interaction(self, data: dict[str, Any]) -> dict[str, Any]:
        """Ajoute une interaction PUIS rafraîchit le score du contact concerné.

        C'est le point d'entrée des agents : une seule fonction pour que
        l'historique et le score ne puissent pas diverger.
        """
        interaction = self.interactions.add(data)
        scored = None
        if interaction.get("contact_id"):
            scored = self.scorer.apply(interaction["contact_id"])
        return {"interaction": interaction, "scoring": scored}

    def contact_view(self, contact_id: str) -> dict[str, Any]:
        """Fiche complète : contact, société, opportunités, historique, score."""
        row = self.db.one("SELECT * FROM crm_contacts WHERE id=?", (contact_id,))
        if not row:
            return {}
        contact = _row(row)
        try:
            contact["tags"] = json.loads(contact.get("tags") or "[]")
            contact["score_detail"] = json.loads(contact.get("score_detail") or "{}")
        except Exception:
            contact["tags"], contact["score_detail"] = [], {}
        contact["auto_tags"] = auto_tags(contact)
        return {
            "contact": contact,
            "company": self.companies.get(contact.get("company_id") or "") if contact.get("company_id") else None,
            "deals": self.deals.list(contact_id=contact_id),
            "timeline": self.interactions.timeline(contact_id, limit=50),
        }
