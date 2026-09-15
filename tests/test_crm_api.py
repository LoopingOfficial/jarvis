"""Tests de l'API CRM, sur SQLite en mémoire.

La base réelle est un MySQL distant : la joindre depuis les tests les rendrait
lents et dépendants du réseau. SQLite suffit ici parce qu'on vérifie le
contrat HTTP (codes, formes de réponse, règles métier), pas le dialecte SQL.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("pymysql")

# Doit précéder l'import du paquet : config.py refuse de démarrer sans ces
# variables, et database.py construit le moteur dès l'import.
os.environ.setdefault("DB_NAME", "test_crm")
os.environ.setdefault("DB_USER", "test_user")
os.environ.setdefault("DB_PASSWORD", "test_password")
os.environ.setdefault("CRM_API_KEY", "cle-de-test")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from crm_api.database import Base, get_db  # noqa: E402
from crm_api.main import app  # noqa: E402

KEY = {"X-API-Key": "cle-de-test"}


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        # StaticPool : sans lui, chaque connexion ouvrirait une base en mémoire
        # distincte et les tables créées ici seraient invisibles des requêtes.
        poolclass=StaticPool,
    )

    # SQLite ignore les clés étrangères sauf si on l'active par connexion ;
    # sans ce PRAGMA, le ON DELETE CASCADE de la table `interactions` ne
    # s'appliquerait pas et le test de suppression passerait à côté du bug.
    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_conn, _record):  # pragma: no cover - hook SQLite
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


def _create(client, **kw):
    payload = {"full_name": "Frédéric Martin", "email": "f.martin@example.com"}
    payload.update(kw)
    return client.post("/contacts", json=payload, headers=KEY)


def test_health_is_public(client):
    assert client.get("/health").status_code == 200


def test_routes_require_api_key(client):
    assert client.get("/contacts").status_code == 401
    assert client.get("/contacts", headers={"X-API-Key": "mauvaise"}).status_code == 401


def test_create_and_read_contact(client):
    r = _create(client, company="ACME")
    assert r.status_code == 201, r.text
    contact_id = r.json()["id"]

    detail = client.get(f"/contacts/{contact_id}", headers=KEY).json()
    assert detail["full_name"] == "Frédéric Martin"
    assert detail["status"] == "prospect"
    assert detail["interactions"] == []


def test_duplicate_email_is_rejected(client):
    _create(client)
    assert _create(client, full_name="Homonyme").status_code == 409


def test_unknown_contact_is_404(client):
    assert client.get("/contacts/9999", headers=KEY).status_code == 404


def test_extra_field_is_refused(client):
    r = client.post(
        "/contacts",
        json={"full_name": "Test", "champ_invente": "x"},
        headers=KEY,
    )
    assert r.status_code == 422


def test_blank_strings_become_null(client):
    r = client.post(
        "/contacts", json={"full_name": "Sans société", "company": "N/A"}, headers=KEY
    )
    assert r.status_code == 201
    assert r.json()["company"] is None


def test_partial_update_keeps_other_fields(client):
    contact_id = _create(client, company="ACME").json()["id"]
    r = client.patch(
        f"/contacts/{contact_id}", json={"status": "client"}, headers=KEY
    )
    assert r.status_code == 200
    assert r.json()["status"] == "client"
    assert r.json()["company"] == "ACME"


def test_search_and_pagination(client):
    _create(client, company="ACME")
    _create(client, full_name="Alice Durand", email="alice@example.com", company="Globex")

    body = client.get("/contacts", params={"q": "ACME"}, headers=KEY).json()
    assert body["total"] == 1
    assert body["items"][0]["company"] == "ACME"

    page = client.get("/contacts", params={"limit": 1}, headers=KEY).json()
    assert page["total"] == 2 and len(page["items"]) == 1


def test_interactions_flow(client):
    contact_id = _create(client).json()["id"]
    r = client.post(
        f"/contacts/{contact_id}/interactions",
        json={"kind": "call", "subject": "Rappel devis"},
        headers=KEY,
    )
    assert r.status_code == 201, r.text
    # occurred_at absent : le serveur doit le dater lui-même.
    assert r.json()["occurred_at"]

    fil = client.get(f"/contacts/{contact_id}/interactions", headers=KEY).json()
    assert len(fil) == 1

    transverse = client.get("/interactions", params={"kind": "call"}, headers=KEY).json()
    assert len(transverse) == 1
    assert client.get("/interactions", params={"kind": "email"}, headers=KEY).json() == []


def test_delete_cascades_interactions(client):
    contact_id = _create(client).json()["id"]
    client.post(
        f"/contacts/{contact_id}/interactions", json={"content": "note"}, headers=KEY
    )
    assert client.delete(f"/contacts/{contact_id}", headers=KEY).status_code == 204
    assert client.get(f"/contacts/{contact_id}", headers=KEY).status_code == 404
    assert client.get("/interactions", headers=KEY).json() == []
