"""Brain Manager — l'Obsidian Brain de JARVIS, alimenté par les vraies données.

Le graphe n'est jamais décoratif : chaque nœud correspond à une vraie donnée
(mémoire, fiche de connaissance, connecteur, outil, tâche, projet, automatisation).
Les événements `brain.*` sont émis en temps réel pour animer l'UI 3D :
  brain.search, brain.node.selected, brain.path, brain.learn.created
"""
from __future__ import annotations

import time
from typing import Any

from .db import dumps, loads, new_id

# Familles du Brain : on ne crée que des familles qui ont du sens.
FAMILIES = {
    "JARVIS": "Le centre : l'entité elle-même.",
    "MEMORY": "Souvenirs persistants (portées user/project/…).",
    "KNOWLEDGE": "Fiches de connaissances et procédures validées.",
    "PROJECTS": "Projets connus de JARVIS.",
    "PEOPLE": "Personnes connues.",
    "SERVERS": "Serveurs, hôtes et machines.",
    "TOOLS": "Outils et connecteurs réellement utilisables.",
    "WORKFLOWS": "Automatisations et workflows n8n.",
    "APPLICATIONS": "Applications locales.",
    "AUTOMATIONS": "Règles de déclenchement automatique.",
    "DECISIONS": "Contexte durable décidé avec l'utilisateur.",
    "ERRORS": "Échecs enregistrés (pour apprentissage).",
    "SOLUTIONS": "Correctifs appliqués et validés.",
    "DOCUMENTS": "Fichiers / documents référencés.",
}

_NODE_ORDER = ["JARVIS", "MEMORY", "KNOWLEDGE", "PROJECTS", "SERVERS", "PEOPLE",
               "TOOLS", "WORKFLOWS", "APPLICATIONS", "AUTOMATIONS",
               "DECISIONS", "SOLUTIONS", "ERRORS", "DOCUMENTS"]


