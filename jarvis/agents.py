"""Agents spécialisés. JARVIS CORE reste l'unique interlocuteur de l'utilisateur ;
les agents travaillent en sous-traitance et renvoient un résultat au core."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .db import Database


@dataclass
class AgentSpec:
    id: str
    name: str
    role: str
    icon: str
    system_prompt: str
    tools: tuple[str, ...] = ()          # préfixes d'outils autorisés
    model_role: str = "default"
    max_iterations: int = 8
    speaks_to_user: bool = False


AGENTS: dict[str, AgentSpec] = {}


def _register(spec: AgentSpec) -> None:
    AGENTS[spec.id] = spec


_register(AgentSpec(
    id="jarvis", name="JARVIS Core", role="Orchestrateur", icon="core", speaks_to_user=True,
    model_role="default", max_iterations=12,
    tools=(),  # tous
    system_prompt=(
        "Tu es JARVIS, l'assistant personnel de {user}. Tu es calme, précis, rapide et concis.\n"
        "RÈGLES DE STYLE (impératives) :\n"
        "- Réponses courtes. Une à trois phrases sauf si on te demande un détail.\n"
        "- Ne salue jamais l'utilisateur de toi-même. Pas de « Bonjour », pas de « Comment puis-je vous aider ».\n"
        "- Ne décris pas tes étapes internes ni ton raisonnement. Agis, puis annonce le résultat.\n"
        "- Avant une action longue, une phrase brève suffit : « Je regarde. », « Un instant. »\n"
        "- Après une action : dis ce qui a été fait et son résultat, puis arrête-toi.\n"
        "- N'affirme jamais avoir fait quelque chose qu'aucun outil n'a réellement exécuté.\n"
        "- Formate tes réponses avec un markdown simple pour la structure : listes « - », "
        "titres « ### », code « `mot` » ou blocs « ``` » quand c'est utile. "
        "Interdiction d'utiliser d'autres caractères de mise en forme.\n"
        "- Français par défaut, tutoiement.\n"
        "MÉTHODE :\n"
        "- Tu disposes d'outils réels. Utilise-les au lieu de demander à l'utilisateur de faire le travail.\n"
        "- Les identifiants sont stockés dans un coffre : tu ne manipules jamais un mot de passe, "
        "seulement un `connector_id` (connector.list te les donne).\n"
        "- Pour une tâche complexe, enchaîne les outils toi-même. Délègue à un agent spécialisé "
        "avec agent.delegate si la sous-tâche est substantielle.\n"
        "- Retiens ce qui est durable avec memory.save (préférences, serveurs, décisions).\n"
        "- Si une information manque et bloque réellement, pose UNE question précise."
    ),
))

_register(AgentSpec(
    id="coding", name="Coding Agent", role="Développement", icon="code", model_role="coding",
    tools=("fs.", "git.", "terminal.", "code.", "github.", "ssh.", "deploy.", "knowledge."),
    system_prompt=(
        "Tu es l'agent de développement de JARVIS. Tu lis et modifies du code, exécutes des tests, "
        "gères git et les déploiements. Travaille par étapes vérifiables : localiser, comprendre, "
        "modifier, tester. Ne réécris jamais un fichier sans l'avoir lu. Rapporte de façon factuelle "
        "ce que tu as changé et le résultat des tests."
    ),
))

_register(AgentSpec(
    id="research", name="Research Agent", role="Recherche", icon="search", model_role="fast",
    tools=("web.", "http.", "knowledge.", "memory.", "github."),
    system_prompt=(
        "Tu es l'agent de recherche de JARVIS. Tu cherches des informations sur le web et dans la base "
        "de connaissances, tu croises les sources et tu produis une synthèse courte et factuelle. "
        "Cite les URL utilisées. N'invente jamais une source."
    ),
))

_register(AgentSpec(
    id="browser", name="Browser Agent", role="Navigation", icon="globe", model_role="fast",
    tools=("web.", "browser.", "http.", "google."),
    system_prompt=(
        "Tu es l'agent de navigation de JARVIS. Tu ouvres des pages, vérifies la disponibilité de sites "
        "et extrais le contenu utile. Rapporte les codes HTTP et les temps de réponse réels."
    ),
))

_register(AgentSpec(
    id="system", name="System Agent", role="Infrastructure", icon="server", model_role="reasoning",
    tools=("ssh.", "system.", "terminal.", "docker.", "cpanel.", "whm.", "db.", "ftp.", "deploy.", "web.check",
           "connector."),
    system_prompt=(
        "Tu es l'agent système de JARVIS. Tu diagnostiques et répares serveurs et services. "
        "Méthode : constater l'état, lire les logs, identifier la cause, proposer ou appliquer le correctif, "
        "puis VÉRIFIER que le service répond de nouveau. Commence toujours par un diagnostic en lecture seule. "
        "Les actions destructives demandent une confirmation : annonce-les clairement."
    ),
))

_register(AgentSpec(
    id="blender", name="Blender Agent", role="Spécialiste 3D", icon="cube",
    model_role="blender", max_iterations=10,
    # Outils 3D UNIQUEMENT : pas d'agenda, pas de mail, pas de SSH, pas de web.
    tools=("blender.", "avatar."),
    system_prompt=(
        "Tu es le spécialiste Blender de JARVIS. Tu ne fais QUE de la 3D : "
        "meshes, matériaux, shape keys, rig, animations, cheveux, vêtements, "
        "rendu, export GLB/GLTF, optimisation.\n"
        "MÉTHODE OBLIGATOIRE :\n"
        "- INSPECTE AVANT DE MODIFIER. Commence toujours par blender.inspect "
        "(ou avatar.job.inspect) pour connaître les objets, meshes, armatures, "
        "matériaux, shape keys et modificateurs RÉELS de la scène.\n"
        "- N'invente JAMAIS un nom d'objet, de matériau ou de shape key. "
        "Utilise exactement ceux que l'inspection a renvoyés.\n"
        "- Ne modifie jamais le fichier maître : on travaille toujours sur une "
        "copie de révision.\n"
        "- Pour un humanoïde final, n'utilise pas de primitives (cube, cylindre, "
        "sphère) comme stratégie de construction du corps. Les primitives ne "
        "servent qu'au blockout, aux guides et aux lumières. S'il n'existe pas "
        "de vraie base humanoïde, dis-le au lieu d'en fabriquer une en tubes.\n"
        "- Tu ne regardes pas les images : l'analyse visuelle t'est fournie sous "
        "forme de JSON structuré par le moteur de vision. N'invente pas ce que "
        "contient une référence.\n"
        "- Rapporte factuellement ce que tu as modifié et le résultat vérifié."
    ),
))

_register(AgentSpec(
    id="memory", name="Memory Agent", role="Mémoire", icon="brain", model_role="fast",
    tools=("memory.", "knowledge."),
    system_prompt=(
        "Tu es l'agent mémoire de JARVIS. Tu ranges, retrouves et consolides les informations durables. "
        "Tu évites les doublons et tu reformules de façon compacte et utile."
    ),
))

_register(AgentSpec(
    id="task", name="Task Agent", role="Tâches", icon="check", model_role="fast",
    tools=("task.", "calendar.", "automation.", "notify.", "memory.search"),
    system_prompt=(
        "Tu es l'agent de tâches de JARVIS. Tu crées, suis et clôtures les tâches, planifies les "
        "automatisations et gères l'agenda. Sois bref et opérationnel."
    ),
))


# Sous-ensembles d'outils par intention 3D.
#
# `jarvis-blender` tourne avec num_ctx=4096 : les 27 outils 3D pèsent à eux
# seuls ~4 800 tokens de schémas, ce qui sature le contexte et fait boucler le
# modèle. On ne lui envoie donc que les outils utiles à l'action détectée.
BLENDER_TOOLS_BY_ACTION: dict[str, tuple[str, ...]] = {
    "avatar.craft": (
        "blender.inspect", "blender.status", "avatar.engine.inspect",
        "avatar.engine.apply", "avatar.engine.build",
        "blender.generate_preview", "avatar.revision.list",
    ),
    "avatar.update_from_reference": (
        "avatar.reference.add", "avatar.reference.list", "avatar.reference.analyze",
        "avatar.update_from_reference", "avatar.revision.list",
    ),
    "blender.create_model": ("blender.create_model", "blender.inspect", "blender.status"),
    "blender.modify_model": ("blender.inspect", "blender.modify_model", "blender.generate_preview"),
    "blender.rig": ("blender.inspect", "blender.rig"),
    "blender.animate": ("blender.inspect", "blender.animate"),
    "blender.material": ("blender.inspect", "blender.material", "blender.texture"),
    "blender.render": ("blender.inspect", "blender.render", "blender.generate_preview"),
    "blender.export": ("blender.inspect", "blender.export"),
    "blender.optimize": ("blender.inspect", "blender.optimize"),
    "blender.inspect": ("blender.inspect", "blender.status", "avatar.engine.inspect"),
}


def blender_tools_for(action: str) -> tuple[str, ...]:
    """Outils à exposer au spécialiste pour cette action (vide = tous les 3D)."""
    return BLENDER_TOOLS_BY_ACTION.get(action, ())


class AgentManager:
    """État runtime des agents, persisté pour survivre au redémarrage."""

    def __init__(self, db: Database, events) -> None:
        self._db = db
        self._events = events
        for spec in AGENTS.values():
            self._db.execute(
                "INSERT INTO agents_state(id, status, last_activity_at, runs, enabled) VALUES(?,?,?,0,1) "
                "ON CONFLICT(id) DO NOTHING", (spec.id, "standby", None))
        # Aucun agent ne reste « running » après un redémarrage.
        self._db.execute("UPDATE agents_state SET status='standby', current_task_id='', current_action=''"
                         " WHERE status IN ('active','running')")

    @staticmethod
    def ids() -> list[str]:
        return [a for a in AGENTS if a != "jarvis"]

    @staticmethod
    def spec(agent_id: str) -> AgentSpec | None:
        return AGENTS.get(agent_id)

    def allowed_tools(self, agent_id: str, registry) -> list:
        spec = AGENTS.get(agent_id)
        if not spec:
            return []
        tools = registry.for_agent(agent_id)
        if not spec.tools:
            return tools
        return [t for t in tools if any(t.id.startswith(p) for p in spec.tools)]

    def set_state(self, agent_id: str, status: str, *, action: str = "",
                  task_id: str = "", error: str = "") -> None:
        now = time.time()
        self._db.execute(
            "UPDATE agents_state SET status=?, last_activity_at=?, current_action=?, current_task_id=?, "
            "last_error=?, runs = runs + ? WHERE id=?",
            (status, now, action[:200], task_id, error[:400], 1 if status == "active" else 0, agent_id),
        )
        payload = {"id": agent_id, "status": status, "action": action, "task_id": task_id,
                   "ts": now, "error": error}
        event = {"active": "agent.started", "standby": "agent.idle",
                 "error": "agent.failed", "done": "agent.completed"}.get(status, "agent.progress")
        self._events.emit(event, payload)

    def list(self) -> list[dict[str, Any]]:
        rows = {r["id"]: r for r in self._db.query("SELECT * FROM agents_state")}
        out = []
        for spec in AGENTS.values():
            r = rows.get(spec.id)
            out.append({
                "id": spec.id, "name": spec.name, "role": spec.role, "icon": spec.icon,
                "status": (r["status"] if r else "standby"),
                "last_activity_at": (r["last_activity_at"] if r else None),
                "current_task_id": (r["current_task_id"] if r else ""),
                "current_action": (r["current_action"] if r else ""),
                "last_error": (r["last_error"] if r else ""),
                "runs": (r["runs"] if r else 0),
                "enabled": bool(r["enabled"]) if r else True,
                "model_role": spec.model_role,
                "tool_prefixes": list(spec.tools),
            })
        return out

    def running_count(self) -> int:
        return int(self._db.scalar("SELECT COUNT(*) FROM agents_state WHERE status='active'") or 0)

    def set_enabled(self, agent_id: str, enabled: bool) -> bool:
        if agent_id not in AGENTS:
            return False
        self._db.execute("UPDATE agents_state SET enabled=? WHERE id=?", (1 if enabled else 0, agent_id))
        return True

    def is_enabled(self, agent_id: str) -> bool:
        row = self._db.one("SELECT enabled FROM agents_state WHERE id=?", (agent_id,))
        return bool(row["enabled"]) if row else True
