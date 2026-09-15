"""Memory Manager — mémoires séparées par portée + base de connaissances.

Portées (jamais mélangées, et jamais confondues avec le Secret Vault) :
  user          — préférences, habitudes, informations durables sur Jérôme
  project       — contexte d'un projet précis
  conversation  — contexte propre à une conversation
  task          — trace de ce qu'une tâche a produit
  knowledge     — table dédiée (fiches, procédures, documentation)

Recherche : FTS5 (lexicale) + similarité vectorielle si un modèle d'embedding
est configuré, sinon repli sur un score lexical.
"""
from __future__ import annotations

import array
import math
import re
import time
from typing import Any

from .db import Database, dumps, loads, new_id

SCOPES = ("user", "project", "conversation", "task")

# Motifs d'extraction automatique — uniquement des faits énoncés par l'utilisateur.
EXTRACT_PATTERNS = [
    (r"\b(?:je m'appelle|mon (?:pr[ée]nom|nom) est)\s+([A-ZÉÈÀ][\w\-']{1,30})", "Prénom : {}"),
    (r"\b(?:je préfère|je veux toujours|préfère que tu)\s+(.{5,140})", "Préférence : {}"),
    (r"\b(?:mon serveur|le serveur)\s+([\w.\-]{2,40})\s+(?:est|sert|héberge)\s+(.{3,120})", "Serveur {} : {}"),
    (r"\b(?:retiens|souviens[- ]toi|note)\s+(?:que\s+)?(.{5,200})", "{}"),
    (r"\b(?:j'utilise|j'ai)\s+((?:un|une|le|la)\s+.{4,120})", "Utilise {}"),
    (r"\b(?:mon projet|le projet)\s+([\w.\- ]{2,40})\s+(?:est|utilise|tourne sur)\s+(.{3,140})", "Projet {} : {}"),
]

STOPWORDS = {
    "le", "la", "les", "de", "des", "du", "un", "une", "et", "ou", "que", "qui", "quoi", "dans",
    "sur", "pour", "avec", "est", "sont", "ce", "cette", "mon", "ma", "mes", "je", "tu", "il",
    "the", "a", "an", "of", "to", "in", "is", "are", "and", "or", "for", "with",
}


