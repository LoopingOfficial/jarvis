"""JARVIS Orchestrator — boucle agentique : intention → plan → outils → validation → réponse."""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from .agents import AGENTS, blender_tools_for
from .build import JARVIS_BUILD_ID, scrub_forbidden_roots, trace
from .intents import (ConnectorIntent, detect_3d_intent, detect_image_intent,
                      detect_read_only_intent, resolve_connector_intent)
from .llm.base import ChatMessage, ToolCall
from .llm.tool_calls import (looks_like_tool_call_dump, recover_text_tool_calls,
                             strip_tool_call_text)
from .permissions import RISK_LABELS
from .tools.base import registry
from .tools.runner import ConfirmationRequired
from .fast_actions import FastActionRouter
from .goals import GoalCompletionChecker, has_write_intent
from .risk_guard import RiskEscalationGuard
from .remote_paths import (InvalidRemoteRootError, MissingRemoteWorkingDirectoryError,
                           active_remote_root, resolveRemotePath)
from .security_analysis import (AnalysisFinding, AnalysisResult, SourceDocument,
                                READ_ONLY_ALLOWED_TOOLS, deduplicate_findings,
                                parse_findings, split_into_chunks)
from .code_edit import CodeEditRouter
from .file_router import FileCommandRouter

# Raccourcis déterministes : ces demandes n'ont pas besoin d'un LLM.
DIRECT_PATTERNS: list[tuple[str, str, dict[str, Any]]] = [
    (r"^(?:statut|status|diagnostic|état du système|etat du systeme)$", "system.info", {}),
    (r"^(?:qu[e'’]?\s*est[- ]ce qui (?:n[ée]cessite|requiert|demande) (?:mon|ton) attention|"
     r"quoi de neuf|fais[- ]moi un point|point de situation|briefing?)\s*\??$", "jarvis.brief", {}),
    (r"^(?:mes t[âa]ches|liste (?:les|mes) t[âa]ches|t[âa]ches en cours)\s*\??$", "task.list", {}),
    (r"^(?:mon agenda|agenda|mes rendez[- ]vous|planning)\s*(?:du jour|aujourd'?hui)?\s*\??$",
     "calendar.list", {"days": 7}),
    (r"^(?:mes connecteurs|liste (?:les|mes) connecteurs)\s*\??$", "connector.list", {}),
    (r"^(?:mes automatisations|liste (?:les|mes) automatisations|mes workflows)\s*\??$", "automation.list", {}),
]


# ---------------------------------------------------------------------------
# Routage des intentions créatives
# ---------------------------------------------------------------------------
# Quand l'utilisateur demande une image, seuls les outils de création visuelle
# sont exposés au modèle. web.search / web.fetch sont volontairement retirés :
# une demande de génération ne doit JAMAIS retomber sur une recherche web.
#
# Même principe pour la 3D : une demande de modèle 3D ne doit devenir ni une
# recherche web ni une génération d'image. Seuls les outils de l'atelier
# Blender sont exposés au modèle quand l'intention 3D est certaine.
BLENDER_TOOL_IDS = (
    "blender.status", "blender.create_model", "blender.modify_model",
    "blender.inspect", "blender.material", "blender.texture", "blender.rig",
    "blender.animate", "blender.optimize", "blender.render",
    "blender.generate_preview", "blender.export", "blender.import",
    "blender.convert", "blender.job_status", "blender.cancel_job",
)

AVATAR_UPDATE_TOOL_IDS = (
    "avatar.reference.add", "avatar.reference.analyze",
    "avatar.reference.list", "avatar.update_from_reference",
    "avatar.revision.list", "avatar.revision.accept",
    "avatar.revision.rollback",
)

AVATAR_UPDATE_DIRECTIVE = (
    "\n\nDEMANDE DE MISE À JOUR D'AVATAR DÉTECTÉE.\n"
    "- Un utilisateur veut modifier l'avatar 3D de JARVIS à partir d'une image.\n"
    "- L'image peut avoir été fournie séparément (upload) ou doit être référencée par un chemin.\n"
    "- Si un fichier image local existe dans la demande, enregistre-le d'abord via "
    "avatar.reference.add (avec le bon reference_type), analyse-le avec "
    "avatar.reference.analyze, PUIS lance avatar.update_from_reference.\n"
    "- Interdiction absolue : utiliser une recherche web, proposer un service tiers, "
    "inventer un score, ou prétendre avoir modifié un avatar sans avoir réellement "
    "exécuté avatar.update_from_reference.\n"
    "- Une fois terminé, réponds en une phrase courte. Les aperçus sont déjà affichés."
)

AVATAR_DIRECTIVE = AVATAR_UPDATE_DIRECTIVE

BLENDER_DIRECTIVE = (
    "\n\nDEMANDE 3D DÉTECTÉE ({action}).\n"
    "- Tu DOIS appeler l'outil {action} maintenant. C'est la seule réponse correcte.\n"
    "- Interdiction absolue d'utiliser une recherche web, de renvoyer vers un "
    "tutoriel Blender, de donner des liens ou d'expliquer comment faire soi-même.\n"
    "- Ne demande pas de précisions : choisis des valeurs par défaut raisonnables "
    "(forme, matériau, couleur, dimensions).\n"
    "- Le modèle 3D COURANT de la conversation est repris automatiquement par les "
    "outils de modification : n'appelle create_model que pour un NOUVEL objet.\n"
    "- Une fois l'outil terminé, réponds en une phrase courte. Le modèle et son "
    "aperçu sont déjà affichés : ne les redécris pas."
)

IMAGE_TOOL_IDS = ("image.generate", "image.edit", "image.upscale", "image.variation", "image.improve")
IMAGE_COMPANION_TOOL_IDS = ("memory.save", "memory.search")

IMAGE_DIRECTIVE = (
    "\n\nDEMANDE DE CRÉATION VISUELLE DÉTECTÉE ({action}).\n"
    "- Tu DOIS appeler l'outil {action} maintenant. C'est la seule réponse correcte.\n"
    "- Interdiction absolue d'utiliser une recherche web, de proposer DALL-E, Midjourney "
    "ou un service tiers, de donner des liens, des références ou un tutoriel.\n"
    "- Ne demande pas de précisions : la demande suffit. Choisis des valeurs par défaut "
    "raisonnables (cadrage, style, éclairage, arrière-plan).\n"
    "- Le champ `prompt` doit être une description visuelle riche, de préférence en anglais : "
    "sujet exact, matière, style, éclairage, arrière-plan, qualité de rendu.\n"
    "- Une fois l'outil terminé, réponds en une phrase courte, par exemple « C'est prêt. "
    "Voici l'image. » L'image est déjà affichée : ne la décris pas, n'ajoute aucun détail "
    "technique."
)


