"""Route déterministe d'ÉDITION du document ouvert dans l'environnement Coding.

Symétrique de `file_router` (qui gère la LECTURE déterministe).

Pourquoi une route dédiée plutôt qu'une simple directive au modèle : sur un
modèle local de 8 B, « ajoute un commentaire en haut de X » produit presque
toujours une *explication* (« voici comment faire, ouvre ton éditeur… ») au
lieu d'un appel `ssh.write_file`. Le contenu, lui, le modèle sait le produire.

On sépare donc les deux responsabilités :
  - le modèle génère UNIQUEMENT le nouveau contenu complet du fichier ;
  - le code effectue l'écriture, la relecture et la vérification.

Le chemin, le connecteur et l'écriture ne dépendent jamais du modèle.
"""
from __future__ import annotations

import re
import time
import json
from hashlib import sha256

from .build import trace
from .goals import has_write_intent
from .intents import detect_read_only_intent
from .execution_policy import current_policy, is_readonly
from .llm.base import ChatMessage
from .tools.runner import ConfirmationRequired

# Un bloc markdown ```php … ``` renvoyé malgré la consigne doit être déballé,
# jamais écrit tel quel dans le fichier.
_FENCE = re.compile(r"^\s*```[A-Za-z0-9_+-]*\s*\n(.*?)\n?\s*```\s*$", re.S)

_SYSTEM = (
    "Tu es un éditeur de code. Tu reçois le contenu intégral d'un fichier et une "
    "instruction de modification.\n"
    "Tu réponds UNIQUEMENT avec le contenu intégral du fichier après modification.\n"
    "Règles absolues :\n"
    "- Pas de bloc markdown, pas de ```, pas de commentaire d'introduction, "
    "pas de conclusion, pas d'explication.\n"
    "- Le fichier complet, du premier au dernier caractère.\n"
    "- Tout ce que l'instruction ne demande pas de changer reste STRICTEMENT identique.\n"
    "- N'invente pas de contenu qui n'était pas là et qui n'est pas demandé."
)


def _unfence(text: str) -> str:
    m = _FENCE.match(text or "")
    return m.group(1) if m else (text or "")


def targets_active_document(text: str, doc) -> bool:
    """L'instruction vise-t-elle le document ouvert ?

    Vrai si elle nomme ce fichier, ou si elle ne nomme aucun autre fichier
    (« ajoute un commentaire en haut ») alors qu'un document est actif.
    """
    if not doc:
        return False
    named = re.findall(r"\b([\w.@-]+\.[A-Za-z0-9]{1,8})\b", text or "")
    if not named:
        return True
    return any(n.casefold() == doc.filename.casefold() for n in named)


