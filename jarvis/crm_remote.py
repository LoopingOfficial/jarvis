"""Pont entre le carnet local (`crm.py`) et l'API CRM distante (`crm_api/`).

Pourquoi un pont plutôt qu'une bascule
--------------------------------------
Le carnet local est en SQLite, dans le processus : il répond en microsecondes
et fonctionne hors-ligne. L'API est distante et partagée. Remplacer l'un par
l'autre rendrait l'assistant muet dès que le réseau tombe — or « qui est
Martin ? » doit rester répondable. Le local reste donc la source de lecture
immédiate, et le distant la mémoire partagée vers laquelle on pousse.

`urllib` plutôt que `requests` : le reste du projet fait déjà ainsi
(blog_site.py, blog_sources.py) et le pont n'introduit aucune dépendance.

Correspondance des champs
-------------------------
Les deux schémas ont divergé. `name` ici, `full_name` là-bas ; `vat_number`
n'existe que localement (l'API est un CRM générique) et n'est donc PAS
synchronisé — mieux vaut un champ absent qu'un numéro de TVA rangé dans un
champ « notes » où personne n'ira le chercher.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_TIMEOUT = 10.0

# local -> distant. L'inverse s'en déduit.
_TO_REMOTE = {
    "name": "full_name",
    "company": "company",
    "email": "email",
    "phone": "phone",
    "address": "address",
    "notes": "notes",
}
_TO_LOCAL = {v: k for k, v in _TO_REMOTE.items()}


class CrmRemoteError(RuntimeError):
    """Panne du pont. Distincte des erreurs locales : l'appelant doit pouvoir
    répondre « le CRM distant est injoignable » sans laisser croire que le
    contact n'existe pas."""


def to_remote(contact: dict[str, Any]) -> dict[str, Any]:
    """Fiche locale -> corps accepté par `POST /contacts`.

    Les chaînes vides sont retirées : le carnet local stocke "" pour un champ
    non renseigné, l'API attend `null` ou l'absence du champ (une chaîne vide
    échouerait la validation EmailStr sur `email`).
    """
    out: dict[str, Any] = {}
    for local_key, remote_key in _TO_REMOTE.items():
        value = contact.get(local_key)
        if value is None:
            continue
        value = str(value).strip()
        if value:
            out[remote_key] = value
    return out


def to_local(payload: dict[str, Any]) -> dict[str, Any]:
    """Fiche distante -> forme attendue par `CrmStore.upsert`.

    L'identifiant distant est un entier auto-incrémenté, le local une chaîne
    « ct_… » : ils ne sont pas interchangeables et l'`id` n'est pas reporté.
    Il est exposé à part, sous `remote_id`.
    """
    out: dict[str, Any] = {}
    for remote_key, local_key in _TO_LOCAL.items():
        value = payload.get(remote_key)
        if value is not None:
            out[local_key] = str(value)
    if payload.get("id") is not None:
        out["remote_id"] = payload["id"]
    return out


class CrmRemoteClient:
    """Client HTTP minimal de l'API CRM.

    Configuré par l'environnement (`CRM_API_URL`, `CRM_API_KEY`) : l'URL et la
    clé varient d'une machine à l'autre et n'ont rien à faire dans le dépôt.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = (base_url or os.getenv("CRM_API_URL", "")).strip().rstrip("/")
        self.api_key = (api_key or os.getenv("CRM_API_KEY", "")).strip()
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    # -- transport --------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        if not self.configured:
            raise CrmRemoteError(
                "CRM distant non configuré : renseignez CRM_API_URL et CRM_API_KEY."
            )
        url = f"{self.base_url}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v not in (None, "")}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)

        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("X-API-Key", self.api_key)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                if response.status == 204 or not raw:
                    return None
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Le corps d'erreur de FastAPI contient `detail`, bien plus parlant
            # qu'un « HTTP 409 » nu pour l'utilisateur comme pour le modèle.
            detail = ""
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
            except Exception:
                pass
            raise CrmRemoteError(
                f"CRM distant : HTTP {exc.code}" + (f" — {detail}" if detail else "")
            ) from exc
        except urllib.error.URLError as exc:
            raise CrmRemoteError(f"CRM distant injoignable : {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise CrmRemoteError("CRM distant : réponse illisible (JSON attendu).") from exc

    # -- contacts ---------------------------------------------------------
    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health") or {}

    def search(self, query: str, limit: int = 20) -> dict[str, Any]:
        return self._request("GET", "/contacts", params={"q": query, "limit": limit}) or {}

    def get(self, remote_id: int) -> dict[str, Any]:
        return self._request("GET", f"/contacts/{int(remote_id)}") or {}

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/contacts", body=payload) or {}

    def update(self, remote_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", f"/contacts/{int(remote_id)}", body=payload) or {}

    def push_contact(self, contact: dict[str, Any]) -> dict[str, Any]:
        """Envoie une fiche locale, en mettant à jour celle qui existe déjà.

        L'e-mail sert de clé de rapprochement : c'est la seule donnée unique
        que partagent les deux bases (les identifiants, eux, sont propres à
        chacune). Sans e-mail, on crée — quitte à produire un homonyme, ce qui
        est moins grave que d'écraser la fiche d'un autre client.
        """
        payload = to_remote(contact)
        if not payload.get("full_name"):
            raise CrmRemoteError("Le nom du contact est requis pour l'envoi au CRM distant.")

        email = payload.get("email")
        if email:
            found = self.search(email, limit=5).get("items") or []
            existing = next(
                (c for c in found if (c.get("email") or "").lower() == email.lower()), None
            )
            if existing:
                return {"action": "updated", "contact": self.update(existing["id"], payload)}
        return {"action": "created", "contact": self.create(payload)}

    # -- interactions -----------------------------------------------------
    def add_interaction(
        self,
        remote_id: int,
        *,
        kind: str = "note",
        subject: str | None = None,
        content: str | None = None,
        author: str = "jarvis",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"kind": kind, "author": author}
        if subject:
            body["subject"] = subject
        if content:
            body["content"] = content
        # `occurred_at` est volontairement omis : l'API date côté serveur, ce
        # qui évite qu'une horloge locale décalée fausse la chronologie.
        return self._request(
            "POST", f"/contacts/{int(remote_id)}/interactions", body=body
        ) or {}

    def interactions(self, kind: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return self._request(
            "GET", "/interactions", params={"kind": kind, "limit": limit}
        ) or []