class Orchestrator:
    def __init__(self, core) -> None:
        self._core = core
        self.fast_actions = FastActionRouter(core)
        self.goal_checker = GoalCompletionChecker()
        self.risk_guard = RiskEscalationGuard()
        self.file_router = FileCommandRouter(core)
        self.code_edit_router = CodeEditRouter(core)

    @staticmethod
    def _file_contents_goal(text: str) -> str:
        """Retourne le nom demandé quand l'objectif exige explicitement le contenu."""
        if not re.search(r"\b(?:trouve|chercher|cherche|affiche|montre|donne).*(?:contenu|fichier)|contenu.*\b(?:de|du)\b", text, re.I):
            return ""
        m = re.search(r"\b([\w.-]+\.(?:php|html?|css|js|json|ya?ml|py|txt|log))\b", text, re.I)
        return m.group(1) if m else ""

    @staticmethod
    def _path_from_search(output: str, filename: str) -> str:
        for line in (output or "").splitlines():
            candidate = line.strip().split()[0] if line.strip() else ""
            if filename.casefold() in candidate.casefold() and (candidate.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", candidate)):
                return candidate.strip("'\"`:,;")
        m = re.search(r"(?:/[^\s'\"`]*|[A-Za-z]:[\\/][^\s'\"`]*)" + re.escape(filename), output or "", re.I)
        return m.group(0) if m else ""

    # -- entrée principale -------------------------------------------------
    def handle(
        self, text: str, *, conversation_id: str = "", source: str = "text",
        confirmation_id: str = "", background: bool = False,
    ) -> dict[str, Any]:
        core = self._core
        text = (text or "").strip()
        if not text:
            return {"ok": False, "response": "", "action": "none"}

        conversation_id = conversation_id or core.conversations.current_id()
        core.conversations.add_message(conversation_id, "user", text, meta={"source": source})
        core.memory.auto_extract(text, conversation_id=conversation_id)

        # Décision de sécurité avant tout routeur et avant le LLM. Cette valeur
        # est recopiée dans le contexte actif pour protéger aussi les chemins
        # déterministes et les appels de runner sans argument explicite.
        readonly_intent = detect_read_only_intent(text)
        execution_policy = readonly_intent.to_dict()
        core.active_task_context.update({
            "intent": readonly_intent.intent,
            "read_only": readonly_intent.read_only,
            "write_allowed": readonly_intent.write_allowed,
            "active_file": readonly_intent.target or core.active_task_context.get("active_file", ""),
            "updated_at": time.time(),
        })
        self._debug(f"[intent] {readonly_intent.intent}", force=readonly_intent.read_only)
        self._debug(f"[policy] read_only={readonly_intent.read_only} "
                    f"write_allowed={readonly_intent.write_allowed}", force=readonly_intent.read_only)

        # Une correction de portée répare l'objectif actif ; elle ne devient
        # jamais une nouvelle commande de service.
        active_ctx = core.active_task_context
        goal_hint = self.goal_checker.detect(text, active_ctx)
        if goal_hint and goal_hint.target:
            active_ctx.update({"active_goal": goal_hint.type, "requested_file": goal_hint.target,
                               "scope": active_ctx.get("scope", "auto"), "updated_at": time.time()})
        connect_followup = bool(re.search(r"\b(?:connecte[- ]toi|utilise|connecte)\s+(?:au\s+|le\s+)?ssh\b", text, re.I))
        if connect_followup and active_ctx.get("requested_file"):
            active_ctx.update({"scope": "remote", "target_type": "remote_server", "connector_type": "ssh"})
            ssh_candidates = core.connectors.list(include_disabled=False)
            ssh_candidates = [c for c in ssh_candidates if c.get("type") == "ssh"]
            if len(ssh_candidates) == 1 and ssh_candidates[0].get("status") != "connected":
                try:
                    core.connectors.test(ssh_candidates[0]["id"])
                except Exception:
                    pass
            if len(ssh_candidates) == 1:
                active_ctx["connector_id"] = ssh_candidates[0]["id"]
            if not ssh_candidates:
                msg = "Aucun connecteur SSH n'est configuré."
                core.conversations.add_message(conversation_id, "assistant", msg, meta={"connector_resolution": "unavailable"})
                return {"ok": False, "response": msg, "action": "connector.resolve", "conversation_id": conversation_id, "tools_used": []}
            text = "trouve et affiche le contenu de " + str(active_ctx["requested_file"])
            active_ctx["updated_at"] = time.time()
        if active_ctx.get("target_type") == "remote_server" and re.search(r"\b(?:pas\s+en\s+local|sur\s+le\s+serveur|utilise\s+ssh|non\b|continue)\b", text, re.I):
            active_ctx["connector_type"] = "ssh"
            if active_ctx.get("connector_id"):
                active_ctx["updated_at"] = time.time()
                if active_ctx.get("last_remote_path"):
                    text = "affiche le contenu de " + str(active_ctx["last_remote_path"])

        # Action locale non ambiguë : exécution immédiate, sans Ollama.
        fast = self.fast_actions.execute(text, conversation_id)
        if fast is not None:
            return fast
        file_action = self.file_router.execute(text, conversation_id)
        if file_action is not None:
            return file_action
        # ROUTE D'EDITION DETERMINISTE : une demande de modification portant sur
        # le document ouvert dans Coding est EXECUTEE ici. Laissee au modele,
        # elle degenere en explication (« ouvre ton editeur et colle ceci »).
        if self.code_edit_router.should_handle(text):
            edit_action = self.code_edit_router.execute(text, conversation_id)
            if edit_action is not None:
                return edit_action

        # 1. Raccourcis déterministes (rapides, sans LLM)
        low = text.casefold().strip(" .!")
        for pattern, tool_id, args in DIRECT_PATTERNS:
            if re.match(pattern, low, re.IGNORECASE):
                core.activity(title="Exécution directe", detail=tool_id, kind="action", state="ACTING")
                core.events.emit("jarvis.state", {"state": "ACTING", "reason": tool_id})
                result = core.runner.run(tool_id, args, agent="jarvis", conversation_id=conversation_id)
                response = result.output or "Terminé."
                core.conversations.add_message(conversation_id, "assistant", response,
                                               meta={"tool": tool_id, "direct": True})
                core.events.emit("jarvis.state", {"state": "SPEAKING", "reason": "response"})
                return {"ok": result.ok, "response": response, "action": tool_id,
                        "conversation_id": conversation_id, "tools_used": [tool_id]}

        # 2. Boucle agentique complète, dans une tâche suivie
        task = core.tasks.create(name=text[:200], kind="chat", agent="jarvis", conversation_id=conversation_id)
        if background:
            core.tasks.run_background(
                task["id"],
                lambda: self._run_loop(text, task["id"], conversation_id,
                                       confirmation_id=confirmation_id,
                                       execution_policy=execution_policy),
            )
            return {"ok": True, "response": "Je m'en occupe.", "action": "task",
                    "task_id": task["id"], "conversation_id": conversation_id, "background": True}
        return self._run_loop(text, task["id"], conversation_id,
                              confirmation_id=confirmation_id,
                              execution_policy=execution_policy)

    # -- boucle -----------------------------------------------------------
    def _run_loop(
        self, text: str, task_id: str, conversation_id: str, *, confirmation_id: str = "",
        execution_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        core = self._core
        spec = AGENTS["jarvis"]
        core.tasks.set_status(task_id, "planning", progress=0.05)
        core.agents.set_state("jarvis", "active", action=text[:120], task_id=task_id)
        self._debug(f"JARVIS_BUILD_ID={JARVIS_BUILD_ID} LOOP_START input={text!r}", force=True)

        policy = dict(execution_policy or detect_read_only_intent(text).to_dict())
        if policy.get("intent") == "security_analysis":
            return self._run_security_analysis(text, task_id, conversation_id, policy)

        # Détection déterministe : une action connecteur claire ne doit JAMAIS
        # être « racontée » sans que l'outil n'ait réellement tourné.
        # Continuité opérationnelle : un nom de fichier nu reprend la cible
        # distante réellement utilisée au message précédent.
        intent_text = text
        active = core.active_task_context
        file_scope = core.settings.get("files", "default_scope", "auto")
        file_request = re.search(r"\b[\w.-]+\.(?:php|html?|css|js|json|py|ya?ml)\b", text, re.I)
        ssh_active = core.connectors.routing_candidates("ssh")
        if (file_scope == "remote" or (file_scope == "auto" and len(ssh_active) == 1)) and file_request and not re.search(r"\b(?:local|pc|ordinateur|bureau|jarvis-windows|c:)\b", text, re.I):
            intent_text = "via ssh " + text
            if len(ssh_active) == 1:
                cfg = (ssh_active[0].get("config") or {})
                root = cfg.get("working_directory") or cfg.get("deployment_path") or cfg.get("remote_path") or ""
                active.update({"scope": "remote", "target_type": "remote_server", "connector_type": "ssh",
                               "connector_id": ssh_active[0]["id"], "remote_root": root,
                               "deployment_path": cfg.get("deployment_path") or cfg.get("remote_path") or "",
                               "project": cfg.get("local_project") or active.get("project", ""),
                               "updated_at": time.time()})
        if active.get("target_type") == "remote_server" and time.time() - active.get("updated_at", 0) < 1800:
            if re.search(r"\b(?:trouve|chercher|cherche|affiche|montre|contenu|fichier)\b", text, re.I) and re.search(r"\b[\w.-]+\.(?:php|html?|css|js|json|log|txt)\b", text, re.I):
                intent_text = "via ssh " + text
            elif active.get("last_remote_path") and re.search(r"\b(?:affiche|montre|lis|lire)\b.*\b(?:son|le|la|ce)\b|\baffiche-le\b|\blis-le\b", text, re.I):
                intent_text = "via ssh affiche fichier " + str(active["last_remote_path"])
        intent = resolve_connector_intent(core, intent_text)
        if intent and intent.connector_type == "ssh" and intent.arguments.get("path") and intent.connector_id:
            connector = core.connectors.raw(intent.connector_id)
            if connector:
                try:
                    intent.arguments["path"] = resolveRemotePath(connector, intent.arguments["path"], active)
                except (InvalidRemoteRootError, MissingRemoteWorkingDirectoryError) as exc:
                    self._debug(f"FILE_TRACE error: {exc}", force=True)
                    intent.arguments["path"] = ""
                    intent.arguments["_error"] = str(exc)
        if intent:
            self._debug("INTENT_ACTION=" + intent.envelope["action"] + " OBJECT=" + str(intent.envelope["object"]) + " TARGET=" + intent.envelope["target_type"] + " TRANSPORT=" + intent.connector_type + " RISK=" + ("read_only" if intent.envelope["read_only"] else "destructive"), force=True)
        if intent and active.get("target_type") == "remote_server" and not intent.connector_id:
            intent.connector_id = active.get("connector_id", "")
            intent.arguments["connector_id"] = intent.connector_id
        goal = self.goal_checker.detect(text, active)
        file_goal = goal.target if goal and goal.type == "get_file_contents" else self._file_contents_goal(text)
        if goal:
            self._debug(f"USER_GOAL: {goal.type}", force=True)
        # Pipeline déterministe : tenter d'abord le chemin exact sous la
        # working_directory du SSH résolu. La recherche récursive n'arrive
        # qu'après un échec réel de cette lecture.
        if file_goal and active.get("scope") == "remote" and active.get("connector_id") and active.get("remote_root"):
            root = active["remote_root"]
            trace(f"input={text!r}",
                  f"intent={file_goal}",
                  f"scope=remote",
                  f"connector_id={active['connector_id']}",
                  f"connector_working_directory={root}",
                  f"active_remote_root={root}",
                  f"requested_path={file_goal}")
            try:
                exact = resolveRemotePath({"config": {"working_directory": root}}, file_goal, active)
            except (InvalidRemoteRootError, MissingRemoteWorkingDirectoryError) as exc:
                self._debug(f"FILE_TRACE error: {exc}", force=True)
                exact = ""
            if exact:
                self._debug(f"FILE_REQUEST={file_goal} FILE_SCOPE=remote CONNECTOR_ID={active['connector_id']} WORKING_DIRECTORY={active['remote_root']} RESOLVED_PATH={exact} TOOL=ssh.read_file", force=True)
                trace(f"resolved_path={exact}",
                      f"selected_tool=ssh.read_file",
                      f"tool_arguments={{connector_id: {active['connector_id']}, path: {exact}}}")
                direct = core.runner.run("ssh.read_file", {"connector_id": active["connector_id"], "path": exact}, agent="jarvis", task_id=task_id, conversation_id=conversation_id)
                direct_content = (direct.data or {}).get("content") or direct.output or ""
                if direct.ok and direct_content != "":
                    core.active_task_context.update({"last_remote_path": exact, "last_file": file_goal, "last_successful_tool": "ssh.read_file", "updated_at": time.time()})
                    doc = core.documents.add_from_tool(source="ssh", connector_id=active["connector_id"], path=exact, content=direct_content, language=exact.rsplit(".", 1)[-1].lower())
                    core.documents.open(doc.id)
                    core.events.emit("code.file.opened", core.documents.event_payload(doc))
                    response = core.documents.opened_response(doc, file_goal)
                    core.tasks.complete(task_id, response)
                    core.conversations.add_message(conversation_id, "assistant", response, meta={"source": "ssh", "path": exact, "raw_content": True})
                    return {"ok": True, "response": response, "file_content": (direct.data or {}).get("content") or direct.output,
                            "path": exact, "source": "ssh", "task_id": task_id, "conversation_id": conversation_id, "tools_used": ["ssh.read_file"]}
        followup_done = False

        # ROUTAGE CRÉATIF : décidé avant le modèle. « Crée-moi une image » ne
        # peut pas devenir une recherche web, quelle que soit l'humeur du LLM.
        image_intent = detect_image_intent(text)
        image_jobs: list[dict[str, Any]] = []
        image_error = ""

        # ROUTAGE 3D : « crée-moi une lampe en 3D », « anime ce personnage »,
        # « exporte en GLB » vont à l'atelier Blender, jamais au web.
        blender_intent = detect_3d_intent(text)
        avatar_update_intent = None
        if blender_intent and blender_intent.get("action") == "avatar.update_from_reference":
            avatar_update_intent = blender_intent
            blender_intent = None
        blender_jobs: list[dict[str, Any]] = []
        blender_error = ""
        if blender_intent and blender_intent.get("needs_project"):
            # Une suite (« fais-la plus fine ») n'a de sens qu'avec un projet ouvert.
            current = core.blender.current(conversation_id)
            if not (current and current.get("has_blend")):
                blender_intent = None
        if avatar_update_intent:
            image_intent = None
            intent = None
            self._debug(f"INTENT DETECTED: {avatar_update_intent['action'].replace('.', '_')} "
                        f"({avatar_update_intent['reason']})", force=True)
            core.activity(title="Mise à jour d'avatar détectée",
                          detail=avatar_update_intent["action"],
                          kind="think", state="THINKING")
            if not core.blender.available():
                self._debug("BLENDER MISSING — aucun repli web", force=True)
                message = ("Je ne peux pas modifier mon avatar 3D : Blender n'est pas "
                           "détecté sur ce PC. Installe-le depuis blender.org, puis "
                           "indique son chemin dans Settings → Atelier 3D.")
                core.tasks.complete(task_id, message)
                core.agents.set_state("jarvis", "standby")
                core.events.emit("jarvis.state", {"state": "SPEAKING",
                                                  "reason": "no_blender"})
                core.conversations.add_message(
                    conversation_id, "assistant", message,
                    meta={"avatar_update_missing_blender": True})
                return {"ok": False, "response": message, "task_id": task_id,
                        "conversation_id": conversation_id, "tools_used": [],
                        "blender_missing": True}
            # Pipeline avatar : la VISION analyse l'image (le spécialiste 3D
            # n'est pas un moteur de vision), puis on lui transmet le JSON.
            avatar_features = self._latest_reference_features()
            return self.delegate_to_blender(
                text, avatar_update_intent["action"], task_id=task_id,
                conversation_id=conversation_id, features=avatar_features)
        elif blender_intent:
            image_intent = None                # la 3D prime sur l'image
            intent = None
            self._debug(f"INTENT DETECTED: {blender_intent['action'].replace('.', '_')} "
                        f"({blender_intent['reason']})", force=True)
            core.activity(title="Intention 3D détectée", detail=blender_intent["action"],
                          kind="think", state="THINKING")
            if not core.blender.available():
                # Blender absent : JARVIS le dit. Jamais de recherche web à la place.
                self._debug("BLENDER MISSING — aucun repli web", force=True)
                message = ("Je ne peux pas produire de modèle 3D : Blender n'est pas "
                           "détecté sur ce PC. Installe-le depuis blender.org, puis "
                           "indique son chemin dans Settings → Atelier 3D.")
                core.tasks.complete(task_id, message)
                core.agents.set_state("jarvis", "standby")
                core.events.emit("jarvis.state", {"state": "SPEAKING",
                                                  "reason": "no_blender"})
                core.conversations.add_message(
                    conversation_id, "assistant", message,
                    meta={"blender_intent": blender_intent["action"],
                          "blender_missing": True})
                return {"ok": False, "response": message, "task_id": task_id,
                        "conversation_id": conversation_id, "tools_used": [],
                        "blender_missing": True}
            # Le modèle général n'improvise plus de 3D : on délègue au
            # spécialiste `jarvis-blender`.
            return self.delegate_to_blender(
                text, blender_intent["action"], task_id=task_id,
                conversation_id=conversation_id)

        if image_intent:
            intent = None                      # la création visuelle prime
            self._debug(f"INTENT DETECTED: {image_intent['action'].replace('.', '_')} "
                        f"({image_intent['reason']})", force=True)
            core.activity(title="Intention créative détectée", detail=image_intent["action"],
                          kind="think", state="THINKING")
            if not core.imagegen.available():
                # Aucun moteur : JARVIS le dit. Jamais de recherche web à la place.
                self._debug("IMAGE BACKEND MISSING — aucun repli web", force=True)
                message = ("Je ne peux pas générer d'image : ComfyUI n'est pas disponible "
                           "pour le pipeline golden Z-Image-Turbo.")
                core.tasks.complete(task_id, message)
                core.agents.set_state("jarvis", "standby")
                core.events.emit("jarvis.state", {"state": "SPEAKING", "reason": "no_image_backend"})
                core.conversations.add_message(conversation_id, "assistant", message,
                                               meta={"image_intent": image_intent["action"],
                                                     "image_backend": "missing"})
                return {"ok": False, "response": message, "task_id": task_id,
                        "conversation_id": conversation_id, "tools_used": [],
                        "image_backend_missing": True}
            # Le pipeline image golden est volontairement déterministe : il ne
            # passe pas par le LLM, qui pourrait appeler avatar.gesture ou
            # enrichir le prompt au lieu de produire l'image demandée.
            return self._execute_direct_image_intent(
                image_intent, text, task_id=task_id,
                conversation_id=conversation_id, confirmation_id=confirmation_id)

        # RECALL : le Brain cherche une procédure connue avant d'agir.
        # (sauté pour une action connecteur : le rappel ne doit pas biaiser l'exécution)
        if intent and intent.executable:
            core.events.emit("jarvis.state", {"state": "ACTING", "reason": intent.tool_id})
            core.activity(title="Action reconnue", detail=intent.description, kind="action",
                          state="ACTING")
        else:
            core.events.emit("jarvis.state", {"state": "RECALLING", "reason": "recall"})
            core.activity(title="Consultation de la mémoire", detail="Recherche de procédures connues",
                          kind="recall", state="RECALLING")

        tools = [t for t in registry.for_agent("jarvis") if t.enabled]
        if policy.get("read_only") and not policy.get("write_allowed"):
            tools = [t for t in tools if t.id in READ_ONLY_ALLOWED_TOOLS]
        elif image_intent:
            allowed = set(IMAGE_TOOL_IDS) | set(IMAGE_COMPANION_TOOL_IDS)
            tools = [t for t in tools if t.id in allowed]
        elif avatar_update_intent:
            allowed = set(AVATAR_UPDATE_TOOL_IDS) | set(IMAGE_COMPANION_TOOL_IDS)
            tools = [t for t in tools if t.id in allowed]
        elif intent and intent.connector_type in {"ssh", "sftp", "ftp"} and intent.envelope.get("read_only"):
            allowed = {"ssh.run", "ssh.list", "ssh.read_file", "ssh.logs", "ssh.status", "memory.search", "memory.save"}
            tools = [t for t in tools if t.id in allowed]
        elif blender_intent:
            allowed = set(BLENDER_TOOL_IDS) | set(IMAGE_COMPANION_TOOL_IDS)
            tools = [t for t in tools if t.id in allowed]
        schemas = [t.llm_schema() for t in tools]
        self._debug("TOOLS AVAILABLE: ["
                    + ", ".join(str(s.get("function", {}).get("name") or s.get("name"))
                                for s in schemas) + "]",
                    force=bool(image_intent or blender_intent or avatar_update_intent))
        messages = self._build_messages(spec, text, conversation_id, tools,
                                        skip_recall=bool(image_intent or blender_intent
                                                         or avatar_update_intent)
                                                         or bool(intent and intent.executable))
        if policy.get("read_only"):
            messages[0].content += (
                "\n\nEXECUTION_POLICY (décidée par le backend avant le modèle) :\n"
                + json.dumps({"intent": policy.get("intent", "read_only"),
                              "read_only": True, "write_allowed": False}, ensure_ascii=False)
                + "\nMode STRICTEMENT lecture seule. Autorise uniquement la lecture et "
                "l'analyse. Toute écriture, patch, remplacement, déploiement ou "
                "redémarrage est interdite et sera bloquée par le ToolRunner.\n"
                "Le résultat de l'analyse est une AnalysisResult, jamais le contenu "
                "d'un SourceDocument et jamais une nouvelle version du fichier."
            )
        connector_context = core.connectors.llm_context()
        if connector_context:
            lines = []
            for c in connector_context:
                root = (c.get("working_directory") or c.get("deployment_path") or c.get("remote_path") or "")
                root_part = f", root={root}" if root else ""
                lines.append(
                    f"- {c['type']}: id={c['id']}, host={c.get('host','')}"
                    f"{root_part}, permissions={','.join(c['permissions'])}")
            messages[0].content += "\n\nAVAILABLE_CONNECTORS (source de vérité backend):\n" + "\n".join(lines)
            messages[0].content += (
                "\n\nRÈGLE D'ACCÈS AUX FICHIERS DISTANTS : pour tout fichier sur un "
                "connecteur SSH avec une `root` configurée, le chemin complet est "
                "`<root>/<fichier>` (ex. blog.php → <root>/blog.php). N'invente JAMAIS de "
                "chemin générique du type /var/www/html, /www, /home/user : la racine "
                "affichée ci-dessus sur le connecteur est la SEULE valide. S'il n'y a pas "
                "de root, utilise ssh.read_file avec `connector_id` et demande le chemin."
            )
        else:
            messages[0].content += "\n\nAVAILABLE_CONNECTORS: aucun connecteur actif. Ne prétends pas qu'un service est configuré."
        if core.active_task_context:
            ctx = core.active_task_context
            root = ctx.get("remote_root") or ctx.get("deployment_path") or ""
            messages[0].content += ("\n\nACTIVE_TASK_CONTEXT (état backend vérifié): "
                f"target_type={ctx.get('target_type')}, connector_id={ctx.get('connector_id')}, "
                f"connector_type={ctx.get('connector_type')}, host={ctx.get('host')}, "
                f"remote_root={root}, last_file={ctx.get('last_file')}. "
                "Reprends cette cible pour un follow-up de fichier : le fichier demandé "
                f"est sous remote_root={root}.")
        # DOCUMENT COURANT DE L'ENVIRONNEMENT CODING.
        # Sans cette injection, « ajoute un commentaire en haut de X » ne peut
        # que produire un conseil : le modèle ne connaît ni le chemin exact,
        # ni le contenu, ni le connecteur du fichier ouvert sous les yeux de
        # l'utilisateur. Avec elle, il peut émettre un ssh.write_file complet.
        active_doc = None
        try:
            _adid = core.documents.active_document_id
            active_doc = core.documents.documents.get(_adid) if _adid else None
        except Exception:
            active_doc = None
        if active_doc is not None:
            body = active_doc.content or ""
            truncated = len(body) > 24000
            messages[0].content += (
                "\n\nACTIVE_CODE_DOCUMENT (fichier actuellement ouvert dans Coding) :"
                f"\n- absolute_path = {active_doc.absolute_path}"
                f"\n- source_type = {active_doc.source_type}"
                f"\n- connector_id = {active_doc.connector_id}"
                f"\n- language = {active_doc.language}"
                f"\n- taille = {len(body)} caractères"
                + ("\n- ATTENTION : contenu tronqué ci-dessous, relis le fichier "
                   "avant toute réécriture globale." if truncated else "")
                + "\n- contenu actuel :\n```\n" + body[:24000] + "\n```")
            if has_write_intent(text):
                tool_name = ("ssh.write_file" if active_doc.source_type == "ssh"
                             else "fs.write")
                messages[0].content += (
                    "\n\nCODE_EDIT_DIRECTIVE : l'utilisateur demande une MODIFICATION "
                    "de ce fichier. Tu dois l'EXÉCUTER, pas l'expliquer.\n"
                    f"- Appelle {tool_name} avec path={active_doc.absolute_path}"
                    + (f" et connector_id={active_doc.connector_id}"
                       if active_doc.connector_id else "") + ".\n"
                    "- `content` doit contenir le fichier COMPLET après modification, "
                    "pas seulement le fragment ajouté, et pas de bloc markdown.\n"
                    "- Conserve à l'identique tout ce que l'utilisateur n'a pas demandé "
                    "de changer.\n"
                    "- N'explique JAMAIS à l'utilisateur comment le faire lui-même "
                    "et ne lui demande pas d'ouvrir un éditeur.")
        if image_intent:
            messages[0].content += IMAGE_DIRECTIVE.format(action=image_intent["action"])
        elif avatar_update_intent:
            messages[0].content += AVATAR_UPDATE_DIRECTIVE
            context_3d = core.blender.current(conversation_id)
            if context_3d and context_3d.get("has_blend"):
                messages[0].content += (
                    f"\n- Projet 3D courant : « {context_3d.get('name', '')} »"
                    f" (job {context_3d.get('last_job_id', '')}). L'avatar sera "
                    f"repris depuis le master .blend officiel.")
        elif blender_intent:
            messages[0].content += BLENDER_DIRECTIVE.format(action=blender_intent["action"])
            context_3d = core.blender.current(conversation_id)
            if context_3d and context_3d.get("has_blend"):
                messages[0].content += (
                    f"\n- Projet 3D courant : « {context_3d.get('name', '')} »"
                    f" (job {context_3d.get('last_job_id', '')}). Les outils de "
                    f"modification le reprennent automatiquement.")

        max_iterations = int(core.settings.get("ai", "max_tool_iterations", 12))
        used_tools: list[str] = []
        final_text = ""
        last_model_text = ""
        forced_once = False
        pending_confirmation: dict[str, Any] | None = None

        for step in range(max_iterations):
            if core.tasks.is_cancelled(task_id):
                core.agents.set_state("jarvis", "standby")
                return {"ok": False, "response": "Tâche annulée.", "task_id": task_id,
                        "conversation_id": conversation_id}
            core.tasks.set_status(task_id, "running", progress=min(0.9, 0.1 + step * 0.12))
            core.events.emit("jarvis.state", {"state": "THINKING", "reason": "llm"})
            core.activity(title="Réflexion", detail="Analyse de la demande", kind="think",
                          state="THINKING")
            response = core.llm.chat(messages, role="default", tools=schemas,
                                     temperature=core.settings.get("ai", "temperature", 0.3))
            if not response.ok:
                core.tasks.fail(task_id, response.error)
                core.agents.set_state("jarvis", "error", error=response.error)
                fallback = self._offline_fallback(text, response.error)
                core.conversations.add_message(conversation_id, "assistant", fallback, meta={"error": True})
                core.events.emit("jarvis.state", {"state": "ERROR", "reason": response.error[:120]})
                return {"ok": False, "response": fallback, "task_id": task_id,
                        "conversation_id": conversation_id, "error": response.error}

            last_model_text = (response.text or "").strip()
            allowed_names = {t.id for t in tools}
            if not response.tool_calls:
                recovered = recover_text_tool_calls(response.text, allowed_names)
                if recovered:
                    self._debug("TEXT TOOL CALL RECOVERED: "
                                + ", ".join(c.name for c in recovered), force=True)
                    response.tool_calls = recovered
                    response.text = ""
                    last_model_text = ""

            if (not response.tool_calls and blender_intent and not used_tools
                    and not forced_once):
                # Le modele a bavarde au lieu d'agir : JARVIS execute lui-meme.
                forced_once = True
                self._debug(f"MODEL RETURNED NO TOOL -> forcing {blender_intent['action']}",
                            force=True)
                response.tool_calls = [self._forced_blender_call(blender_intent, text)]
                response.text = ""

            if not response.tool_calls and image_intent and not used_tools and not forced_once:
                # Le modèle a bavardé au lieu d'agir : JARVIS exécute lui-même.
                # C'est ce qui garantit qu'une demande d'image produit une image.
                forced_once = True
                self._debug(f"MODEL RETURNED NO TOOL → forcing {image_intent['action']}",
                            force=True)
                response.tool_calls = [self._forced_image_call(image_intent, text)]
                response.text = ""

            if not response.tool_calls and avatar_update_intent and not used_tools and not forced_once:
                # « Modifie ton avatar selon cette image » doit TOUJOURS aboutir à
                # une exécution réelle. Le modèle ne peut pas bavarder à la place.
                forced_once = True
                self._debug(f"MODEL RETURNED NO TOOL → forcing "
                            f"{avatar_update_intent['action']}", force=True)
                response.tool_calls = [self._forced_avatar_update_call(avatar_update_intent, text)]
                response.text = ""

            if not response.tool_calls:
                # Anti-hallucination : une action connecteur claire a été demandée
                # mais le modèle répond sans exécuter d'outil. On force une
                # reprise explicite (une seule fois).
                if intent and intent.executable and not used_tools and not forced_once:
                    forced_once = True
                    self._debug(f"MODEL RETURNED NO TOOL → forcing {intent.tool_id}")
                    messages.append(ChatMessage(role="assistant", content=last_model_text))
                    force = (f"Interdiction d'inventer : exécute obligatoirement l'outil "
                             f"{intent.tool_id} avec ces arguments exacts : "
                             f"{json.dumps(intent.arguments, ensure_ascii=False)}. "
                             f"Clé de connexion : {intent.connector_id}. "
                             f"Tu concluras UNIQUEMENT sur le résultat réel retourné par l'outil.")
                    messages.append(ChatMessage(role="user", content=force))
                    continue
                final_text = strip_tool_call_text(last_model_text, allowed_names)
                if looks_like_tool_call_dump(last_model_text, allowed_names):
                    self._debug("REFUSING TO DISPLAY RAW TOOL JSON", force=True)
                    final_text = ""
                break

            messages.append(ChatMessage(role="assistant", content=response.text, tool_calls=response.tool_calls))
            if response.text.strip():
                core.tasks.log(task_id, response.text.strip()[:400], level="thought")

            for call in response.tool_calls:
                goal_name = goal.type if goal else (intent.envelope["action"] if intent else "")
                allowed_call, reason = self.risk_guard.check(goal_name, call.name, call.arguments)
                if not allowed_call:
                    self._debug(reason, force=True)
                    messages.append(ChatMessage(role="tool", content=reason, tool_call_id=call.id, name=call.name))
                    continue
                tool = registry.get(call.name)
                label = tool.name if tool else call.name
                self._debug(f"TOOL SELECTED: {call.name} "
                            + json.dumps(self._safe_args(call.arguments), ensure_ascii=False),
                            force=call.name.startswith("image."))
                core.tasks.log(task_id, f"Outil : {label}", level="tool", data=self._safe_args(call.arguments))
                core.agents.set_state("jarvis", "active", action=f"{label}", task_id=task_id)
                core.events.emit("jarvis.state", {"state": "ACTING", "reason": call.name})
                core.events.emit("tool.started", {"tool_id": call.name, "name": label})
                core.brain.tool_activity(call.name)
                core.activity(title=f"Utilisation de {tool.name if tool else call.name}",
                              detail=f"Outil {call.name}", kind="tool", state="ACTING")
                try:
                    result = core.runner.run(call.name, call.arguments, agent="jarvis",
                                             task_id=task_id, conversation_id=conversation_id,
                                              confirmation_id=confirmation_id,
                                              execution_policy=policy)
                except ConfirmationRequired as exc:
                    pending = exc.pending
                    core.tasks.set_status(task_id, "waiting_confirmation")
                    core.tasks.log(task_id, f"Confirmation requise : {pending.action}", level="warn")
                    core.conversations.add_message(
                        conversation_id, "assistant", exc.message,
                        meta={"confirmation_id": pending.id, "risk": pending.risk})
                    core.agents.set_state("jarvis", "standby")
                    core.events.emit("jarvis.state", {"state": "WAITING", "reason": "confirmation"})
                    pending_confirmation = {
                        "id": pending.id, "action": pending.action, "risk": pending.risk,
                        "risk_label": RISK_LABELS.get(pending.risk, pending.risk), "reason": pending.reason,
                    }
                    return {"ok": True, "response": exc.message, "task_id": task_id,
                            "conversation_id": conversation_id, "needs_confirmation": pending_confirmation,
                            "tools_used": used_tools}
                used_tools.append(call.name)
                if goal:
                    complete = self.goal_checker.observe(goal, call.name, result)
                    self._debug(f"GOAL_COMPLETE: {complete}", force=True)
                if result.ok and call.name in {"ssh.read_file", "fs.read"} and isinstance(result.data, dict):
                    source = result.data.get("source", "ssh" if call.name.startswith("ssh") else "fs")
                    connector_id = result.data.get("connector", "")
                    path = result.data.get("path", call.arguments.get("path", ""))
                    content = result.data.get("content") or result.output or ""
                    language = path.rsplit(".", 1)[-1].lower() if "." in path else "plaintext"
                    doc = core.documents.add_from_tool(source=source, connector_id=connector_id,
                                                       path=path, content=content, language=language)
                    core.documents.open(doc.id)
                    core.events.emit("code.file.opened", core.documents.event_payload(doc))
                # Un résultat de recherche n'est pas terminal quand l'objectif
                # demande le contenu : enchaînement backend vers read_file.
                if (result.ok and file_goal and not followup_done
                        and call.name in {"ssh.run", "ssh.search", "fs.search"}):
                    found_path = self._path_from_search(result.output, file_goal)
                    if found_path and call.name.startswith("ssh"):
                        read_args = {"path": found_path}
                        cid = (result.data or {}).get("connector") if isinstance(result.data, dict) else ""
                        if cid: read_args["connector_id"] = cid
                        self._debug(f"GOAL INCOMPLETE → automatic ssh.read_file {found_path}", force=True)
                        read_result = core.runner.run("ssh.read_file", read_args, agent="jarvis",
                                                      task_id=task_id, conversation_id=conversation_id,
                                                        confirmation_id=confirmation_id,
                                                        execution_policy=policy)
                        used_tools.append("ssh.read_file")
                        self.goal_checker.observe(goal, "ssh.read_file", read_result)
                        if read_result.ok:
                            read_content = (read_result.data or {}).get("content") or read_result.output or ""
                            doc = core.documents.add_from_tool(source="ssh", connector_id=cid, path=found_path, content=read_content, language=found_path.rsplit(".", 1)[-1].lower())
                            core.documents.open(doc.id)
                            core.events.emit("code.file.opened", core.documents.event_payload(doc))
                            if goal and self.goal_checker.observe(goal, "ssh.read_file", read_result):
                                response = core.documents.opened_response(doc, file_goal)
                                core.tasks.complete(task_id, response)
                                core.conversations.add_message(conversation_id, "assistant", response,
                                                               meta={"source": "ssh", "path": found_path, "raw_content": True})
                                return {"ok": True, "response": response,
                                        "file_content": (read_result.data or {}).get("content") or read_result.output,
                                        "path": found_path, "source": "ssh", "task_id": task_id,
                                        "conversation_id": conversation_id, "tools_used": used_tools}
                        followup_done = True
                        core.active_task_context["last_remote_path"] = found_path
                        core.active_task_context["last_file"] = file_goal
                        core.active_task_context["updated_at"] = time.time()
                        messages.append(ChatMessage(role="tool", content=(read_result.output or "")[:30000],
                                                    tool_call_id="auto-read-file", name="ssh.read_file"))
                if result.ok and tool and tool.connector_type:
                    cfg = (core.connectors.raw((result.data or {}).get("connector")) or {}).get("config", {}) if isinstance(result.data, dict) else {}
                    core.active_task_context = {
                        "project": (re.search(r"\b([a-z0-9-]+\.(?:com|fr|net|org))\b", text, re.I).group(1)
                                    if re.search(r"\b([a-z0-9-]+\.(?:com|fr|net|org))\b", text, re.I)
                                    else (active.get("project", "") if active else "")),
                        "target_type": "remote_server" if tool.connector_type in {"ssh", "sftp", "ftp", "cpanel"} else "connector",
                        "connector_type": tool.connector_type,
                        "connector_id": (result.data or {}).get("connector") if isinstance(result.data, dict) else "",
                        "host": cfg.get("host", ""),
                        "remote_root": cfg.get("working_directory") or cfg.get("deployment_path") or "",
                        "deployment_path": cfg.get("deployment_path") or "",
                        "project": (cfg.get("local_project") or core.active_task_context.get("project", "")),
                        "last_file": "", "updated_at": time.time(),
                        "last_tool": call.name,
                         "intent": policy.get("intent", "general"),
                         "read_only": bool(policy.get("read_only")),
                         "write_allowed": bool(policy.get("write_allowed", True)),
                         "mode": policy.get("intent", "general"),
                         "last_tool_result": {"ok": True, "source": tool.connector_type,
                                              "connector_id": (result.data or {}).get("connector") if isinstance(result.data, dict) else ""},
                    }
                    for key in ("path", "file", "filename"):
                        if call.arguments.get(key): core.active_task_context["last_file"] = str(call.arguments[key])
                for artifact in result.artifacts or []:
                    if artifact.get("type") == "image" and artifact.get("job_id"):
                        image_jobs.append(artifact)
                    elif artifact.get("type") == "model3d" and artifact.get("job_id"):
                        blender_jobs.append(artifact)
                if call.name.startswith("blender.") and not result.ok:
                    # Un echec 3D ne doit jamais etre maquille en succes.
                    blender_error = result.output or "Blender n'a pas repondu."
                    self._debug(f"BLENDER JOB FAILED: {blender_error[:200]}", force=True)
                if call.name.startswith("image.") and not result.ok:
                    # Un échec de génération ne doit jamais être maquillé en succès.
                    image_error = result.output or "Le moteur image n'a pas répondu."
                    self._debug(f"IMAGE JOB FAILED: {image_error[:200]}", force=True)
                core.events.emit("tool.completed", {"tool_id": call.name,
                                                    "metrics_recorded": True,
                                                    "ok": result.ok,
                                                    "preview": (result.output or "")[:200]})
                output = result.output or ("Terminé." if result.ok else "Échec sans détail.")
                self._debug(f"TOOL RESULT SENT TO LLM ({call.name}) : {(output or '')[:300]!r}")
                messages.append(ChatMessage(role="tool", content=output[:20000],
                                            tool_call_id=call.id, name=call.name))
                core.tasks.log(task_id, f"{label} → {'ok' if result.ok else 'échec'}",
                               level="info" if result.ok else "error", data={"preview": output[:400]})
        else:
            final_text = "J'ai atteint la limite d'étapes pour cette demande."
            self._debug("MAX_ITERATIONS REACHED")

        # Garde finale : une action connecteur doit avoir réellement tourné.
        if intent and intent.executable and not used_tools:
            verdict, payload = self._force_execute(intent, messages, text, last_model_text,
                                                   task_id, conversation_id, confirmation_id, used_tools)
            if verdict == "confirmation":
                message, confirm = payload
                return {"ok": True, "response": message, "task_id": task_id,
                        "conversation_id": conversation_id, "needs_confirmation": confirm,
                        "tools_used": used_tools}
            final_text = payload

        if not final_text:
            final_text = "C'est fait."
        if blender_jobs:
            final_text = self._model_success_text(final_text)
        elif blender_error:
            detail = blender_error.strip()
            prefix = "La creation 3D a echoue."
            final_text = (detail if detail.lower().startswith("le job 3d")
                          else f"{prefix} {detail}")[:600]
        elif image_jobs:
            final_text = self._image_success_text(final_text)
        elif image_error:
            # Échec réel : on le dit, sans jamais laisser passer un texte de repli
            # bavard (liens, résultats de recherche) produit par le modèle.
            detail = image_error.strip()
            prefix = "La génération a échoué."
            final_text = (detail if detail.lower().startswith(prefix.lower())
                          else f"{prefix} {detail}")[:400]
            if "réessay" not in final_text.lower():
                final_text += " Dis-moi si je réessaie."
        final_text = core.vault.scrub(final_text)
        self._debug(f"FINAL RESPONSE : {final_text[:200]!r}")

        # VERIFY + LEARN : auto-apprentissage après une réussite réelle.
        if used_tools and final_text and final_text not in {"C'est fait.", "Terminé."}:
            core.events.emit("jarvis.state", {"state": "LEARNING", "reason": "auto-learn"})
            core.activity(title="Analyse du résultat", detail="Vérification de la réussite",
                          kind="verify", state="VERIFYING")
            try:
                learnt = core.auto_learning.learn(
                    request=text, response=final_text, tools_used=used_tools,
                    project=self._core.settings.get("general", "default_project", ""))
                if learnt and learnt["action"] != "updated":
                    core.activity(title="Nouvelle connaissance enregistrée",
                                  detail=learnt.get("id", ""), kind="learn", state="LEARNING")
            except Exception:
                pass

        core.tasks.complete(task_id, final_text)
        core.agents.set_state("jarvis", "standby")
        core.events.emit("jarvis.state", {"state": "SPEAKING", "reason": "response", "text": final_text[:200]})
        meta: dict[str, Any] = {"tools": used_tools, "task_id": task_id}
        if image_jobs:
            meta["image_jobs"] = [j["job_id"] for j in image_jobs]
        if blender_jobs:
            meta["blender_jobs"] = [j["job_id"] for j in blender_jobs]
        message = core.conversations.add_message(conversation_id, "assistant", final_text, meta=meta)
        for artifact in image_jobs:
            core.imagegen.attach_message(artifact["job_id"], message["id"])
        for artifact in blender_jobs:
            core.blender.attach_message(artifact["job_id"], message["id"])
        core.conversations.maybe_title(conversation_id, text)
        return {"ok": True, "response": final_text, "task_id": task_id,
                "conversation_id": conversation_id, "tools_used": used_tools,
                "images": [{"job_id": j["job_id"], "url": j.get("url", "")} for j in image_jobs],
                "models": [{"job_id": j["job_id"], "glb_url": j.get("glb_url", ""),
                            "preview_url": j.get("preview_url", "")} for j in blender_jobs]}

    def _run_security_analysis(
        self, text: str, task_id: str, conversation_id: str,
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        """Analyse une source sans jamais transformer le rapport en fichier."""
        core = self._core
        target = str(policy.get("target") or core.active_task_context.get("requested_file") or "").strip()
        if not target:
            message = "Précise le fichier à analyser. Aucune modification n'a été effectuée."
            core.tasks.fail(task_id, message)
            return {"ok": False, "response": message, "task_id": task_id,
                    "conversation_id": conversation_id, "tools_used": [],
                    "analysis": None}

        candidates = core.connectors.routing_candidates("ssh")
        active = core.active_task_context
        connector_id = str(active.get("connector_id") or "")
        connector = core.connectors.raw(connector_id) if connector_id else None
        if connector is None and len(candidates) == 1:
            connector = core.connectors.raw(candidates[0]["id"])
            connector_id = candidates[0]["id"]

        source_type = "ssh" if connector else "fs"
        path = target
        read_args: dict[str, Any] = {}
        if connector:
            cfg = connector.get("config") or {}
            root = (active.get("remote_root") or cfg.get("working_directory") or
                    cfg.get("deployment_path") or cfg.get("remote_path") or "")
            if not root:
                message = ("Le connecteur SSH n'a pas de working_directory configuré. "
                           "Aucune modification n'a été effectuée.")
                core.tasks.fail(task_id, message)
                return {"ok": False, "response": message, "task_id": task_id,
                        "conversation_id": conversation_id, "tools_used": [], "analysis": None}
            try:
                path = resolveRemotePath(connector, target, {**active, "remote_root": root})
            except (InvalidRemoteRootError, MissingRemoteWorkingDirectoryError) as exc:
                message = str(exc)
                core.tasks.fail(task_id, message)
                return {"ok": False, "response": message, "task_id": task_id,
                        "conversation_id": conversation_id, "tools_used": [], "analysis": None}
            active.update({"scope": "remote", "target_type": "remote_server",
                           "connector_type": "ssh", "connector_id": connector_id,
                           "remote_root": root, "active_file": target,
                           "requested_file": target, "mode": "security_analysis"})
            read_args = {"connector_id": connector_id, "path": path}
        else:
            read_args = {"path": path, "max_lines": 2_000_000}

        self._debug(f"[intent] security_analysis", force=True)
        self._debug(f"[policy] read_only=true write_allowed=false", force=True)
        self._debug(f"[workspace] {active.get('remote_root') or '(local filesystem roots)'}", force=True)
        self._debug(f"[file] {target}", force=True)
        core.activity(title="🔒 Analyse en lecture seule", detail=f"{target} — lecture", kind="security", state="THINKING")
        core.tasks.log(task_id, "🔒 Analyse en lecture seule", level="info",
                       data={"intent": "security_analysis", "read_only": True,
                             "write_allowed": False, "file": target})

        read_tool = "ssh.read_file" if connector else "fs.read"
        self._debug(f"[tool] {read_tool}", force=True)
        result = core.runner.run(read_tool, read_args, agent="jarvis", task_id=task_id,
                                 conversation_id=conversation_id,
                                 execution_policy=policy)
        tools_used = [read_tool]
        if not result.ok:
            message = f"Lecture impossible de {target} : {result.output or 'erreur inconnue'}"
            core.tasks.fail(task_id, message)
            return {"ok": False, "response": message, "task_id": task_id,
                    "conversation_id": conversation_id, "tools_used": tools_used, "analysis": None}

        content = (result.data or {}).get("content") if isinstance(result.data, dict) else None
        if content is None:
            content = result.output or ""
        source = SourceDocument.from_content(path, str(content))
        self._debug(f"[file_size] {source.size}", force=True)
        core.tasks.log(task_id, f"Lecture — {source.size} caractères", level="info",
                       data={"phase": "read", "size": source.size, "path": path})

        chunks = split_into_chunks(source.content)
        self._debug(f"[security] chunks={len(chunks)}", force=True)
        chunk_outputs: list[str] = []
        findings: list[AnalysisFinding] = []
        for index, (start_offset, chunk) in enumerate(chunks, 1):
            progress = 0.15 + 0.65 * (index / max(1, len(chunks)))
            core.tasks.set_status(task_id, "running", progress=progress)
            core.activity(title=f"Analyse {index}/{len(chunks)}", detail=target,
                          kind="security", state="THINKING")
            core.events.emit("security.analysis.progress", {
                "task_id": task_id, "phase": "chunk", "current": index,
                "total": len(chunks), "file": target, "read_only": True})
            chunk_start_line = source.content[:start_offset - 1].count("\n") + 1
            prompt = (
                "Analyse uniquement ce fragment de code pour les failles de sécurité. "
                "Ne propose aucune écriture et ne modifie aucune source. Réponds en JSON "
                "avec une liste findings, chaque élément ayant severity, category, line, "
                "evidence, risk et recommendation. Les lignes sont relatives au fragment.\n\n"
                f"Fichier: {path}\nFragment {index}/{len(chunks)} (ligne de départ {chunk_start_line}):\n"
                f"```\n{chunk}\n```"
            )
            response = core.llm.chat(
                [ChatMessage(role="system", content=(
                    "Tu es un auditeur de sécurité applicative. Tu es strictement en "
                    "lecture seule. Tu ne dois appeler aucun outil et tu ne dois jamais "
                    "générer de contenu destiné à remplacer le fichier.")),
                 ChatMessage(role="user", content=prompt)],
                role="reasoning", tools=[], temperature=0.1, max_tokens=2500)
            if response.ok:
                raw = (response.text or "").strip()
                if raw:
                    chunk_outputs.append(raw[:8000])
                    parsed = parse_findings(raw)
                    for finding in parsed:
                        if finding.line is not None:
                            finding.line += chunk_start_line - 1
                    findings.extend(parsed)

        core.activity(title="Fusion des résultats", detail="Déduplication des findings", kind="security", state="VERIFYING")
        core.events.emit("security.analysis.progress", {
            "task_id": task_id, "phase": "merge", "current": len(chunks),
            "total": len(chunks), "file": target, "read_only": True})
        findings = deduplicate_findings(findings)

        # Une passe de fusion produit un résumé séparé du document source. Les
        # outils sont explicitement vides pour que cette passe ne puisse rien écrire.
        summary = ""
        if chunk_outputs:
            fusion = core.llm.chat(
                [ChatMessage(role="system", content=(
                    "Tu fusionnes des résultats d'audit de sécurité. Lecture seule : "
                    "ne demande aucun outil. Réponds par un résumé bref en français, "
                    "sans réécrire le fichier.")),
                 ChatMessage(role="user", content=(
                     f"Fichier {path}. Findings structurés déjà dédupliqués :\n"
                     + json.dumps([f.to_dict() for f in findings], ensure_ascii=False)[:18000]
                     + "\nRésultats des fragments :\n" + "\n---\n".join(chunk_outputs)[:12000]))],
                role="reasoning", tools=[], temperature=0.1, max_tokens=1200)
            if fusion.ok and (fusion.text or "").strip() and not fusion.tool_calls:
                summary = fusion.text.strip()[:3000]

        # Relecture indépendante : le pipeline n'écrit rien, mais la source
        # distante peut avoir changé par un autre acteur pendant l'audit.
        after_result = core.runner.run(read_tool, read_args, agent="jarvis", task_id=task_id,
                                       conversation_id=conversation_id,
                                       execution_policy=policy)
        tools_used.append(read_tool)
        source_after = source
        if after_result.ok:
            after_content = ((after_result.data or {}).get("content")
                             if isinstance(after_result.data, dict) else None)
            if after_content is None:
                after_content = after_result.output or ""
            source_after = SourceDocument.from_content(path, str(after_content))
        if source_after.hash != source.hash:
            message = (f"{target} a changé pendant l'analyse. Rapport abandonné ; "
                       "aucune modification n'a été effectuée.")
            self._debug(f"[file_hash_before] {source.hash}", force=True)
            self._debug(f"[file_hash_after] {source_after.hash}", force=True)
            core.tasks.fail(task_id, message)
            return {"ok": False, "response": message, "task_id": task_id,
                    "conversation_id": conversation_id, "tools_used": tools_used,
                    "analysis": {"file": path, "read_only": True,
                                 "source_hash_before": source.hash,
                                 "source_hash_after": source_after.hash}}

        result_model = AnalysisResult(
            file=path, findings=findings, summary=summary,
            source_hash_before=source.hash, source_hash_after=source_after.hash,
            source_size=source.size, chunks=len(chunks))
        count = len(findings)
        if count:
            response_text = (f"🔒 Analyse en lecture seule terminée : {count} vulnérabilité(s) "
                             f"potentielle(s) trouvée(s) dans {target}. "
                             "Aucune modification n'a été effectuée.")
        else:
            response_text = (f"🔒 Analyse en lecture seule terminée : aucune vulnérabilité "
                             f"détectée automatiquement dans {target}. "
                             "Aucune modification n'a été effectuée.")
        self._debug(f"[write_policy] DENY", force=True)
        self._debug(f"[security] findings={count}", force=True)
        self._debug(f"[file_hash_before] {source.hash}", force=True)
        self._debug(f"[file_hash_after] {source.hash}", force=True)
        core.tasks.complete(task_id, response_text)
        core.agents.set_state("jarvis", "standby")
        core.events.emit("jarvis.state", {"state": "SPEAKING", "reason": "security_analysis"})
        core.conversations.add_message(conversation_id, "assistant", response_text, meta={
            "intent": "security_analysis", "read_only": True, "write_allowed": False,
            "file": path, "findings": count, "source_hash": source.hash,
            "chunks": len(chunks), "tools": tools_used})
        return {"ok": True, "response": response_text, "task_id": task_id,
                "conversation_id": conversation_id, "tools_used": tools_used,
                "analysis": result_model.to_dict()}

    def _execute_direct_image_intent(
        self, image_intent: dict[str, Any], text: str, *, task_id: str,
        conversation_id: str, confirmation_id: str = "",
    ) -> dict[str, Any]:
        """Execute one image tool without allowing the general LLM to reroute it."""
        core = self._core
        action = str(image_intent.get("action") or "image.generate")
        call = self._forced_image_call(image_intent, text)
        self._debug(f"DIRECT IMAGE EXECUTION → {action}", force=True)
        core.tasks.set_status(task_id, "running", progress=0.12)
        core.tasks.log(task_id, f"Exécution directe : {action}", level="tool",
                       data=self._safe_args(call.arguments))
        core.agents.set_state("jarvis", "active", action=action, task_id=task_id)
        core.events.emit("jarvis.state", {"state": "ACTING", "reason": action})
        core.events.emit("tool.started", {"tool_id": action, "name": action})
        try:
            result = core.runner.run(action, call.arguments, agent="jarvis",
                                     task_id=task_id, conversation_id=conversation_id,
                                     confirmation_id=confirmation_id)
        except ConfirmationRequired as exc:
            pending = exc.pending
            core.tasks.set_status(task_id, "waiting_confirmation")
            core.agents.set_state("jarvis", "standby")
            core.conversations.add_message(
                conversation_id, "assistant", exc.message,
                meta={"confirmation_id": pending.id, "risk": pending.risk})
            return {"ok": True, "response": exc.message, "task_id": task_id,
                    "conversation_id": conversation_id, "tools_used": [],
                    "needs_confirmation": {"id": pending.id, "action": pending.action,
                                            "risk": pending.risk,
                                            "risk_label": RISK_LABELS.get(pending.risk, pending.risk),
                                            "reason": pending.reason}}

        used_tools = [action]
        image_jobs = [a for a in (result.artifacts or [])
                      if a.get("type") == "image" and a.get("job_id")]
        core.events.emit("tool.completed", {"tool_id": action, "metrics_recorded": True,
                                            "ok": result.ok,
                                            "preview": (result.output or "")[:200]})
        if not result.ok or not image_jobs:
            detail = (result.output or "Le moteur image n'a pas renvoyé d'image.").strip()
            response = (detail if detail.lower().startswith("la génération a échoué")
                        else f"La génération a échoué. {detail}")[:600]
            core.tasks.fail(task_id, response)
            core.agents.set_state("jarvis", "error", error=response)
            core.conversations.add_message(
                conversation_id, "assistant", response,
                meta={"image_intent": action, "tools": used_tools, "error": True})
            return {"ok": False, "response": response, "task_id": task_id,
                    "conversation_id": conversation_id, "tools_used": used_tools,
                    "images": []}

        response = "C'est prêt. Voici l'image."
        core.tasks.complete(task_id, response)
        core.agents.set_state("jarvis", "standby")
        core.events.emit("jarvis.state", {"state": "SPEAKING", "reason": "image"})
        meta = {"image_jobs": [a["job_id"] for a in image_jobs],
                "tools": used_tools, "task_id": task_id, "direct": True}
        message = core.conversations.add_message(conversation_id, "assistant", response, meta=meta)
        for artifact in image_jobs:
            core.imagegen.attach_message(artifact["job_id"], message["id"])
        core.conversations.maybe_title(conversation_id, text)
        return {"ok": True, "response": response, "task_id": task_id,
                "conversation_id": conversation_id, "tools_used": used_tools,
                "images": [{"job_id": a["job_id"], "url": a.get("url", "")} for a in image_jobs]}

    # Formulations qui trahissent un repli « recherche web » alors qu'une image
    # vient d'être produite : la réponse est alors remplacée.
    _WEB_FLAVOUR = re.compile(
        r"r[ée]sultats? de recherche|https?://|dall[·.]?e|midjourney|stable diffusion online|"
        r"voici des liens|tu peux (?:utiliser|essayer)|je ne peux pas g[ée]n[ée]rer",
        re.IGNORECASE)

    @classmethod
    def _image_success_text(cls, final_text: str) -> str:
        """L'image est affichée : la phrase doit être courte, juste et sans liens."""
        text = (final_text or "").strip()
        if not text or len(text) > 240 or cls._WEB_FLAVOUR.search(text):
            return "C'est prêt. Voici l'image."
        return text

    @classmethod
    def _model_success_text(cls, final_text: str) -> str:
        """Le modele 3D est affiche : la phrase doit etre courte, juste, sans liens."""
        text = (final_text or "").strip()
        if not text or len(text) > 260 or cls._WEB_FLAVOUR.search(text):
            return "C'est pret. Voici le modele 3D."
        return text

    @staticmethod
    def _forced_blender_call(blender_intent: dict[str, Any], text: str) -> ToolCall:
        """Construit l'appel d'outil 3D que le modele aurait du produire."""
        action = blender_intent["action"]
        if action == "blender.create_model":
            arguments: dict[str, Any] = {"prompt": text}
        elif action == "blender.modify_model":
            arguments = {"instruction": text}
        elif action == "blender.animate":
            arguments = {"clip": ""}
        elif action == "blender.export":
            arguments = {"format": "glb"}
        else:
            arguments = {}
        return ToolCall(id="forced_blender", name=action, arguments=arguments)

    @staticmethod
    def _forced_avatar_update_call(avatar_intent: dict[str, Any], text: str) -> ToolCall:
        """Construit l'appel d'outil d'avatar que le modèle aurait dû produire.

        Priorité : si un fichier image local est mentionné dans la demande on
        l'enregistre d'abord (avatar.reference.add), sinon on lance directement
        avatar.update_from_reference avec les options issues du texte.
        """
        import re as _re
        m = _re.search(r"[^\s\"'’`“”]+\.(?:png|jpe?g|webp|bmp|tiff?|gif)\b", text, _re.IGNORECASE)
        path = (m.group(0).strip().rstrip(".,;:!?") if m else "") or ""
        if path:
            arguments: dict[str, Any] = {
                "image_path": path,
                "reference_type": "face",
                "options": {},
            }
            return ToolCall(id="forced_avatar_ref_add", name="avatar.reference.add",
                            arguments=arguments)
        arguments = {"instruction": text, "reference_id": None, "options": {}}
        return ToolCall(id="forced_avatar_update", name="avatar.update_from_reference",
                        arguments=arguments)

    @staticmethod
    def _forced_image_call(image_intent: dict[str, Any], text: str) -> ToolCall:
        """Construit l'appel d'outil que le modèle aurait dû produire."""
        action = image_intent["action"]
        if action == "image.upscale":
            arguments: dict[str, Any] = {}
        elif action == "image.edit":
            arguments = {"instruction": text}
        elif action in {"image.variation", "image.improve"}:
            arguments = {}
        else:
            # The golden Z-Image adapter receives the user's words verbatim.
            arguments = {"prompt": text}
        return ToolCall(id="forced_image", name=action, arguments=arguments)

    def _force_execute(
        self, intent: ConnectorIntent, messages: list[ChatMessage], original_text: str,
        last_model_text: str, task_id: str, conversation_id: str, confirmation_id: str,
        used_tools: list[str],
    ) -> tuple[str, Any]:
        """Execute l'outil réel, sans jamais présumer du résultat.

        Retourne ("response", texte) après un résumé LLM du résultat réel, ou
        ("confirmation", payload) si la permission l'exige.
        """
        core = self._core
        tool_id = intent.tool_id
        self._debug(f"FORCED EXECUTION → {tool_id} "
                    + json.dumps(self._safe_args(intent.arguments), ensure_ascii=False))
        core.events.emit("jarvis.state", {"state": "ACTING", "reason": tool_id})
        core.tasks.log(task_id, f"Exécution directe : {tool_id}", level="tool",
                       data=self._safe_args(intent.arguments))
        try:
            result = core.runner.run(tool_id, intent.arguments, agent="jarvis",
                                     task_id=task_id, conversation_id=conversation_id,
                                     confirmation_id=confirmation_id)
        except ConfirmationRequired as exc:
            pending = exc.pending
            core.tasks.set_status(task_id, "waiting_confirmation")
            core.tasks.log(task_id, f"Confirmation requise : {pending.action}", level="warn")
            core.conversations.add_message(conversation_id, "assistant", exc.message,
                                           meta={"confirmation_id": pending.id, "risk": pending.risk})
            core.agents.set_state("jarvis", "standby")
            core.events.emit("jarvis.state", {"state": "WAITING", "reason": "confirmation"})
            payload = {
                "id": pending.id, "action": pending.action, "risk": pending.risk,
                "risk_label": RISK_LABELS.get(pending.risk, pending.risk), "reason": pending.reason,
            }
            return "confirmation", (exc.message, payload)

        used_tools.append(tool_id)
        core.events.emit("tool.completed", {"tool_id": tool_id, "ok": result.ok,
                                            "metrics_recorded": True,
                                            "preview": (result.output or "")[:200]})
        output = result.output or ("Terminé." if result.ok else "Échec sans détail.")
        synthetic = ToolCall(id="direct_forced", name=tool_id, arguments=dict(intent.arguments))
        messages.append(ChatMessage(role="assistant", content=last_model_text or original_text,
                                    tool_calls=[synthetic]))
        messages.append(ChatMessage(role="tool", content=output[:20000],
                                    tool_call_id=synthetic.id, name=tool_id))
        self._debug(f"TOOL RESULT SENT TO LLM (forced) : {output[:300]!r}")

        summary = core.llm.chat(
            messages + [ChatMessage(role="user", content=(
                "Résume en français, de façon concise, ce résultat RÉEL de l'outil. "
                "Ne mentionne aucune action qui n'a pas été réellement exécutée."))],
            role="default", temperature=0.2)
        if summary.ok and (summary.text or "").strip():
            return "response", summary.text.strip()
        return "response", output or ("Terminé." if result.ok
                                      else "Il n'a pas été possible d'exécuter la demande.")

    def _debug(self, message: str, *, force: bool = False) -> None:
        """`force=True` : trace toujours écrite (routage d'intention, jobs image).

        Une console Windows en cp1252 ne doit jamais faire échouer une requête :
        le message est dégradé en ASCII plutôt que de lever UnicodeEncodeError.
        """
        if not (force or os.getenv("JARVIS_DEBUG", "0").lower() in {"1", "true", "yes", "on"}):
            return
        line = f"[orchestrator] {message}"
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            print(line.encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:
            pass

    def _latest_reference_features(self) -> dict[str, Any] | None:
        """Features VISION de la référence la plus récente, si elle existe.

        Le spécialiste Blender ne regarde pas les images : il reçoit ce JSON.
        Renvoie None si aucune référence ou si l'analyse a échoué — dans ce cas
        c'est l'outil avatar qui refusera proprement.
        """
        core = self._core
        try:
            references = core.avatar_ref.list(limit=1)
        except Exception:
            return None
        if not references:
            return None
        reference = references[0]
        features = reference.get("extracted_features") or {}
        if not features.get("analysis_success"):
            try:
                features = core.avatar_ref.analyze(reference["id"]) or {}
            except Exception:
                return None
        if not features.get("analysis_success"):
            return None
        self._blender_log(f"vision features reference={reference['id']} "
                          f"model={features.get('vision_model', '')}")
        return {k: v for k, v in features.items()
                if k not in {"raw_analysis", "analysis_error"}}

    # -- routage vers le spécialiste 3D -------------------------------------
    def _blender_log(self, message: str) -> None:
        line = f"[blender-agent] {message}"
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            print(line.encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:
            pass

    # Outils de « présence » (déplacement, geste, regard) : ils pilotent le
    # viewer 3D, pas l'atelier Blender. Ils ne sont pas exposés au spécialiste.
    _BLENDER_PRESENCE_TOOLS = {"avatar.move", "avatar.gesture", "avatar.look"}

    def _blender_tools(self, tools, action: str = "") -> list:
        """Outils 3D exposés au spécialiste, filtrés par action.

        Exigence 4 : ne JAMAIS exposer calendar/email/SSH/weather/mémoire au
        spécialiste, et ne lui donner QUE les outils utiles à l'action
        détectée. `jarvis-blender` tourne avec un petit num_ctx : les 27
        schémas d'outils 3D le saturent et le font boucler.
        """
        tools = [t for t in tools if t.id not in self._BLENDER_PRESENCE_TOOLS]
        subset = blender_tools_for(action) if action else ()
        if subset:
            allowed = set(subset)
            tools = [t for t in tools if t.id in allowed]
        return tools

    def delegate_to_blender(
        self, text: str, action: str, *, task_id: str = "", conversation_id: str = "",
        features: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Confie une demande 3D au spécialiste `jarvis-blender`.

        Le modèle général de JARVIS n'improvise plus de Blender : il délègue.
        Si le spécialiste est absent, on le DIT — pas de bascule silencieuse
        vers le modèle général pour une génération 3D.
        """
        core = self._core
        try:
            print(f"[agent-router] agent=blender action={action}", flush=True)
        except Exception:
            pass

        status = core.llm.blender_status()
        if not status["available"]:
            self._blender_log(f"indisponible — {status['reason']}")
            message = ("Le spécialiste Blender n'est pas disponible. "
                       "Installe le modèle `jarvis-blender` dans Ollama "
                       "(ou indique-le dans Settings → AI Providers). "
                       "Je ne lance pas une génération 3D avec le modèle général.")
            core.tasks.complete(task_id, message)
            core.agents.set_state("jarvis", "standby")
            core.conversations.add_message(
                conversation_id, "assistant", message,
                meta={"blender_agent": "unavailable", "action": action})
            return {"ok": False, "response": message, "task_id": task_id,
                    "conversation_id": conversation_id, "tools_used": [],
                    "blender_agent_missing": True}

        self._blender_log(f"model={status['model']} provider={status['provider']}")
        tools = self._blender_tools(core.agents.allowed_tools("blender", registry), action)
        self._blender_log("tools=[" + ", ".join(t.id for t in tools) + "]")

        instruction = (
            f"Demande de l'utilisateur : {text}\n"
            f"Intention détectée : {action}\n"
            "Commence par inspecter la scène réelle avant toute modification."
        )
        if features:
            # Exigence 6 : le spécialiste n'invente pas ce que contient l'image.
            # L'analyse vient du moteur de VISION, il la reçoit en JSON.
            instruction += (
                "\n\nAnalyse visuelle de la référence (produite par le moteur de "
                "vision — ne l'invente pas, utilise ces valeurs) :\n"
                + json.dumps(features, ensure_ascii=False, indent=1)[:4000])

        result = self.run_agent("blender", instruction, task_id=task_id,
                                conversation_id=conversation_id, parent_agent="jarvis",
                                action=action)
        used = result.get("tools_used") or []
        self._blender_log(f"terminé ok={result.get('ok')} tools_used={used}")

        response = (result.get("output") or "").strip() or "Terminé."
        core.tasks.complete(task_id, response)
        core.agents.set_state("jarvis", "standby")
        core.events.emit("jarvis.state", {"state": "SPEAKING", "reason": "blender_agent"})
        core.conversations.add_message(
            conversation_id, "assistant", response,
            meta={"blender_agent": status["model"], "action": action,
                  "tools_used": used})
        return {"ok": bool(result.get("ok")), "response": response, "task_id": task_id,
                "conversation_id": conversation_id, "tools_used": used,
                "agent": "blender", "model": status["model"]}

    @staticmethod
    def _recover_text_tool_calls(text: str, allowed: set[str]) -> list[ToolCall]:
        return recover_text_tool_calls(text, allowed)

    # -- agents spécialisés -------------------------------------------------
    def run_agent(
        self, agent_id: str, instruction: str, *, task_id: str = "", conversation_id: str = "",
        parent_agent: str = "jarvis", action: str = "",
    ) -> dict[str, Any]:
        core = self._core
        spec = AGENTS.get(agent_id)
        if not spec:
            return {"ok": False, "output": f"Agent inconnu: {agent_id}"}
        if not core.agents.is_enabled(agent_id):
            return {"ok": False, "output": f"L'agent {spec.name} est désactivé."}

        core.agents.set_state(agent_id, "active", action=instruction[:120], task_id=task_id)
        tools = core.agents.allowed_tools(agent_id, registry)
        if agent_id == "blender":
            tools = self._blender_tools(tools, action)
        schemas = [t.llm_schema() for t in tools]
        system = spec.system_prompt.format(user=core.settings.get("general", "user_name", "Jérôme"))
        system += (
            "\n\nOutils disponibles pour toi : "
            + ", ".join(t.id for t in tools)
            + "\nLes identifiants sont dans un coffre : utilise connector_id, jamais de mot de passe."
        )
        context = core.memory.context_for(instruction)
        if context:
            system += "\n\nContexte mémorisé :\n" + "\n".join(f"- {m['content']}" for m in context[:8])
        messages = [ChatMessage(role="system", content=system),
                    ChatMessage(role="user", content=instruction)]

        output_parts: list[str] = []
        used: list[str] = []
        allowed_names = {t.id for t in tools}

        if agent_id == "blender" and action in {"blender.inspect", "avatar.craft"}:
            inspect_id = "blender.inspect" if "blender.inspect" in allowed_names else "avatar.engine.inspect"
            if inspect_id in allowed_names:
                self._blender_log(f"exécution déterministe {inspect_id}")
                try:
                    forced = core.runner.run(inspect_id, {}, agent=agent_id,
                                             task_id=task_id, conversation_id=conversation_id)
                except ConfirmationRequired as exc:
                    core.agents.set_state(agent_id, "standby")
                    return {"ok": False, "output": exc.message, "agent": agent_id,
                            "needs_confirmation": {"id": exc.pending.id, "action": exc.pending.action,
                                                   "risk": exc.pending.risk}}
                used.append(inspect_id)
                messages.append(ChatMessage(
                    role="assistant", content="",
                    tool_calls=[ToolCall(id="forced_inspect", name=inspect_id, arguments={})]))
                messages.append(ChatMessage(
                    role="tool", content=(forced.output or "Terminé.")[:20000],
                    tool_call_id="forced_inspect", name=inspect_id))

        for _ in range(spec.max_iterations):
            response = core.llm.chat(messages, role=spec.model_role, tools=schemas)
            if not response.ok:
                core.agents.set_state(agent_id, "error", error=response.error)
                return {"ok": False, "output": f"{spec.name} : {response.error}", "agent": agent_id}
            if not response.tool_calls:
                recovered = self._recover_text_tool_calls(response.text, allowed_names)
                if recovered:
                    if agent_id == "blender":
                        self._blender_log(
                            "appel d'outil récupéré depuis le texte : "
                            + ", ".join(c.name for c in recovered))
                    response.tool_calls = recovered
                    response.text = ""
                else:
                    cleaned = strip_tool_call_text(response.text, allowed_names)
                    if cleaned:
                        output_parts.append(cleaned)
                    break
            messages.append(ChatMessage(role="assistant", content=response.text, tool_calls=response.tool_calls))
            for call in response.tool_calls:
                if task_id:
                    core.tasks.log(task_id, f"{spec.name} → {call.name}", level="tool")
                core.agents.set_state(agent_id, "active", action=call.name, task_id=task_id)
                try:
                    result = core.runner.run(call.name, call.arguments, agent=agent_id,
                                             task_id=task_id, conversation_id=conversation_id)
                except ConfirmationRequired as exc:
                    core.agents.set_state(agent_id, "standby")
                    return {"ok": False, "output": exc.message, "agent": agent_id,
                            "needs_confirmation": {"id": exc.pending.id, "action": exc.pending.action,
                                                   "risk": exc.pending.risk}}
                used.append(call.name)
                if agent_id == "blender":
                    milestones = {
                        "blender.inspect": "scene inspected",
                        "avatar.reference.analyze": "reference analysed",
                        "avatar.update_from_reference": "revision created",
                        "blender.generate_preview": "preview published",
                        "blender.render": "preview published",
                        "avatar.revision.list": "evaluation received",
                    }
                    if call.name in milestones:
                        self._blender_log(milestones[call.name])
                messages.append(ChatMessage(role="tool", content=(result.output or "Terminé.")[:20000],
                                            tool_call_id=call.id, name=call.name))
        core.agents.set_state(agent_id, "standby")
        text = "\n".join(p for p in output_parts if p).strip()
        if not text or looks_like_tool_call_dump(text, allowed_names):
            text = "Terminé." if used else "Terminé."
        return {"ok": True, "output": text, "agent": agent_id, "tools_used": used}

    # -- reprise après confirmation ----------------------------------------
    def _refresh_document_after_write(self, pending, task_id: str, conversation_id: str) -> None:
        """Relit le fichier écrit et met à jour le DocumentStore + Coding."""
        core = self._core
        path = str(pending.arguments.get("path") or "")
        if not path:
            return
        ssh = pending.tool == "ssh.write_file"
        args = {"path": path}
        connector_id = str(pending.arguments.get("connector_id") or "")
        if connector_id:
            args["connector_id"] = connector_id
        try:
            verify = core.runner.run("ssh.read_file" if ssh else "fs.read", args,
                                     agent="jarvis", task_id=task_id,
                                     conversation_id=conversation_id)
        except Exception:
            return
        if not verify.ok:
            return
        content = (verify.data or {}).get("content") or verify.output or ""
        language = path.rsplit(".", 1)[-1].lower() if "." in path else "plaintext"
        doc = core.documents.add_from_tool(source="ssh" if ssh else "fs",
                                           connector_id=connector_id, path=path,
                                           content=content, language=language)
        core.documents.open(doc.id)
        core.events.emit("code.file.opened", core.documents.event_payload(doc))

    def resume_confirmation(self, confirmation_id: str, approved: bool) -> dict[str, Any]:
        core = self._core
        pending = core.permissions.resolve(confirmation_id, approved)
        if not pending:
            return {"ok": False, "response": "Cette demande de confirmation a expiré."}
        task_id = pending.task_id
        conversation_id = ""
        if task_id:
            task = core.tasks.get(task_id)
            conversation_id = (task or {}).get("conversation_id", "")
        if not approved:
            if task_id:
                core.tasks.set_status(task_id, "cancelled", error="Refusée par l'utilisateur.")
            message = "Annulé."
            if conversation_id:
                core.conversations.add_message(conversation_id, "assistant", message)
            return {"ok": True, "response": message, "task_id": task_id}

        try:
            result = core.runner.run(pending.tool, pending.arguments, agent="jarvis", task_id=task_id,
                                     conversation_id=conversation_id, confirmed=True)
        except ConfirmationRequired:
            return {"ok": False, "response": "Confirmation impossible à rejouer."}
        # Une écriture confirmée doit se refléter dans l'environnement Coding :
        # on relit le fichier sur le serveur et on rafraîchit le document ouvert.
        if result.ok and pending.tool in {"ssh.write_file", "fs.write"}:
            self._refresh_document_after_write(pending, task_id, conversation_id)
        output = result.output or ("Terminé." if result.ok else "Échec.")
        if task_id:
            if result.ok:
                core.tasks.complete(task_id, output)
            else:
                core.tasks.fail(task_id, output)
        if conversation_id:
            core.conversations.add_message(conversation_id, "assistant", output, meta={"confirmed": True})
        return {"ok": result.ok, "response": output, "task_id": task_id}

    # -- helpers ------------------------------------------------------------
    def _build_messages(self, spec, text: str, conversation_id: str, tools,
                        skip_recall: bool = False) -> list[ChatMessage]:
        core = self._core
        user_name = core.settings.get("general", "user_name", "Jérôme")
        system = spec.system_prompt.format(user=user_name)

        connectors = core.connectors.list(include_disabled=False)
        if connectors:
            system += "\n\nConnecteurs enregistrés (utilise ces connector_id) :\n" + "\n".join(
                f"- {c['id']} : {c['name']} ({c['type']})" for c in connectors[:25])
        else:
            system += ("\n\nAucun connecteur n'est encore enregistré. Si une demande en nécessite un, "
                       "dis-le brièvement et indique Settings → Connectors.")

        agents_line = ", ".join(f"{a.id} ({a.role})" for a in AGENTS.values() if a.id != "jarvis")
        system += f"\n\nAgents spécialisés disponibles via agent.delegate : {agents_line}."

        # Racine réelle du(des) connecteur(s) SSH pour réécrire toute mention
        # d'une racine générique interdite (/var/www/html…) dans l'historique.
        ssh_root = ""
        ssh_connectors = [c for c in core.connectors.list(include_disabled=False)
                          if c.get("type") == "ssh"]
        if len(ssh_connectors) == 1:
            ssh_root = ((ssh_connectors[0].get("config") or {}).get("working_directory")
                        or (ssh_connectors[0].get("config") or {}).get("deployment_path")
                        or (ssh_connectors[0].get("config") or {}).get("remote_path") or "")

        context = core.memory.context_for(text, conversation_id=conversation_id)
        if context:
            system += "\n\nCe que tu sais déjà :\n" + "\n".join(
                f"- {scrub_forbidden_roots(m['content'], ssh_root)}" for m in context)

        # RECALL : procédures déjà connues, vraiment pertinentes (jamais toute la base).
        # Sauté pour une action connecteur : le rappel ne doit pas biaiser l'exécution
        # ni pousser le modèle à réciter une procédure obsolète ou inventée.
        if not skip_recall:
            recall_section = core.auto_learning._build_recall_section(text)
            if recall_section:
                system += scrub_forbidden_roots(recall_section, ssh_root)

        metrics = core.monitor.snapshot()
        system += (f"\n\nÉtat machine : CPU {metrics['cpu']['percent']}%, "
                   f"mémoire {metrics['memory']['percent']}%, disque {metrics['disk']['percent']}%, "
                   f"réseau {metrics['network']['status']}.")
        system += f"\nDate et heure : {time.strftime('%A %d %B %Y, %H:%M')}."
        extra = core.settings.get("ai", "system_prompt_extra", "")
        if extra:
            system += f"\n\n{scrub_forbidden_roots(extra, ssh_root)}"

        messages = [ChatMessage(role="system", content=system)]
        history_limit = int(core.settings.get("ai", "max_context_messages", 24))
        for m in core.conversations.messages(conversation_id, limit=history_limit):
            if m["role"] in {"user", "assistant"} and m["content"]:
                content = scrub_forbidden_roots(m["content"], ssh_root)[:6000]
                messages.append(ChatMessage(role=m["role"], content=content))
        if not messages or messages[-1].content != text:
            messages.append(ChatMessage(role="user", content=text))
        return messages

    @staticmethod
    def _safe_args(arguments: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for k, v in (arguments or {}).items():
            if k in {"password", "token", "api_key", "secret", "private_key"}:
                out[k] = "[masqué]"
            else:
                out[k] = (v[:300] if isinstance(v, str) else v)
        return out

    def _offline_fallback(self, text: str, error: str) -> str:
        """Sans LLM, JARVIS reste utile : il dit ce qu'il peut encore faire."""
        core = self._core
        hints = []
        if core.connectors.by_type("ssh"):
            hints.append("commandes SSH")
        if core.connectors.by_type("cpanel"):
            hints.append("cPanel")
        if core.connectors.by_type("n8n"):
            hints.append("workflows n8n")
        hints.append("diagnostic système, tâches, agenda, mémoire")
        return (f"Je ne peux pas raisonner : {error[:160]}\n"
                f"Les actions directes restent disponibles ({', '.join(hints)}). "
                f"Ouvre Settings → AI Providers pour connecter un modèle.")
