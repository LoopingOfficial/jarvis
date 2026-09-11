"""Conversations persistantes — le contexte de session ne se perd jamais."""
from __future__ import annotations

import threading
import time
from typing import Any

from .db import Database, dumps, loads, new_id


class ConversationManager:
    def __init__(self, db: Database, events, settings) -> None:
        self._db = db
        self._events = events
        self._settings = settings
        self._current = ""
        self._lock = threading.RLock()

    # -- session courante ---------------------------------------------------
    def current_id(self) -> str:
        with self._lock:
            if self._current and self._db.one("SELECT 1 FROM conversations WHERE id=?", (self._current,)):
                return self._current
            row = self._db.one(
                "SELECT id, updated_at FROM conversations WHERE archived=0 ORDER BY updated_at DESC LIMIT 1")
            idle_hours = float(self._settings.get("voice", "session_idle_reset_hours", 8))
            if row and (time.time() - (row["updated_at"] or 0)) < idle_hours * 3600:
                self._current = row["id"]
            else:
                self._current = self.create()["id"]
            return self._current

    def set_current(self, conversation_id: str) -> bool:
        if not self._db.one("SELECT 1 FROM conversations WHERE id=?", (conversation_id,)):
            return False
        with self._lock:
            self._current = conversation_id
        return True

    # -- CRUD ---------------------------------------------------------------
    def create(self, title: str = "", context: dict[str, Any] | None = None) -> dict[str, Any]:
        cid = new_id("conv")
        now = time.time()
        self._db.execute(
            "INSERT INTO conversations(id, title, context, archived, created_at, updated_at) VALUES(?,?,?,0,?,?)",
            (cid, title or "Nouvelle conversation", dumps(context or {}), now, now))
        with self._lock:
            self._current = cid
        conv = self.get(cid)
        self._events.emit("conversation.created", conv)
        return conv  # type: ignore[return-value]

    def get(self, conversation_id: str) -> dict[str, Any] | None:
        row = self._db.one("SELECT * FROM conversations WHERE id=?", (conversation_id,))
        if not row:
            return None
        d = {k: row[k] for k in row.keys()}
        d["context"] = loads(d["context"], {})
        d["message_count"] = int(self._db.scalar(
            "SELECT COUNT(*) FROM messages WHERE conversation_id=?", (conversation_id,)) or 0)
        d["task_count"] = int(self._db.scalar(
            "SELECT COUNT(*) FROM tasks WHERE conversation_id=?", (conversation_id,)) or 0)
        d["memory_count"] = int(self._db.scalar(
            "SELECT COUNT(*) FROM memories WHERE conversation_id=?", (conversation_id,)) or 0)
        return d

    def list(self, limit: int = 50, search: str = "") -> list[dict[str, Any]]:
        if search:
            rows = self._db.query(
                "SELECT DISTINCT c.* FROM conversations c LEFT JOIN messages m ON m.conversation_id=c.id "
                "WHERE c.title LIKE ? OR m.content LIKE ? ORDER BY c.updated_at DESC LIMIT ?",
                (f"%{search}%", f"%{search}%", limit))
        else:
            rows = self._db.query("SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            conv = self.get(r["id"])
            if conv:
                last = self._db.one(
                    "SELECT content FROM messages WHERE conversation_id=? ORDER BY created_at DESC LIMIT 1",
                    (r["id"],))
                conv["preview"] = (last["content"][:140] if last else "")
                out.append(conv)
        return out

    def rename(self, conversation_id: str, title: str) -> bool:
        ok = bool(self._db.execute(
            "UPDATE conversations SET title=?, updated_at=? WHERE id=?",
            (title.strip()[:200] or "Sans titre", time.time(), conversation_id)).rowcount)
        if ok:
            self._events.emit("conversation.updated", {"id": conversation_id, "title": title})
        return ok

    def delete(self, conversation_id: str) -> bool:
        self._db.execute("DELETE FROM messages WHERE conversation_id=?", (conversation_id,))
        ok = bool(self._db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,)).rowcount)
        if ok:
            with self._lock:
                if self._current == conversation_id:
                    self._current = ""
            self._events.emit("conversation.deleted", {"id": conversation_id})
        return ok

    # -- messages -----------------------------------------------------------
    def add_message(self, conversation_id: str, role: str, content: str,
                    meta: dict[str, Any] | None = None) -> dict[str, Any]:
        mid = new_id("msg")
        now = time.time()
        self._db.execute(
            "INSERT INTO messages(id, conversation_id, role, content, meta, created_at) VALUES(?,?,?,?,?,?)",
            (mid, conversation_id, role, content, dumps(meta or {}), now))
        self._db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conversation_id))
        message = {"id": mid, "conversation_id": conversation_id, "role": role,
                   "content": content, "meta": meta or {}, "created_at": now}
        self._events.emit("conversation.message", message)
        return message

    def messages(self, conversation_id: str, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        rows = self._db.query(
            "SELECT * FROM (SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at DESC "
            "LIMIT ? OFFSET ?) ORDER BY created_at", (conversation_id, limit, offset))
        out = []
        for r in rows:
            d = {k: r[k] for k in r.keys()}
            d["meta"] = loads(d["meta"], {})
            out.append(d)
        return out

    def maybe_title(self, conversation_id: str, first_message: str) -> None:
        conv = self.get(conversation_id)
        if not conv or conv["title"] != "Nouvelle conversation":
            return
        title = " ".join(first_message.split())[:60]
        if title:
            self.rename(conversation_id, title + ("…" if len(first_message) > 60 else ""))

    def stats(self) -> dict[str, Any]:
        return {
            "conversations": int(self._db.scalar("SELECT COUNT(*) FROM conversations") or 0),
            "messages": int(self._db.scalar("SELECT COUNT(*) FROM messages") or 0),
        }