class CodeEditRouter:
    """Applique une modification au document actif, de bout en bout."""

    def __init__(self, core) -> None:
        self.core = core

    def active_document(self):
        docs = self.core.documents
        return docs.documents.get(docs.active_document_id) if docs.active_document_id else None

    def should_handle(self, text: str) -> bool:
        if detect_read_only_intent(text).read_only or is_readonly(current_policy()):
            return False
        doc = self.active_document()
        if doc is None or doc.audit_jobs or (doc.read_only and doc.read_only_reason != "security_audit_readonly"):
            return False
        if not has_write_intent(text or ""):
            return False
        return targets_active_document(text, doc)

    def execute(self, text: str, conversation_id: str, task_id: str = "") -> dict | None:
        # Independent guard: direct callers cannot bypass should_handle().
        if detect_read_only_intent(text).read_only or is_readonly(current_policy()):
            self.core.audit.record(action="Édition refusée pendant un audit", tool="code.edit",
                                   status="denied", detail="WRITE_DENIED_READ_ONLY")
            return {"ok": False, "response": "WRITE_DENIED_READ_ONLY", "tools_used": []}
        if not self.should_handle(text):
            return None
        doc = self.active_document()
        if doc is None:
            return None
        policy = {"intent": "file_edit", "read_only": False, "write_allowed": True,
                  "require_write_confirmation": True}
        if not task_id:
            task_id = self.core.tasks.create(name=text[:200], kind="file_edit",
                conversation_id=conversation_id, meta={"execution_policy": policy})["id"]
        if not self.core.documents.allow_edit(doc, self.core.events):
            return {"ok": False, "response": "Le document est encore en lecture seule.",
                    "task_id": task_id, "tools_used": []}
        audit = self.core.active_task_context.get("last_security_audit") or {}
        audit_context = ("\nRapport précédent (contexte, seule la nouvelle demande autorise les changements):\n"
                         + json.dumps(audit, ensure_ascii=False)
                         if audit.get("file") == doc.absolute_path else "")
        tool = "ssh.write_file" if doc.source_type == "ssh" else "fs.write"
        trace(f"input={text!r}",
              "intent=EDIT_ACTIVE_DOCUMENT",
              f"document_id={doc.id}",
              f"absolute_path={doc.absolute_path}",
              f"source_type={doc.source_type}",
              f"selected_tool={tool}")

        # 1. Le modèle produit le nouveau contenu — et RIEN d'autre.
        response = self.core.llm.chat(
            [ChatMessage(role="system", content=_SYSTEM),
             ChatMessage(role="user", content=(
                 f"Fichier : {doc.absolute_path}\n"
                 f"Langage : {doc.language}\n\n"
                 f"--- CONTENU ACTUEL ---\n{doc.content}\n--- FIN ---\n\n"
                 f"Instruction : {text}\n{audit_context}\n"
                 "Réponds avec le contenu complet du fichier modifié, rien d'autre."))],
            role="coding", temperature=0.1, max_tokens=8192)
        if response.tool_calls or not response.ok or not (response.text or "").strip():
            return {"ok": False, "tools_used": [],
                    "response": "Le modèle n'a pas produit de nouveau contenu. "
                                "Fichier inchangé."}

        new_content = _unfence(response.text).rstrip("\n") + "\n"

        # 2. Garde-fous : on n'écrase jamais sur une sortie manifestement cassée.
        if not new_content.strip():
            return {"ok": False, "tools_used": [],
                    "response": "Contenu généré vide — écriture annulée, fichier inchangé."}
        if len(new_content) < len(doc.content) * 0.5 and len(doc.content) > 200:
            return {"ok": False, "tools_used": [],
                    "response": (f"Le contenu généré est anormalement court "
                                 f"({len(new_content)} contre {len(doc.content)} caractères) : "
                                 "écriture annulée pour ne rien perdre. Reformule la demande.")}
        if new_content == doc.content:
            return {"ok": True, "tools_used": [],
                    "response": f"{doc.filename} est déjà dans l'état demandé — aucune écriture."}

        # 3. Anti-écrasement : le distant a-t-il bougé depuis l'ouverture ?
        read_args = {"path": doc.absolute_path}
        if doc.connector_id:
            read_args["connector_id"] = doc.connector_id
        current = self.core.runner.run(tool.replace("write_file", "read_file").replace("fs.write", "fs.read"),
                                       read_args, agent="jarvis", task_id=task_id,
                                       conversation_id=conversation_id)
        remote_now = (current.data or {}).get("content") if current.ok else None
        if remote_now is None:
            remote_now = current.output if current.ok else None
        if remote_now is None:
            self.core.tasks.fail(task_id, "Lecture de contrôle impossible.")
            return {"ok": False, "response": "Lecture de contrôle impossible — écriture annulée.",
                    "tools_used": [], "task_id": task_id}
        if remote_now != doc.original_content:
            return {"ok": False, "tools_used": [tool.replace("write", "read")],
                    "response": (f"{doc.filename} a été modifié sur le serveur depuis son "
                                 "ouverture. Aucune écriture effectuée — recharge le fichier "
                                 "puis relance la demande.")}

        # 4. Écriture réelle.
        write_args = {"path": doc.absolute_path, "content": new_content,
                      "expected_sha256": sha256(remote_now.encode("utf-8")).hexdigest()}
        if doc.source_type == "ssh":
            write_args["mode"] = "write"
        if doc.connector_id:
            write_args["connector_id"] = doc.connector_id
        try:
            result = self.core.runner.run(tool, write_args, agent="jarvis", task_id=task_id,
                                          conversation_id=conversation_id, execution_policy=policy)
        except ConfirmationRequired as exc:
            # L'écriture distante est une action sensible : on rend la main à
            # l'utilisateur avec une vraie demande de confirmation plutôt qu'une
            # erreur brute. La reprise est gérée par resume_confirmation().
            pending = exc.pending
            self.core.tasks.set_status(task_id, "waiting_confirmation")
            self.core.conversations.add_message(
                conversation_id, "assistant", exc.message,
                meta={"confirmation_id": pending.id, "risk": pending.risk})
            return {"ok": True, "response": exc.message, "tools_used": [], "task_id": task_id,
                    "conversation_id": conversation_id,
                    "needs_confirmation": {
                        "id": pending.id, "action": pending.action,
                        "risk": pending.risk, "reason": pending.reason}}
        if not result.ok:
            return {"ok": False, "tools_used": [tool],
                    "response": f"Écriture échouée : {result.output or 'erreur inconnue'}"}

        # 5. Relecture + vérification exacte : on ne déclare jamais un succès
        #    sur la foi du code retour de l'écriture.
        verify = self.core.runner.run(
            "ssh.read_file" if doc.source_type == "ssh" else "fs.read",
            read_args, agent="jarvis", task_id=task_id, conversation_id=conversation_id)
        on_disk = (verify.data or {}).get("content") if verify.ok else None
        if on_disk is None and verify.ok:
            on_disk = verify.output
        if not verify.ok or on_disk != new_content:
            return {"ok": False, "tools_used": [tool],
                    "response": (f"{doc.filename} a été écrit mais la relecture ne "
                                 "correspond pas au contenu attendu. Vérifie le fichier.")}

        # 6. Le document ouvert reflète le disque, et Coding est rafraîchi.
        updated = self.core.documents.add_from_tool(
            source=doc.source_type, connector_id=doc.connector_id,
            path=doc.absolute_path, content=on_disk, language=doc.language)
        self.core.documents.open(updated.id)
        self.core.events.emit("code.file.opened", self.core.documents.event_payload(updated))
        self.core.active_task_context.update({
            "last_remote_path": doc.absolute_path, "last_file": doc.filename,
            "last_successful_tool": tool, "updated_at": time.time()})
        response_text = self.core.documents.opened_response(updated, doc.filename)
        message = (f"{doc.filename} modifié et vérifié sur "
                   f"{doc.source_type.upper()}. " + response_text)
        self.core.conversations.add_message(
            conversation_id, "assistant", message,
            meta={"source": doc.source_type, "path": doc.absolute_path, "raw_content": True})
        trace(f"result=ok file_written={doc.absolute_path} bytes={len(new_content)}")
        return {"ok": True, "response": message, "path": doc.absolute_path,
                "source": doc.source_type, "tools_used": [tool]}
