"""Deterministic, schema-first mission planner for Operator V1."""

from __future__ import annotations

from typing import List

from .mission import Mission, MissionTask


class MissionPlanner:
    def plan(self, mission: Mission) -> List[MissionTask]:
        request = mission.user_request.strip()
        lower = request.lower()
        wants_change = any(word in lower for word in ("corrige", "fix", "implémente", "implement", "modifie", "change"))
        tasks = [
            MissionTask("inspect", "Inspecter le repository", "inspect", "Inspecte la structure et les fichiers pertinents du repository.", required_capabilities=["tools"]),
            MissionTask("analyze", "Identifier la cause racine", "llm", request, ["inspect"], ["reasoning"]),
        ]
        if wants_change:
            tasks.append(MissionTask("code", "Produire le CodeChangeSet", "code_change", "Produis uniquement un CodeChangeSet JSON strict et vérifiable pour la demande utilisateur.", ["analyze"], ["coding", "tools"]))
            test_dependencies = ["code"]
            review_dependencies = ["code"]
        else:
            test_dependencies = ["analyze"]
            review_dependencies = ["analyze"]
        tasks.extend([
            MissionTask("test", "Exécuter les tests", "test", "Exécute les tests disponibles et collecte les preuves.", test_dependencies, ["testing", "tools"]),
            MissionTask("review", "Revoir les résultats", "review", "Vérifie la cohérence de l'analyse et des preuves.", review_dependencies, ["review"]),
            MissionTask("synthesis", "Synthétiser le résultat", "llm", "Présente le résultat, les limites et les preuves.", ["test", "review"], ["reasoning"]),
        ])
        return tasks
