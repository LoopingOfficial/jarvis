"""Planificateur d'upgrade — plan structuré via modèle LLM local (jarvis-astra)."""
from __future__ import annotations

import json
import re
from typing import Any

from .ollama import OllamaClient
from .inspector import ProjectInspector

PLANNER_SYSTEM = (
    "Tu es le planificateur d'auto-amélioration de JARVIS. Tu produis un plan "
    "PRÉCIS et RÉALISABLE en une seule réponse JSON. Tu ne modifies jamais de "
    "fichier : tu planifies uniquement.\n\n"
    "Réponds SEULEMENT avec un objet JSON valide au format :\n"
    '{"objective":"…","target_files":["…"],"steps":[{"action":"create|modify|delete|test","file":"…",'
    '"description":"…"},…],"tests_to_run":["python -m unittest …","…"],"verification":"…",'
    '"risks":["…"]}\n'
    "target_files doivent être des chemins relatifs (ex. jarvis/server.py, ui/js/self_upgrades.js, "
    "tests/test_truc.py). Les tests doivent utiliser unittest (discover -s tests)."
)


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return None


class UpgradePlanner:
    def __init__(self, project_root: Any, ollama_url: str = "http://127.0.0.1:11434",
                 orchestrator_model: str = "jarvis-astra") -> None:
        self._project_root = project_root
        self._client = OllamaClient(ollama_url)
        self._model = orchestrator_model
        self._inspector = ProjectInspector(project_root)

    def _context_summary(self) -> str:
        files = self._inspector.list_files("**/*.py")
        rel = files if len(files) < 200 else files[:200]
        return (
            "Projet JARVIS (Python + vanille JS SPA, pas de build). "
            f"Structure principale: jarvis/ (serveur HTTP + core), ui/ (interface), tests/ (unittest). "
            f"Fichiers Python: {len(files)}. Exemples: {', '.join(rel[:25])}"
        )

    def plan(self, prompt: str, mode: str = "PLAN") -> dict[str, Any]:
        messages = [
            {"role": "system", "content": PLANNER_SYSTEM},
            {"role": "user", "content": (
                f"MODE: {mode}\n\n{self._context_summary()}\n\n"
                f'DEMANDE DE L\'OPÉRATEUR:\n{prompt}\n\n'
                "Produis le plan JSON uniquement."
            )},
        ]
        resp = self._client.chat(self._model, messages, temperature=0.2, max_tokens=3000)
        raw = resp.get("text", "")
        plan = _extract_json(raw) or {"objective": prompt, "target_files": [],
                                      "steps": [], "tests_to_run": [], "verification": "",
                                      "risks": [], "note": "Plan générique (JSON manquant)."}
        plan.setdefault("objective", prompt)
        plan.setdefault("target_files", [])
        plan.setdefault("steps", [])
        plan.setdefault("tests_to_run", [])
        plan.setdefault("verification", "")
        plan.setdefault("risks", [])
        return {
            "model": self._model,
            "raw": raw[:6000],
            "plan": plan,
            "ok": bool(plan.get("steps") or plan.get("target_files")),
        }