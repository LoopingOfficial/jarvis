"""Authoritative in-memory store for documents opened in the Coding surface."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha1
from typing import Any
import threading
import uuid


@dataclass
class CodeDocument:
    id: str
    filename: str
    language: str
    source_type: str
    connector_id: str
    absolute_path: str
    content: str
    original_content: str
    dirty: bool = False
    read_only: bool = False
    created_at: str = ""
    updated_at: str = ""


class DocumentStore:
    BUILD_ID = 'CODING_MERGE3_20260911_H'
    def __init__(self) -> None:
        self.documents: dict[str, CodeDocument] = {}
        self.open_document_ids: list[str] = []
        self.active_document_id: str | None = None
        self._acks = {}
        self._lock = threading.RLock()

    @staticmethod
    def document_id(source: str, connector_id: str, path: str) -> str:
        key = f"{source}\0{connector_id}\0{path}"
        return "doc-" + sha1(key.encode("utf-8")).hexdigest()[:20]

    def add_from_tool(self, *, source: str, connector_id: str, path: str,
                      content: str, language: str) -> CodeDocument:
        now = datetime.now(timezone.utc).isoformat()
        doc_id = self.document_id(source, connector_id, path)
        filename = path.replace("\\", "/").rsplit("/", 1)[-1] or path
        language = {'js': 'javascript', 'ts': 'typescript', 'py': 'python', 'sh': 'shell',
                    'yml': 'yaml', 'md': 'markdown', 'txt': 'plaintext', 'inc': 'php'}.get(language, language)
        doc = self.documents.get(doc_id)
        if doc is None:
            doc = CodeDocument(doc_id, filename, language, source, connector_id, path,
                               content, content, created_at=now, updated_at=now)
            self.documents[doc_id] = doc
        else:
            doc.content = content
            doc.original_content = content
            doc.language = language
            doc.updated_at = now
            doc.dirty = False
        return doc

    def open(self, doc_id: str) -> CodeDocument:
        if doc_id not in self.documents:
            raise KeyError(doc_id)
        if doc_id not in self.open_document_ids:
            self.open_document_ids.append(doc_id)
        self.active_document_id = doc_id
        return self.documents[doc_id]

    def event_payload(self, doc: CodeDocument) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        with self._lock:
            self._acks[doc.id] = (request_id, threading.Event())
        print(f"[CODE-TRACE] document added to store {doc.id}\n[CODE-TRACE] activeDocumentId {self.active_document_id}\n[CODE-TRACE] event emitted code.file.opened", flush=True)
        return {"document_id": doc.id, "filename": doc.filename,
                "request_id": request_id,
                "path": doc.absolute_path, "language": doc.language,
                "source_type": doc.source_type, "source": doc.source_type,
                "connector_id": doc.connector_id, "content": doc.content}

    def acknowledge(self, payload):
        with self._lock:
            doc = self.documents.get(payload.get("document_id"))
            pending = self._acks.get(payload.get("document_id"))
            if not doc or not pending or pending[0] != payload.get("request_id"):
                return False
            if (self.active_document_id != doc.id or not payload.get("model_matches")
                    or payload.get("language") != doc.language):
                return False
            pending[1].set()
            return True

    def opened_response(self, doc, filename=None):
        pending = self._acks.get(doc.id)
        if pending and pending[1].wait(28):
            return f"{filename or doc.filename} est ouvert dans l’environnement Coding."
        return f"{filename or doc.filename} a été lu, mais son affichage dans Coding n’est pas confirmé."

    def snapshot(self):
        with self._lock:
            docs = []
            for doc_id in self.open_document_ids:
                doc = self.documents.get(doc_id)
                if not doc:
                    continue
                payload = asdict(doc)
                payload.update(document_id=doc.id, path=doc.absolute_path,
                               request_id=self._acks.get(doc.id, ('',))[0])
                docs.append(payload)
            return {'documents': docs, 'activeDocumentId': self.active_document_id,
                    'openDocumentIds': list(self.open_document_ids), 'build_id': self.BUILD_ID}

    def close(self, doc_id):
        with self._lock:
            if doc_id in self.open_document_ids:
                self.open_document_ids.remove(doc_id)
            self.documents.pop(doc_id, None)
            self._acks.pop(doc_id, None)
            if self.active_document_id == doc_id:
                self.active_document_id = self.open_document_ids[-1] if self.open_document_ids else None
