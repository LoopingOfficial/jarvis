"""E2E ProjectStatus — la phrase exacte qui produisait CSS_NON_NATIVE."""
from __future__ import annotations

import time

import pytest

from jarvis.orchestrator import Orchestrator
from jarvis.project_status import ProjectStatusService, detect_project_status
from jarvis.validation import ValidationEngine

FAILING_PHRASE = "Fais le point sur mes tâches en cours et ce qui bloque."


# -- 1. Régression du bug d'origine --------------------------------------
def test_css_validator_not_applied_to_plain_requests():
    """Un récap n'est plus soumis au validateur CSS (source du CSS_NON_NATIVE)."""
    spec = Orchestrator._validation_spec(FAILING_PHRASE)
    assert "css" not in spec["validators"]


def test_prose_with_braces_no_longer_fails():
    prose = "Tâches en cours :\n- Refonte (config {theme: {dark}})\n- Budget $500"
    verdict = ValidationEngine().validate(prose, Orchestrator._validation_spec(FAILING_PHRASE))
    assert verdict.ok, verdict.code


def test_css_validator_still_guards_real_css():
    spec = Orchestrator._validation_spec("Génère le CSS du bouton")
    assert spec["validators"] == ["format", "css"]
    verdict = ValidationEngine().validate("a{color:darken($x,5%)}", spec)
    assert not verdict.ok and verdict.code == "CSS_NON_NATIVE"


# -- 2. Détection d'intention --------------------------------------------
@pytest.mark.parametrize("phrase", [
    FAILING_PHRASE,
    "Fais le point sur mes tâches",
    "Où en sont mes projets ?",
    "Qu'est-ce qui bloque ?",
    "Que reste-t-il à faire ?",
    "Mes tâches en cours",
    "État d'avancement des projets",
])
def test_detect_project_status_true(phrase):
    assert detect_project_status(phrase)


@pytest.mark.parametrize("phrase", [
    "Génère une image de chat",
    "Crée une tâche pour demain",
    "Analyse ce fichier google_sheets.py",
    "Bonjour JARVIS",
])
def test_detect_project_status_false(phrase):
    assert not detect_project_status(phrase)


# -- 3. Collecte ancrée sur de vraies lignes de base ----------------------
class _FakeAgents:
    def list(self):
        return [{"id": "jarvis", "status": "active", "current_action": "Correction google_sheets.py",
                 "last_error": ""}]


class _FakeCore:
    def __init__(self, db):
        self.db = db
        self.agents = _FakeAgents()


@pytest.fixture()
def core(tmp_path):
    from jarvis.db import Database
    db = Database(tmp_path / "t.db")
    now = time.time()
    db.execute(
        "INSERT INTO tasks (id,name,kind,status,progress,agent,meta,created_at,started_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("task_a", "Sync Brainrot", "sync", "running", 0.4, "jarvis",
         '{"project": "Brainrot"}', now - 600, now - 600))
    db.execute(
        "INSERT INTO tasks (id,name,kind,status,progress,agent,meta,error,created_at,completed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("task_b", "Import Sheet", "sheet", "failed", 0.2, "jarvis",
         '{"project": "Brainrot"}', "HTTP 403 sur l'API", now - 900, now - 800))
    db.execute(
        "INSERT INTO memories (id,scope,content,importance,project,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("mem_a", "project", "Prochaine étape : valider les 7 CREATE restants.",
         3, "Brainrot", now - 300, now - 300))
    return _FakeCore(db)


def test_collect_is_grounded_in_real_rows(core):
    data = ProjectStatusService(core).collect()
    assert data["grounded"] is True
    assert data["counts"]["active_tasks"] == 1
    assert data["counts"]["failed_recent"] == 1
    brainrot = next(p for p in data["projects"] if p["project"] == "Brainrot")
    assert brainrot["state"] == "bloqué"
    assert any("403" in b for b in brainrot["blockers"])
    assert "7 CREATE" in brainrot["next_action"]
    assert "task:task_a" in brainrot["sources"]


def test_render_uses_the_required_sections(core):
    text = ProjectStatusService(core).render()
    for section in ("PROJET", "ÉTAT", "DERNIÈRE AVANCÉE", "BLOCAGE", "PROCHAINE ACTION"):
        assert section in text, section
    assert "Brainrot" in text
    assert "403" in text
    # Aucune trace du bug d'origine.
    assert "CSS" not in text


def test_render_when_nothing_is_recorded(tmp_path):
    from jarvis.db import Database
    empty = ProjectStatusService(_FakeCore(Database(tmp_path / "empty.db")))
    text = empty.render()
    assert "Aucun projet" in text
    # Ne fabrique pas de projets imaginaires.
    assert "PROJET" not in text


def test_render_output_passes_validation(core):
    """Le récap ne doit pas être rejeté par la chaîne de validation."""
    text = ProjectStatusService(core).render()
    spec = Orchestrator._validation_spec(FAILING_PHRASE)
    assert ValidationEngine().validate(text, spec).ok