class BrainManager:
    def __init__(self, core) -> None:
        self._core = core

    # ------------------------------------------------------- construction
    def graph(self, limit: int = 300) -> dict[str, Any]:
        """Construit le graphe complet depuis de vraies données."""
        core = self._core
        now = time.time()
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add_node(nid: str, family: str, label: str, *, kind: str = "node",
                     weight: float = 1.0, meta: dict[str, Any] | None = None) -> None:
            if nid in seen:
                return
            seen.add(nid)
            nodes.append({"id": nid, "family": family, "label": label, "kind": kind,
                          "weight": weight, "meta": meta or {}})

        def add_edge(source: str, target: str, kind: str = "relation") -> None:
            if source == target:
                return
            edges.append({"source": source, "target": target, "kind": kind})

        # Centre : JARVIS.
        add_node("jarvis", "JARVIS", core.settings.get("general", "assistant_name", "JARVIS"),
                 kind="core", weight=2.0)

        # Mémoire (familles réelles).
        for m in core.memory.list(limit=40):
            scope = (m["scope"] or "user").upper()
            family = "MEMORY"
            meta = {"scope": m["scope"], "importance": m["importance"],
                    "content": m["content"][:200], "id": m["id"]}
            add_node(f"mem:{m['id']}", family, m["content"][:60], kind="memory",
                     weight=max(0.6, m["importance"] / 5), meta=meta)
            add_edge("jarvis", f"mem:{m['id']}", "recall")

        # Knowledge.
        for k in core.memory.knowledge_list(limit=60):
            if k.get("status") == "deprecated":
                continue
            family = "KNOWLEDGE"
            if k["kind"] == "procedure":
                family = "SOLUTIONS"
            elif k["kind"] in {"ERROR_FIX", "USER_ENVIRONMENT_SOLUTION"}:
                family = "ERRORS"
            meta = {"id": k["id"], "kind": k["kind"], "project": k.get("project", ""),
                    "confidence": float(k.get("confidence_score") or 0.5),
                    "validations": int(k.get("validation_count") or 0),
                    "summary": k["content"][:240], "tags": k.get("tags", []),
                    "tools": k.get("tools", [])}
            weight = 0.5 + float(k.get("confidence_score") or 0) * 0.5
            add_node(f"kb:{k['id']}", family, k["title"][:60], kind="knowledge",
                     weight=weight, meta=meta)
            add_edge("jarvis", f"kb:{k['id']}", "learned")
            if k.get("project"):
                add_edge(f"kb:{k['id']}", f"proj:{k['project'].casefold()}", "applies_to")
            for tid in (k.get("tools") or []):
                tnid = f"tool:{tid}"
                if tnid not in seen:
                    t = core.registry.get(tid)
                    add_node(tnid, "TOOLS", t.name if t else tid, kind="tool",
                             meta={"tool_id": tid})
                add_edge(tnid, f"kb:{k['id']}", "teaches")

        # Projets.
        for proj in core.memory.stats().get("projects", [])[:20]:
            add_node(f"proj:{proj.casefold()}", "PROJECTS", proj[:60], kind="project",
                     weight=1.3, meta={"project": proj})
            add_edge("jarvis", f"proj:{proj.casefold()}", "manages")

        # Personnes (depuis mémoire user).
        for m in core.memory.list(scope="user", limit=100):
            low = m["content"].casefold()
            if "prénom" in low or "pr[ée]nom" in low or "s'appelle" in low:
                add_node(f"person:{m['id']}", "PEOPLE", m["content"][:40], kind="person",
                         meta={"content": m["content"][:160]})
                add_edge("jarvis", f"person:{m['id']}", "knows")

        # Connecteurs → TOOLS / SERVERS.
        connectors = core.connectors.list(include_disabled=False)
        for c in connectors[:30]:
            nid = f"conn:{c['id']}"
            family = "TOOLS"
            if c["type"] in {"ssh", "sftp", "ftp", "docker"}:
                family = "SERVERS"
            meta = {"connector_id": c["id"], "type": c["type"], "status": c["status"],
                    "name": c["name"]}
            add_node(nid, family, f"{c['name']} ({c['type']})", kind="tool",
                     weight=1.2 if c["status"] == "connected" else 0.8, meta=meta)
            add_edge("jarvis", nid, "uses")

        # Outils du registre.
        for t in core.registry.all()[:30]:
            if not t.enabled:
                continue
            nid = f"tool:{t.id}"
            add_node(nid, "TOOLS", t.name, kind="tool", weight=1.0,
                     meta={"tool_id": t.id, "category": t.category, "risk": t.risk})
            add_edge("jarvis", nid, "can_use")

        # Automatisations → WORKFLOWS.
        for wf in core.automations.list()[:20]:
            nid = f"wf:{wf['id']}"
            add_node(nid, "WORKFLOWS", wf["name"][:60], kind="workflow",
                     weight=1.0 if wf.get("enabled", True) else 0.6,
                     meta={"workflow_id": wf["id"], "trigger": str(wf.get("trigger", ""))[:80]})
            add_edge("jarvis", nid, "runs")

        # Nœuds persistés supplémentaires (brain_nodes).
        for row in core.db.query("SELECT * FROM brain_nodes ORDER BY updated_at DESC LIMIT 150"):
            meta = loads(row["meta"], {})
            add_node(row["id"], row["family"], row["label"][:60], kind=row["kind"],
                     weight=float(row["weight"] or 1.0), meta=meta)
            if row["family"] == "KNOWLEDGE" or row["kind"] == "procedure":
                add_edge("jarvis", row["id"], "learned")
        for edge in core.db.query("SELECT * FROM brain_edges ORDER BY created_at DESC LIMIT 200"):
            if edge["source"] in seen and edge["target"] in seen:
                add_edge(edge["source"], edge["target"], edge["kind"])

        return {
            "nodes": nodes, "edges": edges,
            "families": [f for f in _NODE_ORDER if f != "JARVIS"],
            "stats": {"nodes": len(nodes), "edges": len(edges)},
        }

    # ------------------------------------------------------- émissions 3D
    def emit_search(self, kind: str, query: str, ids: list[str], labels: list[str]) -> None:
        """Le Brain montre les zones réellement consultées."""
        self._core.events.emit("brain.search", {
            "kind": kind, "query": query[:200],
            "node_ids": ids[:8], "labels": labels[:8],
        })

    def emit_recall(self, ids: list[str], labels: list[str]) -> None:
        self.emit_search("memory", "", ids, labels)

    def node_selected(self, nid: str, label: str) -> None:
        self._core.events.emit("brain.node.selected", {"id": nid, "label": label[:200]})

    def path(self, steps: list[dict[str, str | list[str]]]) -> None:
        """Chemin d'impulsions : ex. [{'family':'KNOWLEDGE','labels':['SSH']}…]"""
        self._core.events.emit("brain.path", {"steps": steps})

    def tool_activity(self, tool_id: str, family: str = "TOOLS") -> None:
        self._core.events.emit("brain.tool.active", {"tool_id": tool_id, "family": family})

    def activity(self, *, title: str, kind: str = "action", state: str = "",
                 detail: str = "", meta: dict[str, Any] | None = None) -> None:
        """Trace d'activité réelle, diffusée en temps réel."""
        core = self._core
        self._core.events.emit("activity.trace", {
            "ts": time.time(), "kind": kind, "title": title,
            "detail": detail, "state": state,
        })
        try:
            core.db.execute(
                "INSERT INTO activity_trace(ts, kind, title, detail, state, meta) VALUES(?,?,?,?,?,?)",
                (time.time(), kind[:40], title[:200], detail[:600], state, dumps(meta or {})),
            )
        except Exception:
            pass

    # ------------------------------------------------------- ajout nœud
    def add_node(self, *, kind: str, family: str, label: str,
                 ref_type: str = "", ref_id: str = "", meta: dict[str, Any] | None = None) -> dict[str, Any]:
        nid = new_id("br")
        now = time.time()
        self._core.db.execute(
            "INSERT INTO brain_nodes(id, kind, family, label, ref_type, ref_id, meta, weight, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (nid, kind, family, label[:200], ref_type, ref_id, dumps(meta or {}), 1.0, now, now),
        )
        self._core.events.emit("brain.learn.created", {
            "id": nid, "kind": kind, "family": family, "label": label[:200],
        })
        return {"id": nid, "kind": kind, "family": family, "label": label}

    def add_edge(self, source: str, target: str, kind: str = "relation") -> dict[str, Any]:
        eid = new_id("be")
        self._core.db.execute(
            "INSERT INTO brain_edges(id, source, target, kind, weight, created_at) VALUES(?,?,?,?,?,?)",
            (eid, source, target, kind, 1.0, time.time()),
        )
        return {"id": eid, "source": source, "target": target, "kind": kind}

    # ------------------------------------------------------- recherche UI
    def search(self, query: str) -> dict[str, Any]:
        core = self._core
        graph = self.graph(limit=250)
        q = (query or "").strip().casefold()
        if not q:
            return graph
        scored: list[tuple[float, dict[str, Any]]] = []
        for n in graph["nodes"]:
            hay = f"{n['label']} {n['family']} {n['kind']} {' '.join(n['meta'].get('tags', []))}".casefold()
            score = 0.0
            for word in q.split():
                if word in hay:
                    score += 1.0
            if n["family"] == "KNOWLEDGE" and score:
                score += 0.3
            if score > 0:
                scored.append((score, n))
        # Les nœuds hors résultat restent affichés (intensité réduite côté UI).
        ids = [n["id"] for _s, n in sorted(scored, key=lambda x: -x[0])[:30]]
        return {**graph, "match_ids": ids, "query": query}

    def stats(self) -> dict[str, Any]:
        from .db import loads
        g = self.graph(limit=400)
        by_family: dict[str, int] = {}
        for n in g["nodes"]:
            by_family[n["family"]] = by_family.get(n["family"], 0) + 1
        return {
            "nodes": len(g["nodes"]), "edges": len(g["edges"]),
            "families": by_family,
            "knowledge": int(self._core.db.scalar("SELECT COUNT(*) FROM knowledge") or 0),
            "knowledge_active": int(self._core.db.scalar(
                "SELECT COUNT(*) FROM knowledge WHERE status='active'") or 0),
            "brains": int(self._core.db.scalar("SELECT COUNT(*) FROM brain_nodes") or 0),
            "tools": self._core.registry.count(),
            "connectors": len(self._core.connectors.list(include_disabled=False)),
        }