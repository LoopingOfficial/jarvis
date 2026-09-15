"""CRM local : contacts, recherche, et levée d'ambiguïté.

Stockage
--------
Table `crm_contacts` de la base SQLite existante (jarvis/db.py). Les contacts
sont une donnée que l'utilisateur ÉCRIT, pas seulement qu'il lit : un fichier
JSON en lecture seule n'aurait pas suffi. Un `data/crm_seed.json` amorce
néanmoins la table si elle est vide, pour que la recherche soit testable
hors-ligne dès la première ouverture.

Ambiguïté
---------
Le cœur de ce module. Quand « Martin » désigne deux clients, on ne devine pas :
la recherche renvoie un statut `ambiguous` accompagné des options réellement
trouvées, et c'est l'utilisateur qui tranche. Deviner ici produirait une
facture adressée au mauvais client — exactement le genre d'erreur qu'une
confirmation ne rattrape pas, puisque l'utilisateur confirmerait un nom qui
lui semble correct.
"""
from __future__ import annotations

import json
import time
import unicodedata
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .db import new_id

SEED_FILE = Path(DATA_DIR) / "crm_seed.json"

FOUND = "found"
AMBIGUOUS = "ambiguous"
NOT_FOUND = "not_found"


def fold(text: Any) -> str:
    """Minuscules sans accents : « Frédéric » et « frederic » se rejoignent."""
    s = str(text or "").lower()
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _row_to_contact(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def label_of(contact: dict[str, Any]) -> str:
    """« Martin Dupont — L'Atelier » : ce qui permet de distinguer deux homonymes."""
    name = str(contact.get("name") or "").strip()
    company = str(contact.get("company") or "").strip()
    return f"{name} — {company}" if company else name


class CrmStore:
    def __init__(self, db) -> None:
        self._db = db
        self._seeded = False

    # -- amorçage ---------------------------------------------------------
    def seed_if_empty(self, path: Path | None = None) -> int:
        """Charge le jeu de démonstration UNIQUEMENT si la table est vide.

        On n'écrase jamais des contacts existants : l'amorçage sert au premier
        démarrage et aux tests, pas à réinitialiser le carnet de l'utilisateur.
        """
        if self.count():
            return 0
        source = Path(path or SEED_FILE)
        if not source.is_file():
            return 0
        raw = json.loads(source.read_text(encoding="utf-8"))
        items = raw.get("contacts", raw) if isinstance(raw, dict) else raw
        added = 0
        for item in items:
            if isinstance(item, dict) and item.get("name"):
                self.upsert(item)
                added += 1
        return added

    def count(self) -> int:
        return int(self._db.scalar("SELECT COUNT(*) FROM crm_contacts") or 0)

    # -- écriture ---------------------------------------------------------
    def upsert(self, contact: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        cid = str(contact.get("id") or "").strip() or new_id("ct")
        existing = self.get(cid)
        fields = {
            "name": str(contact.get("name") or "").strip(),
            "company": str(contact.get("company") or "").strip(),
            "email": str(contact.get("email") or "").strip(),
            "phone": str(contact.get("phone") or "").strip(),
            "address": str(contact.get("address") or "").strip(),
            "vat_number": str(contact.get("vat_number") or "").strip(),
            "notes": str(contact.get("notes") or "").strip(),
        }
        if existing:
            self._db.execute(
                "UPDATE crm_contacts SET name=?, company=?, email=?, phone=?, address=?, "
                "vat_number=?, notes=?, updated_at=? WHERE id=?",
                (*fields.values(), now, cid))
        else:
            self._db.execute(
                "INSERT INTO crm_contacts(id, name, company, email, phone, address, vat_number, "
                "notes, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (cid, *fields.values(), now, now))
        return self.get(cid) or {"id": cid, **fields}

    def delete(self, contact_id: str) -> bool:
        cur = self._db.execute("DELETE FROM crm_contacts WHERE id=?", (contact_id,))
        return bool(cur.rowcount)

    # -- lecture ----------------------------------------------------------
    def get(self, contact_id: str) -> dict[str, Any] | None:
        row = self._db.one("SELECT * FROM crm_contacts WHERE id=?", (contact_id,))
        return _row_to_contact(row) if row else None

    def all(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._db.query("SELECT * FROM crm_contacts ORDER BY name LIMIT ?", (limit,))
        return [_row_to_contact(r) for r in rows]

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Recherche tolérante : accents ignorés, sur nom, société et e-mail.

        Le filtrage se fait en Python après un SELECT large, parce que SQLite
        ne replie pas les accents : un LIKE sur « Frederic » raterait
        « Frédéric ». Le carnet est local et petit, le coût est négligeable.
        """
        needle = fold(query).strip()
        if not needle:
            return []
        scored: list[tuple[int, str, dict[str, Any]]] = []
        for contact in self.all(limit=500):
            name, company = fold(contact.get("name")), fold(contact.get("company"))
            email = fold(contact.get("email"))
            if needle == name:
                rank = 0                      # correspondance exacte du nom
            elif name.startswith(needle) or company.startswith(needle):
                rank = 1
            elif needle in name or needle in company or needle in email:
                rank = 2
            else:
                continue
            scored.append((rank, name, contact))
        scored.sort(key=lambda t: (t[0], t[1]))
        return [c for _, _, c in scored[:limit]]

    # -- recherche avec levée d'ambiguïté ---------------------------------
    def resolve(self, query: str) -> dict[str, Any]:
        """Renvoie un statut exploitable par l'agent ET par l'interface.

        - `found`      : un seul contact correspond, on peut continuer.
        - `ambiguous`  : plusieurs correspondent — on rend les options et on
                         s'arrête. Aucune heuristique de « meilleur candidat »
                         ici : choisir à la place de l'utilisateur, c'est
                         risquer d'adresser un devis au mauvais client.
        - `not_found`  : rien. On le dit, on n'invente pas de fiche.
        """
        matches = self.search(query)
        if not matches:
            return {"status": NOT_FOUND, "query": query, "matches": []}
        # Une correspondance exacte et unique sur le nom lève l'ambiguïté :
        # « Martin Dupont » est sans équivoque même si « Martin Roy » existe.
        exact = [c for c in matches if fold(c.get("name")) == fold(query)]
        if len(exact) == 1:
            return {"status": FOUND, "query": query, "contact": exact[0], "matches": matches}
        if len(matches) == 1:
            return {"status": FOUND, "query": query, "contact": matches[0], "matches": matches}
        return {
            "status": AMBIGUOUS, "query": query, "matches": matches,
            "options": [{"id": c["id"], "label": label_of(c),
                         "company": c.get("company", ""), "email": c.get("email", "")}
                        for c in matches],
        }


def clarification_question(result: dict[str, Any]) -> str:
    """Question directe, courte, utilisable telle quelle à l'oral."""
    options = result.get("options") or []
    if len(options) == 2:
        return (f"Deux contacts correspondent à « {result.get('query')} » : "
                f"{options[0]['label']} ou {options[1]['label']} . Lequel ?")
    labels = ", ".join(o["label"] for o in options[:5])
    return (f"Plusieurs contacts correspondent à « {result.get('query')} » : "
            f"{labels}. Lequel veux-tu ?")
