"""Routeur déterministe des commandes d'ouverture/lecture de fichiers.

Exécuté AVANT le LLM : « affiche blog.php », « lis includes/config.php »,
« montre le contenu de forums.php » sont résolus ici avec le vRAI
`working_directory` du connecteur SSH. Le modèle ne choisit JAMAIS le chemin.
"""
from __future__ import annotations

import re
import time

from .build import trace
from .goals import has_write_intent
from .intents import detect_read_only_intent
from .remote_paths import InvalidRemoteRootError, MissingRemoteWorkingDirectoryError, resolveRemotePath

_LOCAL_HINT = re.compile(
    r"\b(?:en local|sur mon pc|sur mon ordinateur|dans\s+C:[\\/]|dans\s+jarvis-windows|"
    r"sur le bureau|localement)\b", re.I)

# Capture aussi les sous-dossiers : « includes/config.php » -> includes/config.php
_FILE_PATTERN = re.compile(
    r"\b(?:affiche(?:\s*-?moi)?|montre(?:\s*-?moi)?|lis|lire|ouvre|donne(?:\s*-?moi)?|"
    r"voir|regarde)\b[^.\n]*?\b([A-Za-z0-9_./-]+\.(?:php|html?|css|js|json|py|ya?ml|sql|sh|txt|md))\b",
    re.I)


class FileCommandRouter:
    def __init__(self, core):
        self.core = core

    def _ssh_candidates(self) -> list[dict]:
        """Connecteurs SSH réels : d'abord connectés, sinon simplement activés.

        Un statut 'error' (dû à un échec de lecture précédent) ne doit JAMAIS
        désactiver silencieusement le routage déterministe.
        """
        connected = self.core.connectors.active("ssh")
        if connected:
            return connected
        enabled = [c for c in self.core.connectors.list(include_disabled=False)
                   if c.get("type") == "ssh"]
        return enabled

    def execute(self, text: str, conversation_id: str) -> dict | None:
        if detect_read_only_intent(text).intent == "security_audit_readonly":
            return None
        if _LOCAL_HINT.search(text or ""):
            return None
        # « ouvre X et écris ... » est une écriture, pas une lecture.
        if has_write_intent(text or ""):
            trace("routing_blocked reason=write_intent")
            return None
        m = _FILE_PATTERN.search(text or "")
        if not m:
            return None

        candidates = self._ssh_candidates()
        if len(candidates) != 1:
            trace(f"routing_blocked reason=connector_count={len(candidates)}")
            return None
        public = candidates[0]
        connector = self.core.connectors.raw(public["id"])
        if not connector:
            return None
        cfg = (connector or {}).get("config") or {}
        root = cfg.get("working_directory") or cfg.get("deployment_path") or cfg.get("remote_path") or ""
        if not root:
            return None

        requested = m.group(1)
        trace(f"input={text!r}",
              f"intent=OPEN_REMOTE_FILE",
              f"scope=remote",
              f"connector_id={connector['id']}",
              f"connector_working_directory={root}",
              f"active_remote_root={root}",
              f"requested_path={requested}")
        try:
            path = resolveRemotePath(connector, requested, {"remote_root": root})
        except (InvalidRemoteRootError, MissingRemoteWorkingDirectoryError) as exc:
            return {"ok": False, "response": str(exc), "tools_used": []}
        trace(f"resolved_path={path}",
              f"selected_tool=ssh.read_file",
              f"tool_arguments={{connector_id: {connector['id']}, path: {path}}}")

        self.core.active_task_context.update({
            "scope": "remote", "target_type": "remote_server", "connector_type": "ssh",
            "connector_id": connector["id"], "remote_root": root,
            "deployment_path": cfg.get("deployment_path") or cfg.get("remote_path") or "",
            "project": cfg.get("local_project") or "", "last_remote_path": path,
            "last_file": requested, "updated_at": time.time(),
        })
        result = self.core.runner.run(
            "ssh.read_file", {"connector_id": connector["id"], "path": path},
            agent="jarvis", conversation_id=conversation_id,
        )
        if not result.ok:
            # Recherche STRICTEMENT bornée à la racine configurée.
            search = self.core.runner.run(
                "ssh.run",
                {"connector_id": connector["id"],
                 "command": f"find {root} -type f -name '{requested.split('/')[-1]}' -print -quit"},
                agent="jarvis", conversation_id=conversation_id,
            )
            fname = requested.split('/')[-1]
            found = re.search(rf"([^\s'\"`;:]+{re.escape(fname)})(?:\s|$)",
                              search.output or "", re.I)
            if not found:
                return {"ok": False,
                        "response": f"Je n'ai pas trouvé {requested} dans {root}.",
                        "tools_used": ["ssh.read_file", "ssh.run"]}
            path = found.group(1).strip("'\"`:,;")
            result = self.core.runner.run(
                "ssh.read_file", {"connector_id": connector["id"], "path": path},
                agent="jarvis", conversation_id=conversation_id,
            )
        if not result.ok:
            return {"ok": False, "response": result.output or "Lecture impossible.",
                    "tools_used": ["ssh.read_file"]}

        content = (result.data or {}).get("content") or result.output
        doc = self.core.documents.add_from_tool(
            source="ssh", connector_id=connector["id"], path=path,
            content=content, language=self._language(path))
        self.core.documents.open(doc.id)
        self.core.events.emit("code.file.opened", self.core.documents.event_payload(doc))
        self.core.active_task_context.update({
            "last_remote_path": path, "last_file": requested,
            "last_successful_tool": "ssh.read_file", "updated_at": time.time(),
        })
        response = self.core.documents.opened_response(doc, requested)
        self.core.conversations.add_message(
            conversation_id, "assistant", response,
            meta={"source": "ssh", "path": path, "raw_content": True},
        )
        trace(f"result=ok file_opened={path}")
        return {"ok": True, "response": response, "file_content": content,
                "source": "ssh", "connector_id": connector["id"], "path": path,
                "tools_used": ["ssh.read_file"]}

    @staticmethod
    def _language(requested: str) -> str:
        prefix = requested.rstrip("/ ")
        return prefix.rsplit(".", 1)[-1] if "." in prefix else "text"
