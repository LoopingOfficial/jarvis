"""Tests du pont CRM (jarvis/crm_remote.py).

Un faux serveur HTTP local remplace l'API : on veut vérifier ce que le client
envoie et comment il réagit aux réponses, pas refaire les tests de crm_api.
Rien ne sort de la machine.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from jarvis.crm_remote import CrmRemoteClient, CrmRemoteError, to_local, to_remote

KEY = "cle-de-test"


class _Handler(BaseHTTPRequestHandler):
    """Répond selon `routes`, un dict {(méthode, chemin): (code, corps)}."""

    routes: dict = {}
    seen: list = []

    def log_message(self, *args):  # silence le journal stderr du serveur
        pass

    def _respond(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        type(self).seen.append({
            "method": self.command, "path": self.path, "body": body,
            "api_key": self.headers.get("X-API-Key"),
        })

        if self.headers.get("X-API-Key") != KEY:
            payload, status = {"detail": "Clé d'API absente ou invalide."}, 401
        else:
            status, payload = type(self).routes.get(
                (self.command, path), (404, {"detail": "route de test absente"})
            )

        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = do_POST = do_PATCH = _respond


@pytest.fixture()
def server():
    _Handler.routes, _Handler.seen = {}, []
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd, _Handler
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture()
def client(server):
    httpd, _ = server
    host, port = httpd.server_address
    return CrmRemoteClient(base_url=f"http://{host}:{port}", api_key=KEY, timeout=5)


# -- correspondance des champs -------------------------------------------

def test_to_remote_renames_and_drops_empty():
    payload = to_remote({"name": "Frédéric Martin", "company": "", "email": " f@x.fr ",
                         "vat_number": "FR123"})
    assert payload == {"full_name": "Frédéric Martin", "email": "f@x.fr"}
    # vat_number n'a pas d'équivalent distant : il ne doit pas fuiter ailleurs.
    assert "vat_number" not in payload
    assert "FR123" not in json.dumps(payload)


def test_to_local_keeps_ids_separate():
    local = to_local({"id": 7, "full_name": "Alice", "email": "a@x.fr"})
    assert local["name"] == "Alice"
    assert local["remote_id"] == 7
    # L'id distant est un entier, le local une chaîne « ct_… » : les confondre
    # ferait écraser une fiche locale au hasard.
    assert "id" not in local


# -- configuration --------------------------------------------------------

def test_unconfigured_client_fails_clearly():
    with pytest.raises(CrmRemoteError, match="non configuré"):
        CrmRemoteClient(base_url="", api_key="").search("martin")


# -- transport ------------------------------------------------------------

def test_search_sends_api_key(client, server):
    _, handler = server
    handler.routes[("GET", "/contacts")] = (200, {"total": 1, "limit": 20, "offset": 0,
                                                  "items": [{"id": 3, "full_name": "Martin"}]})
    body = client.search("martin")
    assert body["total"] == 1
    assert handler.seen[-1]["api_key"] == KEY
    assert "q=martin" in handler.seen[-1]["path"]


def test_http_error_detail_is_surfaced(client, server):
    _, handler = server
    handler.routes[("POST", "/contacts")] = (409, {"detail": "Un contact existe déjà."})
    with pytest.raises(CrmRemoteError, match="existe déjà"):
        client.create({"full_name": "Doublon"})


def test_bad_key_is_reported_as_401(server):
    httpd, handler = server
    host, port = httpd.server_address
    bad = CrmRemoteClient(base_url=f"http://{host}:{port}", api_key="mauvaise", timeout=5)
    with pytest.raises(CrmRemoteError, match="401"):
        bad.search("martin")


def test_unreachable_server_is_not_a_crash():
    # Port fermé : l'erreur doit rester un CrmRemoteError lisible, pour que
    # l'assistant dise « CRM injoignable » et non « contact inexistant ».
    orphan = CrmRemoteClient(base_url="http://127.0.0.1:9", api_key=KEY, timeout=2)
    with pytest.raises(CrmRemoteError, match="injoignable"):
        orphan.search("martin")


# -- rapprochement par e-mail --------------------------------------------

def test_push_creates_when_email_unknown(client, server):
    _, handler = server
    handler.routes[("GET", "/contacts")] = (200, {"total": 0, "limit": 5, "offset": 0, "items": []})
    handler.routes[("POST", "/contacts")] = (201, {"id": 12, "full_name": "Alice Durand"})

    result = client.push_contact({"name": "Alice Durand", "email": "alice@x.fr"})
    assert result["action"] == "created"
    assert result["contact"]["id"] == 12


def test_push_updates_when_email_already_there(client, server):
    _, handler = server
    handler.routes[("GET", "/contacts")] = (
        200, {"total": 1, "limit": 5, "offset": 0,
              "items": [{"id": 12, "full_name": "Alice", "email": "ALICE@x.fr"}]},
    )
    handler.routes[("PATCH", "/contacts/12")] = (200, {"id": 12, "full_name": "Alice Durand"})

    # La casse de l'e-mail diffère : la comparer sans replier créerait un doublon.
    result = client.push_contact({"name": "Alice Durand", "email": "alice@x.fr"})
    assert result["action"] == "updated"
    assert handler.seen[-1]["method"] == "PATCH"


def test_push_without_name_is_refused(client):
    with pytest.raises(CrmRemoteError, match="nom du contact"):
        client.push_contact({"email": "sans.nom@x.fr"})


# -- interactions ---------------------------------------------------------

def test_interaction_lets_server_set_the_date(client, server):
    _, handler = server
    handler.routes[("POST", "/contacts/12/interactions")] = (201, {"id": 1, "kind": "call"})
    client.add_interaction(12, kind="call", subject="Rappel devis")

    sent = handler.seen[-1]["body"]
    assert sent == {"kind": "call", "author": "jarvis", "subject": "Rappel devis"}
    # Pas d'occurred_at : une horloge locale décalée fausserait la chronologie.
    assert "occurred_at" not in sent