def _tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[\wàâäéèêëîïôöùûüç'-]{2,}", (text or "").casefold()) if w not in STOPWORDS]


def _pack(vector: list[float]) -> bytes:
    return array.array("f", vector).tobytes()


def _unpack(blob: bytes | None) -> list[float]:
    if not blob:
        return []
    arr = array.array("f")
    arr.frombytes(blob)
    return list(arr)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _fts_escape(query: str) -> str:
    words = _tokens(query)
    if not words:
        return ""
    return " OR ".join(f'"{w}"' for w in words[:12])


class MemoryManager:
    def __init__(self, db: Database, events, settings, core=None) -> None:
        self._db = db
        self._events = events
        self._settings = settings
        self._core = core

    def bind_core(self, core) -> None:
        self._core = core

    # -- écriture -----------------------------------------------------------
    def add(
        self, *, content: str, scope: str = "user", importance: int = 2, tags: list[str] | None = None,
        source: str = "", project: str = "", conversation_id: str = "", task_id: str = "",
        pinned: bool = False, related: list[str] | None = None, dedupe: bool = True,
    ) -> dict[str, Any]:
        content = (content or "").strip()
        if not content:
            raise ValueError("Contenu vide.")
        if scope not in SCOPES:
            scope = "user"
        if dedupe:
            existing = self._db.one(
                "SELECT * FROM memories WHERE scope=? AND lower(content)=lower(?)", (scope, content)
            )
            if existing:
                self._db.execute(
                    "UPDATE memories SET importance=MAX(importance, ?), updated_at=? WHERE id=?",
                    (int(importance), time.time(), existing["id"]),
                )
                return self.get(existing["id"])  # type: ignore[return-value]
        mid = new_id("mem")
        now = time.time()
        embedding = self._embed(content)
        self._db.execute(
            "INSERT INTO memories(id, scope, content, importance, pinned, source, tags, related, project, "
            "conversation_id, task_id, created_at, updated_at, embedding) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid, scope, content, max(1, min(5, int(importance))), 1 if pinned else 0, source,
             dumps(tags or []), dumps(related or []), project, conversation_id, task_id, now, now,
             _pack(embedding) if embedding else None),
        )
        item = self.get(mid)
        self._events.emit("memory.created", {"id": mid, "scope": scope, "content": content[:160]})
        return item  # type: ignore[return-value]

    def update(self, memory_id: str, **fields) -> dict[str, Any] | None:
        allowed = {"content", "importance", "pinned", "tags", "scope", "project", "related"}
        sets, params = [], []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key in {"tags", "related"}:
                value = dumps(value or [])
            if key == "pinned":
                value = 1 if value else 0
            if key == "importance":
                value = max(1, min(5, int(value)))
            sets.append(f"{key}=?")
            params.append(value)
        if not sets:
            return self.get(memory_id)
        sets.append("updated_at=?")
        params.extend([time.time(), memory_id])
        self._db.execute(f"UPDATE memories SET {', '.join(sets)} WHERE id=?", params)
        if "content" in fields:
            emb = self._embed(str(fields["content"]))
            if emb:
                self._db.execute("UPDATE memories SET embedding=? WHERE id=?", (_pack(emb), memory_id))
        self._events.emit("memory.updated", {"id": memory_id})
        return self.get(memory_id)

    def delete(self, memory_id: str) -> bool:
        cur = self._db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        if cur.rowcount:
            self._events.emit("memory.deleted", {"id": memory_id})
            return True
        return False

    # -- lecture ------------------------------------------------------------
    def get(self, memory_id: str) -> dict[str, Any] | None:
        row = self._db.one("SELECT * FROM memories WHERE id=?", (memory_id,))
        return self._row(row) if row else None

    def list(self, scope: str = "", limit: int = 100, offset: int = 0, project: str = "") -> list[dict[str, Any]]:
        sql = "SELECT * FROM memories WHERE 1=1"
        params: list[Any] = []
        if scope:
            sql += " AND scope=?"
            params.append(scope)
        if project:
            sql += " AND project=?"
            params.append(project)
        sql += " ORDER BY pinned DESC, importance DESC, updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return [self._row(r) for r in self._db.query(sql, params)]

    def search(self, query: str, limit: int = 10, scope: str = "") -> list[dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return self.list(scope=scope, limit=limit)
        results: dict[str, tuple[float, dict[str, Any]]] = {}

        match = _fts_escape(query)
        if match:
            try:
                rows = self._db.query(
                    "SELECT m.*, bm25(memories_fts) AS score FROM memories_fts "
                    "JOIN memories m ON m.rowid = memories_fts.rowid "
                    "WHERE memories_fts MATCH ? ORDER BY score LIMIT ?",
                    (match, limit * 3),
                )
                for r in rows:
                    item = self._row(r)
                    if scope and item["scope"] != scope:
                        continue
                    score = 1.0 / (1.0 + abs(float(r["score"] or 0)))
                    results[item["id"]] = (score, item)
            except Exception:
                pass

        if not results:  # repli lexical simple
            words = set(_tokens(query))
            for item in self.list(scope=scope, limit=400):
                overlap = words & set(_tokens(item["content"]))
                if overlap:
                    results[item["id"]] = (len(overlap) / max(1, len(words)), item)

        if self._settings.get("memory", "semantic_search", True):
            qvec = self._embed(query)
            if qvec:
                for r in self._db.query(
                    "SELECT * FROM memories WHERE embedding IS NOT NULL" + (" AND scope=?" if scope else ""),
                    (scope,) if scope else (),
                ):
                    sim = _cosine(qvec, _unpack(r["embedding"]))
                    if sim > 0.55:
                        item = self._row(r)
                        prev = results.get(item["id"], (0.0, item))
                        results[item["id"]] = (max(prev[0], sim), item)

        ranked = sorted(results.values(), key=lambda x: (x[1]["pinned"], x[0], x[1]["importance"]), reverse=True)
        return [item for _score, item in ranked[:limit]]

    def context_for(self, text: str, conversation_id: str = "", project: str = "") -> list[dict[str, Any]]:
        """Souvenirs pertinents à injecter dans le prompt système."""
        limit = int(self._settings.get("memory", "max_context_memories", 12))
        min_importance = int(self._settings.get("memory", "min_importance_for_context", 2))
        picked: dict[str, dict[str, Any]] = {}
        for m in self._db.query(
            "SELECT * FROM memories WHERE pinned=1 OR importance>=4 ORDER BY importance DESC LIMIT ?", (limit,)
        ):
            item = self._row(m)
            picked[item["id"]] = item
        for m in self.search(text, limit=limit):
            if m["importance"] >= min_importance:
                picked.setdefault(m["id"], m)
        if conversation_id:
            for m in self.list(limit=6):
                if m["conversation_id"] == conversation_id:
                    picked.setdefault(m["id"], m)
        if project:
            for m in self.list(project=project, limit=6):
                picked.setdefault(m["id"], m)
        return list(picked.values())[:limit]

    def stats(self) -> dict[str, Any]:
        total = int(self._db.scalar("SELECT COUNT(*) FROM memories") or 0)
        by_scope = {r["scope"]: r["n"] for r in self._db.query(
            "SELECT scope, COUNT(*) AS n FROM memories GROUP BY scope")}
        return {
            "total": total,
            "user": by_scope.get("user", 0),
            "project": by_scope.get("project", 0),
            "conversation": by_scope.get("conversation", 0),
            "task": by_scope.get("task", 0),
            "knowledge": int(self._db.scalar("SELECT COUNT(*) FROM knowledge") or 0),
            "conversations": int(self._db.scalar("SELECT COUNT(*) FROM conversations") or 0),
            "pinned": int(self._db.scalar("SELECT COUNT(*) FROM memories WHERE pinned=1") or 0),
            "projects": [r["project"] for r in self._db.query(
                "SELECT DISTINCT project FROM memories WHERE project<>'' ORDER BY project") if r["project"]],
            "embedded": int(self._db.scalar("SELECT COUNT(*) FROM memories WHERE embedding IS NOT NULL") or 0),
            "series": self.growth_series(),
        }

    def growth_series(self, days: int = 14) -> list[dict[str, Any]]:
        """Cumul réel des souvenirs jour par jour (pour Memory Insights)."""
        now = time.time()
        start = now - days * 86400
        before = int(self._db.scalar("SELECT COUNT(*) FROM memories WHERE created_at < ?", (start,)) or 0)
        rows = self._db.query(
            "SELECT CAST((created_at - ?) / 86400 AS INTEGER) AS bucket, COUNT(*) AS n "
            "FROM memories WHERE created_at >= ? GROUP BY bucket", (start, start))
        per_day = {int(r["bucket"]): int(r["n"]) for r in rows}
        series = []
        running = before
        for day in range(days + 1):
            running += per_day.get(day, 0)
            series.append({"ts": start + day * 86400, "total": running})
        return series

    def graph(self, limit: int = 60) -> dict[str, Any]:
        """Relations entre souvenirs (tags partagés / liens explicites) pour Memory Insights."""
        items = self.list(limit=limit)
        nodes = [{"id": m["id"], "label": m["content"][:60], "scope": m["scope"],
                  "importance": m["importance"], "pinned": m["pinned"]} for m in items]
        edges = []
        by_tag: dict[str, list[str]] = {}
        for m in items:
            for t in m["tags"]:
                by_tag.setdefault(str(t).casefold(), []).append(m["id"])
            for rel in m["related"]:
                edges.append({"source": m["id"], "target": rel, "kind": "related"})
        for tag, ids in by_tag.items():
            for i in range(len(ids) - 1):
                edges.append({"source": ids[i], "target": ids[i + 1], "kind": f"tag:{tag}"})
        return {"nodes": nodes, "edges": edges[:200]}

    # -- extraction automatique ---------------------------------------------
    def auto_extract(self, text: str, conversation_id: str = "") -> list[dict[str, Any]]:
        if not self._settings.get("memory", "auto_extract", True):
            return []
        created = []
        for pattern, template in EXTRACT_PATTERNS:
            for match in re.finditer(pattern, text or "", re.IGNORECASE):
                try:
                    content = template.format(*[g.strip(" .,;") for g in match.groups()])
                except Exception:
                    continue
                content = content.strip()
                if len(content) < 4 or len(content) > 300:
                    continue
                try:
                    created.append(self.add(content=content, scope="user", importance=3,
                                            source="auto:conversation", conversation_id=conversation_id,
                                            tags=["auto"]))
                except Exception:
                    continue
        return created

    # -- base de connaissances ---------------------------------------------
    def knowledge_add(self, *, title: str, content: str, kind: str = "note",
                      tags: list[str] | None = None, source: str = "", project: str = "",
                      tools: list[str] | None = None,
                      verification_method: str = "", evidence: dict | None = None) -> dict[str, Any]:
        from .tool_identity import ensure_identity
        tools = list(dict.fromkeys(ensure_identity().canonical(t) for t in (tools or [])))
        kid = new_id("kb")
        now = time.time()
        self._db.execute(
            "INSERT INTO knowledge(id, title, content, source, kind, tags, project, created_at, updated_at, "
            "tools, verification_method, evidence) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (kid, title.strip()[:200], content.strip(), source, kind, dumps(tags or []), project,
             now, now, dumps(tools or []), verification_method, dumps(evidence or {})),
        )
        self._events.emit("memory.created", {"id": kid, "scope": "knowledge", "content": title[:120]})
        return self.knowledge_get(kid)  # type: ignore[return-value]

    def knowledge_get(self, kid: str) -> dict[str, Any] | None:
        row = self._db.one("SELECT * FROM knowledge WHERE id=?", (kid,))
        if not row:
            return None
        d = {k: row[k] for k in row.keys()}
        d["tags"] = loads(d["tags"], [])
        d["tools"] = loads(d.get("tools"), [])
        d["confidence_score"] = float(d.get("confidence_score") or 0.5)
        return d

    def knowledge_get_by_title(self, title: str) -> dict[str, Any] | None:
        row = self._db.one(
            "SELECT * FROM knowledge WHERE lower(title)=lower(?) AND status<>'deprecated' ORDER BY updated_at DESC LIMIT 1",
            ((title or "").strip(),))
        return self.knowledge_get(row["id"]) if row else None

    def knowledge_list(self, limit: int = 100, project: str = "") -> list[dict[str, Any]]:
        sql = "SELECT * FROM knowledge"
        params: list[Any] = []
        if project:
            sql += " WHERE project=?"
            params.append(project)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        out = []
        for r in self._db.query(sql, params):
            d = {k: r[k] for k in r.keys()}
            d["tags"] = loads(d["tags"], [])
            d["tools"] = loads(d.get("tools"), [])
            d["confidence_score"] = float(d.get("confidence_score") or 0.5)
            out.append(d)
        return out

    def knowledge_search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        match = _fts_escape(query)
        if not match:
            return self.knowledge_list(limit=limit)
        try:
            rows = self._db.query(
                "SELECT k.* FROM knowledge_fts JOIN knowledge k ON k.rowid = knowledge_fts.rowid "
                "WHERE knowledge_fts MATCH ? ORDER BY bm25(knowledge_fts) LIMIT ?", (match, limit))
        except Exception:
            rows = []
        if not rows:
            words = set(_tokens(query))
            return [k for k in self.knowledge_list(limit=200)
                    if words & set(_tokens(k["title"] + " " + k["content"]))][:limit]
        out = []
        for r in rows:
            d = {k: r[k] for k in r.keys()}
            d["tags"] = loads(d["tags"], [])
            d["tools"] = loads(d.get("tools"), [])
            d["confidence_score"] = float(d.get("confidence_score") or 0.5)
            out.append(d)
        return out

    def knowledge_update(self, kid: str, **fields) -> dict[str, Any] | None:
        allowed = {"title", "content", "kind", "tags", "project", "status",
                   "confidence_score", "validation_count", "failure_count",
                    "last_validated_at", "tools", "verification_method", "evidence", "source"}
        if "tools" in fields:
            from .tool_identity import ensure_identity
            fields["tools"] = list(dict.fromkeys(ensure_identity().canonical(t) for t in fields["tools"]))
        sets, params = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            sets.append(f"{k}=?")
            params.append(dumps(v) if k in {"tags", "tools", "evidence"} else v)
        if not sets:
            return self.knowledge_get(kid)
        sets.append("updated_at=?")
        params.extend([time.time(), kid])
        self._db.execute(f"UPDATE knowledge SET {', '.join(sets)} WHERE id=?", params)
        self._events.emit("memory.updated", {"id": kid, "scope": "knowledge"})
        return self.knowledge_get(kid)

    def knowledge_delete(self, kid: str) -> bool:
        return bool(self._db.execute("DELETE FROM knowledge WHERE id=?", (kid,)).rowcount)

    # -- auto-learning : confiance des connaissances ----------------------
    def record_validation(self, kid: str, *, success: bool) -> dict[str, Any] | None:
        """Valorise la confiance : validation_count +1, échec -1 (tracked)."""
        item = self.knowledge_get(kid)
        if not item:
            return None
        now = time.time()
        validations = int(item.get("validation_count") or 0)
        failures = int(item.get("failure_count") or 0)
        confidence = float(item.get("confidence_score") or 0.5)
        if success:
            validations += 1
            confidence = min(0.99, confidence + 0.15)
            fields = {"validation_count": validations, "confidence_score": confidence,
                      "last_validated_at": now}
            self._events.emit("knowledge.validated", {"id": kid, "validations": validations,
                                                      "confidence": round(confidence, 3)})
        else:
            failures += 1
            confidence = max(0.05, confidence - 0.2)
            fields = {"failure_count": failures, "confidence_score": confidence}
            self._events.emit("knowledge.failed", {"id": kid, "failures": failures,
                                                   "confidence": round(confidence, 3)})
        fields["status"] = "deprecated" if failures >= 3 or confidence < 0.15 else "active"
        return self.knowledge_update(kid, **fields)

    # -- embeddings ---------------------------------------------------------
    def _embed(self, text: str) -> list[float]:
        if not self._settings.get("memory", "semantic_search", True) or not self._core:
            return []
        try:
            llm = getattr(self._core, "llm", None)
            if llm is None:
                return []
            return llm.embed(text) or []
        except Exception:
            return []

    def _row(self, row) -> dict[str, Any]:
        d = {k: row[k] for k in row.keys() if k != "embedding"}
        d["tags"] = loads(d.get("tags"), [])
        d["related"] = loads(d.get("related"), [])
        d["pinned"] = bool(d.get("pinned"))
        return d
