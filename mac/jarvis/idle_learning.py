"""Apprentissage autonome borné : uniquement pendant l'inactivité réelle.

Idées directrices corrigées :
- UNE identité par outil : toutes les métriques, connaissances et sessions
  sont rattachées au `tool_id` canonique (ex. ``ssh.read_file``), jamais au
  nom humain. Les noms/alias ne servent qu'à l'affichage.
- L'expertise est un vrai score 0-100 (voir expertise.py), recalculé après
  chaque usage ET chaque session d'apprentissage. Une session « 100 % » sans
  connaissance nouvelle ni validation ne gonfle jamais le score.
- Chaque session produit un résultat structuré LearningSessionResult et
  alimente de vraies fiches Knowledge liées par `tools=[tool_id]` + les vrais
  nœuds du Brain Atlas.
"""
from __future__ import annotations

import html
import hashlib
import posixpath
import re
import threading
import time
import urllib.request
from typing import Any

from .db import dumps, loads, new_id
from .config import DB_PATH as DEFAULT_DB_PATH
from .expertise import compute_expertise, VERIFIED_METHODS, evidence, is_verified
from .remote_paths import resolveRemotePath
from .tool_identity import ensure_identity, identity

OFFICIAL = {
    "blender": "https://docs.blender.org/manual/en/latest/",
    "avatar": "https://docs.blender.org/manual/en/latest/",
    "ollama": "https://docs.ollama.com/",
    "three": "https://threejs.org/docs/",
    "docker": "https://docs.docker.com/",
    "n8n": "https://docs.n8n.io/",
    "paramiko": "https://docs.paramiko.org/",
    "comfyui": "https://docs.comfy.org/",
    "ssh": "https://docs.paramiko.org/en/stable/api/client.html",
    "db": "https://dev.mysql.com/doc/",
    "git": "https://git-scm.com/doc",
    "http": "https://developer.mozilla.org/docs/Web/HTTP",
    "web": "https://developer.mozilla.org/docs/Web",
    "docker": "https://docs.docker.com/",
}

# Sujets d'apprentissage par tool (curriculum) : coverage_total = nombre de
# sujets, coverage_mastered = sujets couverts par une connaissance validée
# associée à un usage réussi.
CURRICULA: dict[str, list[tuple[str, list[str]]]] = {
    "ssh.read_file": [
        ("usage de base", ["lire", "read", "cat", "afficher"]),
        ("résolution du connecteur", ["connector", "connecteur", "resolve"]),
        ("working_directory", ["working_directory", "racine", "relative"]),
        ("chemins relatifs", ["relative", "relatif", "path resolution"]),
        ("permissions", ["permission denied", "permission", "chmod"]),
        ("gros fichiers", ["large", "gros fichier", "taille", "head -c"]),
        ("encodage", ["encoding", "encodage", "utf"]),
        ("timeout", ["timeout", "délai", "timed out"]),
        ("erreurs connues", ["erreur", "error", "not found", "introuvable"]),
        ("sécurité", ["security", "sécurité", "injection", "scrub"]),
    ],
    "ssh.list": [
        ("usage de base", ["ls", "lister", "list"]),
        ("résolution du connecteur", ["connector", "connecteur"]),
        ("working_directory", ["working_directory", "racine"]),
        ("chemins relatifs", ["relative", "relatif"]),
        ("permissions", ["permission denied"]),
        ("dossiers vides", ["vide", "empty"]),
        ("erreurs connues", ["erreur", "error", "not found"]),
    ],
    "connector.list": [
        ("usage de base", ["lister", "list"]),
        ("connecteurs actifs", ["enabled", "configuré", "actif"]),
        ("statuts", ["status", "connected", "error"]),
        ("types", ["type", "ssh", "mysql", "cpanel"]),
        ("permissions", ["permission", "read", "write"]),
    ],
    "ssh.run": [
        ("usage de base", ["commande", "command", "run"]),
        ("résolution du connecteur", ["connector", "connecteur"]),
        ("temps d'exécution", ["timeout", "délai"]),
        ("sortie longue", ["sortie", "output", "truncat"]),
        ("erreurs connues", ["erreur", "command not found"]),
        ("sécurité", ["security", "injection", "shell_quote"]),
    ],
    "memory.search": [
        ("usage de base", ["chercher", "search", "mémoire"]),
        ("portées", ["scope", "user", "project"]),
        ("recherche sémantique", ["semantic", "embedding"]),
        ("FTS lexical", ["fts", "bm25", "lexical"]),
    ],
    # ------------------------------------------------------------------
    # Atelier 3D — le spécialiste `jarvis-blender` apprend la vraie API
    # Blender Python, le rigging, les shape keys, l'animation faciale,
    # l'export GLTF, Three.js, la performance, les cheveux et les vêtements.
    # Les connaissances sont liées au tool Blender/avatar correspondant.
    # ------------------------------------------------------------------
    "blender.create_model": [
        ("Blender Python API (bpy)", ["bpy", "ops", "context", "data"]),
        ("géométrie procédurale", ["mesh", "vertices", "faces", "primitive"]),
        ("matériaux et shaders", ["material", "shader", "principled", "node"]),
        ("export GLTF", ["gltf", "glb", "export"]),
    ],
    "blender.modify_model": [
        ("modification de maillage", ["mesh", "vertices", "scale", "transform"]),
        ("modifiers", ["modifier", "subdivision", "boolean"]),
        ("inspection avant modification", ["inspect", "objects", "inventory"]),
        ("export GLTF", ["gltf", "glb", "export"]),
    ],
    "blender.rig": [
        ("armature", ["armature", "bone", "skeleton"]),
        ("poids de skin", ["weight", "skin", "vertex group"]),
        ("rigging", ["rig", "armature", "constraint"]),
        ("export GLTF", ["gltf", "glb", "skin"]),
    ],
    "blender.animate": [
        ("shape keys", ["shape key", "shape_key", "key"]),
        ("animation faciale", ["facial", "shape key", "expression"]),
        ("courbes d'animation", ["animation", "fcurve", "keyframe"]),
        ("export GLTF", ["gltf", "glb", "animation"]),
    ],
    "blender.material": [
        ("matériaux", ["material", "shader", "principled"]),
        ("textures", ["texture", "image", "uv"]),
        ("export GLTF", ["gltf", "glb", "material"]),
    ],
    "blender.export": [
        ("export GLTF", ["gltf", "glb", "export"]),
        ("Three.js", ["three", "gltf", "web"]),
        ("formats de fichier", ["fbx", "obj", "stl", "gltf"]),
    ],
    "blender.optimize": [
        ("performance", ["performance", "polygon", "optimiz"]),
        ("décimation", ["decimate", "low-poly", "polygon"]),
        ("export GLTF", ["gltf", "glb"]),
    ],
    "blender.inspect": [
        ("inspection de scène", ["objects", "inventory", "materials", "scene"]),
        ("Blender Python API (bpy)", ["bpy", "context", "data"]),
    ],
    "blender.render": [
        ("rendu", ["render", "eevee", "cycles"]),
        ("éclairage", ["light", "lamp", "world"]),
        ("caméra", ["camera", "view"]),
    ],
    "blender.run_script": [
        ("Blender Python API (bpy)", ["bpy", "ops", "context", "data"]),
        ("erreurs bpy", ["error", "context", "incorrect"]),
        ("sandbox", ["sandbox", "import", "forbidden"]),
    ],
    "avatar.update_from_reference": [
        ("cheveux", ["hair", "particle", "curve"]),
        ("vêtements", ["cloth", "clothing", "garment"]),
        ("shape keys", ["shape key", "shape_key"]),
        ("matériaux", ["material", "shader"]),
        ("rigging", ["rig", "armature"]),
        ("export GLTF", ["gltf", "glb"]),
    ],
}

DEFAULT_CURRICULUM: list[tuple[str, list[str]]] = [
    ("usage de base", ["usage", "base", "basic"]),
    ("arguments", ["argument", "param", "schema"]),
    ("résolution du connecteur", ["connector", "connecteur"]),
    ("erreurs connues", ["erreur", "error", "failed"]),
    ("sécurité", ["security", "sécurité", "scrub"]),
]

# Classes d'erreurs reconnues (pour error_categories et topics auto).
_ERROR_CLASSES: list[tuple[str, list[str]]] = [
    ("permission denied", ["permission denied", "permission denied (publickey)",
                           "authentication failed", "authentification"]),
    ("file not found", ["no such file", "not found", "cannot access", "introuvable"]),
    ("timeout", ["timed out", "timeout", "connection timed", "délai dépassé"]),
    ("working_directory", ["working_directory", "racine inconnue", "invalid remote root",
                           "missing remote working directory"]),
    ("connector unavailable", ["aucun connecteur", "not configured", "connecteur non configuré"]),
    ("command not found", ["command not found", "commande introuvable"]),
    ("bpy context error", ["context is incorrect", "context is none", "invalid context",
                            "operator poll", "poll failed"]),
    ("bpy reference error", ["invalid reference", "is not a valid", "keyerror",
                              "not found in"]),
]

REPEATED_FAILURE_TOPIC_AFTER = 3  # seuil d'erreurs identiques -> topic auto

# Sujets pour lesquels JARVIS dispose d'un vrai auto-test : la maîtrise y exige
# un test réellement passé, jamais la seule lecture de documentation.
TESTABLE_TOPICS = {"working_directory", "chemins relatifs", "sécurité"}
# Preuve d'usage réel exigée pour maîtriser un sujet non testable.
MIN_SUCCESS_FOR_MASTERY = 3
MIN_SUCCESS_RATE_FOR_MASTERY = 0.9

# Types de connaissance supportés (contrat 19).
KNOWLEDGE_TYPES = (
    "API_REFERENCE", "HOW_TO", "ERROR_FIX", "BEST_PRACTICE", "VERSION_CHANGE",
    "DEPRECATION", "WORKFLOW", "USER_ENVIRONMENT_SOLUTION", "documentation",
)


class IdleLearningEngine:
    STATES = ("ACTIVE", "IDLE", "LEARNING", "PAUSED", "SLEEPING")

    def __init__(self, core) -> None:
        self.core = core
        self.state = "ACTIVE"
        self.last_activity_at = time.time()
        self.current: dict[str, Any] = {}
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._active_tools: dict[str, float] = {}
        self._completed_sig: dict[tuple[str, str], float] = {}
        self._session_lock = threading.Lock()
        self._manual_paused = False
        ensure_identity()
        core.events.on("*", self._event)
        try:
            self._migrate_data()
        except Exception as exc:
            self._log("LEARNING_MIGRATION_FAILED", str(exc), "error")
            raise

    # ------------------------------------------------------------------ utils
    def _canonical(self, raw: str) -> str:
        return ensure_identity().canonical(str(raw or "unknown").strip())

    def _log(self, kind: str, detail: str, level: str = "info") -> None:
        """Trace interne + feed temps réel (contrat logs 40)."""
        try:
            self.core.events.feed(detail, kind=kind, level=level, source="learning",
                                  meta={"category": kind})
        except Exception:
            pass

    # ------------------------------------------------------------ événements
    def _event(self, event: dict[str, Any]) -> None:
        typ, data = event.get("type", ""), event.get("data", {})
        if typ.startswith("learning."):
            return
        if typ in {"conversation.message", "voice.transcript", "task.created", "tool.started"}:
            if typ == "conversation.message" and data.get("role") not in {None, "user"}:
                return
            self.touch()
        if typ == "tool.started":
            tool = str(data.get("tool_id") or data.get("tool") or data.get("name") or "unknown")
            self._active_tools[self._canonical(tool)] = time.time()
        elif typ in {"tool.completed", "tool.failed"}:
            if data.get("metrics_recorded"):
                return
            tool = str(data.get("tool_id") or data.get("tool") or data.get("name") or "unknown")
            tool = self._canonical(tool)
            started = self._active_tools.pop(tool, None)
            if started is None:
                duration = float(data.get("duration_ms") or 0)
            else:
                duration = (time.time() - started) * 1000
            preview = str(data.get("preview") or "")[:60]
            sig = (tool, str(data.get("execution_id") or preview))
            now = time.time()
            if self._completed_sig.get(sig) and now - self._completed_sig[sig] < 2.5:
                # Déjà compté pour cet appel (second événement du même appel).
                return
            self._completed_sig[sig] = now
            if len(self._completed_sig) > 300:
                cutoff = now - 5
                self._completed_sig = {k: v for k, v in self._completed_sig.items() if v >= cutoff}
            self.record_tool(tool, typ == "tool.completed" and data.get("ok", True), duration,
                             str(data.get("error") or ""))

    def touch(self) -> None:
        self.last_activity_at = time.time()
        if self.state == "LEARNING":
            self.state = "PAUSED"
            self.core.events.emit("learning.paused_by_user", {"reason": "user_activity"})
        self._wake.set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="jarvis-idle-learning")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set(); self._wake.set()

    def settings(self) -> dict[str, Any]:
        return self.core.settings.section("learning")

    # ------------------------------------------------------ enregistrement usage
    def _classify_error(self, error: str) -> str:
        low = (error or "").casefold()
        for label, needles in _ERROR_CLASSES:
            if any(n in low for n in needles):
                return label
        first = (error or "").strip().splitlines() or [""]
        return (str(first[0]) or "inconnue")[:60]

    def record_tool(self, raw_tool_id: str, success: bool, duration_ms: float = 0,
                    error: str = "") -> None:
        with self.core.db.transaction():
            self._record_tool(raw_tool_id, success, duration_ms, error)

    def _record_tool(self, raw_tool_id: str, success: bool, duration_ms: float = 0,
                     error: str = "") -> None:
        """Enregistre un usage sous la clé canonique (jamais un nom humain)."""
        tool_id = self._canonical(raw_tool_id)
        now = time.time()
        err = self.core.vault.scrub(error)[:300]
        info = identity.info(tool_id)
        display = info["display_name"] if info else tool_id
        row = self.core.db.one("SELECT * FROM tool_usage WHERE tool_id=?", (tool_id,))
        if row is None:
            self.core.db.execute(
                "INSERT INTO tool_usage(tool_id,display_name,aliases,total_calls,success_calls,"
                "failed_calls,total_duration_ms,avg_duration_ms,first_used_at,last_used_at,"
                "last_error,error_categories,last_failure_at,last_failure_error,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tool_id, display, dumps([]), 1, int(success), int(not success),
                 int(duration_ms), int(duration_ms), now, now, err if not success else "",
                 dumps({self._classify_error(err): 1} if err else {}),
                 now if not success else None, err if not success else "", now))
        else:
            avg_old = float(row["avg_duration_ms"] or 0)
            total = int(row["total_calls"] or 0)
            new_total = total + 1
            avg_new = (float(row["total_duration_ms"] or 0) + duration_ms) / new_total
            categories = loads(row["error_categories"], {}) or {}
            last_failure_at = row["last_failure_at"]
            last_failure_error = row["last_failure_error"]
            if not success:
                reason = self._classify_error(err)
                categories[reason] = int(categories.get(reason, 0)) + 1
                last_failure_at, last_failure_error = now, err
            self.core.db.execute(
                "UPDATE tool_usage SET total_calls=total_calls+1,"
                "success_calls=success_calls+?,failed_calls=failed_calls+?,"
                "total_duration_ms=total_duration_ms+?,avg_duration_ms=?,last_used_at=?,"
                "last_error=?,error_categories=?,last_failure_at=?,last_failure_error=?,"
                "updated_at=? WHERE tool_id=?",
                (int(success), int(not success), int(duration_ms), avg_new, now,
                 err if not success else "", dumps(categories), last_failure_at,
                 last_failure_error, now, tool_id))
            if not success and err:
                self._maybe_queue_error_topic(tool_id, self._classify_error(err),
                                              int(categories.get(self._classify_error(err), 0)))
        self._recompute_expertise(tool_id)
        self._log("LEARNING_TOOL_CANONICAL_ID",
                  f"{tool_id} · {'succès' if success else 'échec'} ({display})", level="info")

    def _maybe_queue_error_topic(self, tool_id: str, reason: str, count: int) -> None:
        if count < REPEATED_FAILURE_TOPIC_AFTER:
            return
        topic = f"Échec récurrent : {reason} → {tool_id}"
        existing = self.core.db.one(
            "SELECT id FROM learning_queue WHERE topic=? AND status='queued'", (topic,))
        if existing:
            return
        self.core.db.execute(
            "INSERT INTO learning_queue(id,tool_id,topic,kind,priority,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (new_id("lq"), tool_id, topic, "error_fix", 90.0, time.time(), time.time()))
        self.core.events.emit("learning.queue.auto_created",
                              {"tool_id": tool_id, "topic": topic, "reason": reason, "count": count})
        self._log("LEARNING_QUEUE_AUTO", topic, level="warn")

    # ---------------------------------------------------------------- priorité
    def _priority(self, row) -> float:
        row = dict(row)
        calls = int(row["total_calls"] or 0)
        fails = int(row["failed_calls"] or 0)
        expertise = float(row["expertise_score"] or 0) / 100.0
        last_used = row["last_used_at"] or 0
        age_days = max(0.0, (time.time() - last_used) / 86400) if last_used else 30.0
        usage_freq = min(20.0, calls * 0.7)
        recency = max(0.0, 10.0 - age_days * 0.4)
        failure = 20.0 * fails / max(1, calls)
        gap = (1.0 - expertise) * 30.0
        docs_age = min(10.0, (time.time() - row["docs_checked_at"]) / 86400 / 18) if row.get("docs_checked_at") else 10.0
        priority = min(100.0, usage_freq + recency + failure + gap + docs_age + 10 * float(row.get("importance_score", .5)))
        return round(priority, 2)

    # ------------------------------------------------------- curriculum / know
    def _topics_for(self, tool_id: str) -> list[tuple[str, list[str]]]:
        return CURRICULA.get(tool_id, DEFAULT_CURRICULUM)

    def _knowledge_for(self, tool_id: str) -> list[dict[str, Any]]:
        """Fiches Knowledge liées à ce tool par `tools` (historique inclus)."""
        needle = f'"{tool_id}"'
        rows = self.core.db.query(
            "SELECT * FROM knowledge WHERE tools LIKE ? ORDER BY updated_at DESC", (f"%{needle}%",))
        out = []
        for r in rows:
            d = {k: r[k] for k in r.keys()}
            d["tags"] = loads(d["tags"], [])
            d["tools"] = loads(d.get("tools"), [])
            d["confidence_score"] = float(d.get("confidence_score") or 0.5)
            if tool_id in d["tools"]:
                out.append(d)
        return out

    @staticmethod
    def _norm(text: str) -> str:
        low = (text or "").casefold()
        return re.sub(r"[éèêë]", "e", re.sub(r"[àâä]", "a", re.sub(r"[ôö]", "o", low)))

    def _coverage(self, tool_id: str, usage_: dict[str, Any],
                  entries: list[dict[str, Any]]) -> tuple[int, int, list[str]]:
        """Sujets maîtrisés (critère 27) : documentation validée + connaissance
        enregistrée + usage/test réussi **si applicable**.

        La troisième preuve dépend du sujet :
        - sujet couvert par un auto-test (TESTABLE_TOPICS) -> il faut un test
          réellement passé ; la doc seule ne suffit jamais ;
        - sinon -> il faut un usage réel réussi de l'outil (au moins
          MIN_SUCCESS_FOR_MASTERY appels réussis et aucun échec dominant).

        Sans cette distinction aucun sujet n'était jamais maîtrisé : une
        session documentaire ne produit pas de test, donc coverage restait
        bloqué à 0/N pour tous les outils.
        """
        topics = self._topics_for(tool_id)
        successes = int(usage_.get("success_calls") or 0)
        total = int(usage_.get("total_calls") or 0)
        success_rate = successes / total if total else 0.0
        proven_usage = (successes >= MIN_SUCCESS_FOR_MASTERY
                        and success_rate >= MIN_SUCCESS_RATE_FOR_MASTERY)
        mastered = []
        for topic, _ in topics:
            linked = [e for e in entries if e.get("status") == "active"
                      and topic in evidence(e).get("topics", [])]
            documented = any(evidence(e).get("docs_validated") for e in linked)
            recorded = any(is_verified(e) for e in linked)
            tested = any(evidence(e).get("tests_passed", 0) > 0 for e in linked)
            applicable = topic in TESTABLE_TOPICS
            third_proof = tested if applicable else (tested or proven_usage)
            if documented and recorded and third_proof:
                mastered.append(topic)
        return len(topics), len(mastered), mastered

    def _recompute_expertise(self, tool_id: str) -> dict[str, Any]:
        """Recalcule le score d'expertise d'un tool et le persiste."""
        row = self.core.db.one("SELECT * FROM tool_usage WHERE tool_id=?", (tool_id,))
        if row is None:
            return {}
        usage = dict(row)
        entries = self._knowledge_for(tool_id)
        total, mastered, mastered_list = self._coverage(tool_id, usage, entries)
        usage.update(coverage_total=total, coverage_mastered=mastered)
        score, components = compute_expertise(usage=usage, knowledge=entries)
        now = time.time()
        self.core.db.execute(
            "UPDATE tool_usage SET expertise_score=?, expertise_components=?, knowledge_count=?,"
            "coverage_total=?, coverage_mastered=?, updated_at=? WHERE tool_id=?",
            (score, dumps(components), sum(e.get("status") == "active" for e in entries), total, mastered, now, tool_id))
        info = identity.info(tool_id)
        self._log("EXPERTISE_RECOMPUTED",
                  f"{info['display_name'] if info else tool_id} → {score}% ({mastered}/{total} sujets)",
                  level="info")
        for kind, value in (("EXPERTISE_BEFORE", row["expertise_score"]), ("EXPERTISE_AFTER", score),
                            ("EXPERTISE_COMPONENTS", components)):
            self._log(kind, f"{tool_id}: {dumps(value)}")
        return {"tool_id": tool_id, "expertise": score, "components": components,
                "coverage_total": total, "coverage_mastered": mastered,
                "mastered": mastered_list, "knowledge": entries}

    def _ensure_usage_row(self, tool_id: str) -> None:
        """Crée le support métrique avant le premier apprentissage.

        Une session peut sélectionner un outil du registre alors qu'il n'a
        encore jamais été exécuté. Sans cette ligne, le recalcul d'expertise
        n'a aucune cible SQL et une session terminée à 100 % laisse donc le
        score à 0 par défaut.
        """
        if self.core.db.one("SELECT 1 FROM tool_usage WHERE tool_id=?", (tool_id,)):
            return
        info = identity.info(tool_id)
        self.core.db.execute(
            "INSERT INTO tool_usage(tool_id,display_name,aliases,updated_at) VALUES(?,?,?,?)",
            (tool_id, info["display_name"] if info else tool_id,
             dumps(info.get("aliases", []) if info else []), time.time()))

    # ----------------------------------------------------------- choix du sujet
    def choose_topic(self) -> dict[str, Any] | None:
        id_ = identity
        queued = self.core.db.query(
            "SELECT * FROM learning_queue WHERE status='queued' ORDER BY priority DESC LIMIT 5")
        if queued:
            q = queued[0]
            tool_id = id_.canonical(q["tool_id"]) if q["tool_id"] else ""
            info = id_.info(tool_id)
            url = self._url_for(tool_id)
            return {"tool_id": tool_id, "topic": q["topic"], "url": url,
                    "priority": q["priority"], "kind": q["kind"] or "documentation",
                    "queue_id": q["id"]}
        rows = self.core.db.query("SELECT * FROM tool_usage ORDER BY total_calls DESC")
        if not rows:
            candidates = [id_.canonical(t.id) for t in self.core.registry.all() if t.enabled
                          and not t.id.startswith("avatar.")]
            if not candidates:
                return None
            tool = next((x for x in candidates if any(k in x.casefold() for k in OFFICIAL)),
                        candidates[0])
            topic = f"Documentation officielle — {id_.display_name(tool)}"
            return {"tool_id": tool, "topic": topic, "url": self._url_for(tool),
                    "priority": 1.0, "kind": "documentation", "queue_id": ""}
        # Prefer tools not revisited recently; retained history prevents a hot loop.
        def scheduling_weight(row):
            last = self.core.db.scalar("SELECT MAX(started_at) FROM learning_sessions WHERE tool_id=?", (row["tool_id"],))
            return self._priority(row) - (100 if last and time.time() - last < 3600 else 0)
        tool = max(rows, key=scheduling_weight)
        tool_id = id_.canonical(tool["tool_id"])
        count = self.core.db.scalar("SELECT COUNT(*) FROM learning_sessions WHERE tool_id=?", (tool_id,)) or 0
        curriculum = self._topics_for(tool_id)
        subject = curriculum[count % len(curriculum)][0]
        topic = f"Documentation officielle — {id_.display_name(tool_id)} — {subject}"
        priority = self._priority(tool)
        existing = self.core.db.one(
            "SELECT id FROM learning_queue WHERE tool_id=? AND topic=? AND status='queued'",
            (tool_id, topic))
        queue_id = existing["id"] if existing else ""
        if not existing:
            queue_id = new_id("lq")
            self.core.db.execute(
                "INSERT INTO learning_queue(id,tool_id,topic,kind,priority,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (queue_id, tool_id, topic, "documentation", priority, time.time(), time.time()))
        return {"tool_id": tool_id, "topic": topic, "url": self._url_for(tool_id),
                "priority": priority, "kind": "documentation", "queue_id": queue_id}

    @staticmethod
    def _url_for(tool_id: str) -> str:
        for key in OFFICIAL:
            if key in tool_id.casefold():
                return OFFICIAL[key]
        return "registry:" + tool_id

    def _read_source(self, url: str) -> str:
        if url.startswith("registry:"):
            tool = self.core.registry.get(url.split(":", 1)[1])
            return f"{tool.description}\nArguments : {dumps(tool.input_schema)}" if tool else ""
        if not url or not self.settings().get("web_enabled", True):
            return ""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-learning/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = resp.read(120_000).decode("utf-8", "ignore")
            text = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", raw, flags=re.S | re.I)
            return re.sub(r"\s+", " ", html.unescape(text)).strip()[:5000]
        except Exception:
            return ""

    # --------------------------------------------------------------- session
    def _run_self_test(self, tool_id: str, error_reason: str) -> tuple[int, int]:
        """Test local réel d'une procédure (aucun appel réseau).

        Ex. « working_directory » : vérifie que le chemin relatif se résout
        dans la vraie racine du connecteur actif.
        """
        if "working_directory" not in error_reason.casefold():
            return 0, 0
        active = self.core.connectors.active("ssh")
        connector = self.core.connectors.raw(active[0]["id"]) if active else None
        if not connector:
            return 0, 0
        try:
            resolved = resolveRemotePath(connector, "test.txt", {})
            root = resolveRemotePath(connector, ".", {})
            passed = resolved == posixpath.join(root, "test.txt")
            try:
                resolveRemotePath(connector, "../escape.txt", {})
                passed = False
            except Exception:
                pass
            return 2, 2 if passed else 0
        except Exception:
            return 1, 0

    def run_once(self) -> dict[str, Any]:
        if not self._session_lock.acquire(blocking=False):
            return {"ok": False, "reason": "already_running"}
        try:
            return self._run_once()
        except Exception as exc:
            sid = self.current.get("session_id")
            if sid:
                self.core.db.execute("UPDATE learning_sessions SET status='failed',error=?,finished_at=? WHERE id=?",
                                     (self.core.vault.scrub(str(exc)), time.time(), sid))
            self.current["error"] = str(exc)
            self._log("LEARNING_SESSION_FAILED", str(exc), "error")
            return {"ok": False, "error": str(exc)}
        finally:
            if self.state == "LEARNING":
                self.state = "IDLE"
            self._session_lock.release()

    def _run_once(self) -> dict[str, Any]:
        topic = self.choose_topic()
        if not topic:
            return {"ok": False, "reason": "no_tool_usage"}
        sid = new_id("learn")
        now = time.time()
        tool_id = self._canonical(topic["tool_id"])
        self._ensure_usage_row(tool_id)
        self.current = {**topic, "tool_id": tool_id, "session_id": sid, "progress": 0}
        self.state = "LEARNING"

        row = self.core.db.one("SELECT * FROM tool_usage WHERE tool_id=?", (tool_id,))
        expertise_before = float(row["expertise_score"] or 0) if row else 0.0
        info = identity.info(tool_id)
        display = info["display_name"] if info else tool_id

        meta = {"source": topic.get("url", ""), "kind": topic.get("kind", "documentation")}
        self.core.db.execute(
            "INSERT INTO learning_sessions(id,status,started_at,last_activity_at,tool_id,topic,meta) "
            "VALUES(?,?,?,?,?,?,?)",
            (sid, "running", now, now, tool_id, topic["topic"], dumps(meta)))
        self.core.events.emit("learning.session.started", {"session_id": sid, **topic})
        self.core.events.emit("learning.topic.selected", {**topic, "tool_id": tool_id})

        result: dict[str, Any] = {
            "tool_id": tool_id, "sources_checked": 0, "knowledge_created": 0,
            "knowledge_updated": 0, "duplicates_merged": 0, "deprecated_marked": 0,
            "validated": 0, "tests_run": 0, "tests_passed": 0,
            "expertise_before": expertise_before, "expertise_after": expertise_before,
            "no_new_knowledge": True, "note": "",
        }
        knowledge_created_events: list[str] = []

        kind = topic.get("kind", "documentation")
        if kind == "error_fix":
            self._apply_error_fix(tool_id, display, topic["topic"], sid, result, knowledge_created_events)
        elif topic.get("url") and not self._stop.is_set() and self.state == "LEARNING":
            self.core.events.emit("learning.search.started",
                                  {"session_id": sid, "query": topic["topic"]})
            source = self._read_source(topic["url"])
            result["sources_checked"] = 1
            result["sources"] = [{"url": topic["url"], "checked_at": time.time(),
                                  "status": "read" if source else "unavailable",
                                  "sha256": hashlib.sha256(source.encode()).hexdigest() if source else ""}]
            self.core.db.execute(
                "INSERT OR REPLACE INTO learning_sources"
                "(id,session_id,tool_id,topic,url,content_hash,checked_at,version,status) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (new_id("src"), sid, tool_id, topic["topic"], topic["url"],
                 result["sources"][0]["sha256"], time.time(), "unknown", result["sources"][0]["status"]))
            if self.state != "LEARNING" or self._stop.is_set():
                source = ""
            if source:
                result["sources_checked"] = 1
                self._apply_docs(tool_id, display, source, topic["url"], sid, result,
                                 knowledge_created_events)

        if not result["no_new_knowledge"]:
            # On valide d'abord les connaissances réellement produites, PENS
            # le score d'expertise est recalculé : la validation doit peser.
            for kid in knowledge_created_events:
                self.core.memory.record_validation(kid, success=True)
                result["validated"] += 1
            now_docs = time.time()
            self.core.db.execute("UPDATE tool_usage SET last_learning_at=?,updated_at=? WHERE tool_id=?",
                                 (now_docs, now_docs, tool_id))
            if kind == "documentation":
                self.core.db.execute("UPDATE tool_usage SET docs_checked_at=?,docs_version='unknown' WHERE tool_id=?",
                                     (now_docs, tool_id))
            # Les métadonnées de fraîcheur et de version font partie de la
            # preuve d'apprentissage : elles doivent être présentes avant le
            # calcul du score retourné à l'UI et écrit dans l'historique.
            expertise = self._recompute_expertise(tool_id)
            result["expertise_after"] = float(expertise.get("expertise", expertise_before))
        else:
            result["note"] = "No new validated knowledge"
            self._log("LEARNING_NO_NEW_KNOWLEDGE", f"{tool_id} — {result['note']}", level="info")

        status = "paused" if self.state == "PAUSED" else "completed"
        result["learning_success"] = not result["no_new_knowledge"]
        result["knowledge_validated"] = result["validated"]
        self.core.db.execute(
            "UPDATE learning_sessions SET status=?,finished_at=?,progress=?,knowledge_created=?,"
            "knowledge_updated=?,knowledge_validated=?,duplicates_merged=?,deprecated_marked=?,"
            "tests_run=?,tests_passed=?,expertise_before=?,expertise_after=?,result=? WHERE id=?",
            (status, time.time(), 0.0 if status == "paused" else 1.0, result["knowledge_created"], result["knowledge_updated"],
             result["validated"], result["duplicates_merged"], result["deprecated_marked"],
             result["tests_run"], result["tests_passed"], expertise_before, result["expertise_after"],
             dumps(result), sid))
        if topic.get("queue_id") and status == "completed":
            self.core.db.execute(
                "UPDATE learning_queue SET status='completed', completed_at=?, updated_at=? WHERE id=?",
                (time.time(), time.time(), topic["queue_id"]))

        self.core.events.emit("learning.session.completed", {
            "session_id": sid, "tool_id": tool_id, "result": result,
            "expertise_before": expertise_before, "expertise_after": result["expertise_after"],
        })
        self._log("LEARNING_SESSION_RESULT",
                  f"{tool_id} : {result['knowledge_created']} créées / "
                  f"{result['knowledge_updated']} mises à jour · "
                  f"{result['expertise_before']:.0f}% → {result['expertise_after']:.0f}%",
                  level="info")
        if self.state == "LEARNING":
            self.state = "IDLE"
        self.current["progress"] = 0.0 if status == "paused" else 1.0
        self.current["result"] = result
        return {"ok": True, "session_id": sid, "tool_id": tool_id, "result": result}

    def _apply_docs(self, tool_id: str, display: str, source: str, url: str, sid: str,
                    result: dict[str, Any], created_events: list[str]) -> None:
        safe = self.core.vault.scrub(source)
        title = f"{display} — Documentation officielle"
        existing = self.core.memory.knowledge_get_by_title(title)
        covered = [t for t, kws in self._topics_for(tool_id)
                   if any(re.search(r"(?<!\w)" + re.escape(self._norm(kw)) + r"(?!\w)", self._norm(safe)) for kw in kws)]
        content = safe[:5000]
        proof = {"topics": covered, "docs_validated": True, "source_type": "official_docs",
                 "source_version": "unknown", "source_hash": hashlib.sha256(content.encode()).hexdigest(),
                 "session_id": sid, "verified_at": time.time()}
        if existing:
            changed = (existing.get("content") or "") != content
            if changed:
                self.core.memory.knowledge_update(
                    existing["id"], content=content,
                    tags=list(dict.fromkeys([*existing.get("tags", []), "idle-learning", tool_id])),
                    tools=list(dict.fromkeys([*existing.get("tools", []), tool_id])),
                    status="active", verification_method="official_source", kind="API_REFERENCE", evidence=proof, source=url)
                result["knowledge_updated"] += 1
                result["no_new_knowledge"] = False
                created_events.append(existing["id"])
                self.core.events.emit("learning.knowledge.updated",
                                      {"id": existing["id"], "tool_id": tool_id})
                self._log("KNOWLEDGE_UPDATED", f"{tool_id} — {title}", level="info")
            else:
                result["note"] = "Même contenu déjà connu (aucun changement)."
        else:
            item = self.core.memory.knowledge_add(
                title=title, content=content, kind="API_REFERENCE",
                tags=["idle-learning", tool_id], tools=[tool_id],
                source=url or "official_docs", verification_method="official_source", evidence=proof)
            result["knowledge_created"] += 1
            result["no_new_knowledge"] = False
            created_events.append(item["id"])
            self.core.brain.add_node(
                kind="knowledge", family="KNOWLEDGE", label=title[:120],
                ref_type="knowledge", ref_id=item["id"],
                meta={"tool_id": tool_id, "source": url or "official_docs",
                      "kind": "API_REFERENCE"})
            try:
                self.core.brain.add_edge(f"tool:{tool_id}", f"kb:{item['id']}", "teaches")
            except Exception:
                pass
            self.core.events.emit("learning.knowledge.created",
                                  {"id": item["id"], "tool_id": tool_id})
            self._log("KNOWLEDGE_CREATED", f"{tool_id} — « {title} »", level="info")

    def _apply_error_fix(self, tool_id: str, display: str, topic_text: str, sid: str,
                         result: dict[str, Any], created_events: list[str]) -> None:
        row = self.core.db.one("SELECT * FROM tool_usage WHERE tool_id=?", (tool_id,))
        if not row:
            return
        categories = loads(row["error_categories"], {}) or {}
        reason = next((r for r in categories if r in topic_text), "") or (max(categories, key=categories.get) if categories else "erreur récurrente")
        count = int(categories.get(reason, 0))
        title = f"{display} — échecs récurrents ({reason})"
        existing = self.core.memory.knowledge_get_by_title(title)
        content = (f"Échec récurrent ({count}x) sur {tool_id} : {reason}. "
                   f"Dernier message : {row['last_failure_error'] or ''}".strip())[:1200]
        tests_run, tests_passed = self._run_self_test(tool_id, reason)
        result["tests_run"] += tests_run
        result["tests_passed"] += tests_passed
        method = "tested" if tests_passed else ""
        proof = {"topics": ["working_directory", "chemins relatifs"] if tests_passed else [],
                 "tests_passed": tests_passed, "tests_run": tests_run, "error_reason": reason,
                 "test_scope": "local path resolver; does not prove remote file access"}
        if tests_passed:
            content = (f"Résolution locale vérifiée pour {tool_id}: test.txt est résolu sous le répertoire "
                       "de travail du connecteur SSH actif; ../escape.txt est refusé. "
                       "Ce test ne valide ni les permissions distantes ni l'existence du fichier.")
        if existing:
            if (existing.get("content") or "") != content or evidence(existing).get("tests_passed", 0) != tests_passed:
                self.core.memory.knowledge_update(
                    existing["id"], content=content, kind="ERROR_FIX",
                    tags=list(dict.fromkeys([*existing.get("tags", []), "idle-learning", tool_id])),
                    tools=list(dict.fromkeys([*existing.get("tools", []), tool_id])),
                    verification_method=method, evidence=proof)
                result["knowledge_updated"] += 1
                result["no_new_knowledge"] = not bool(tests_passed)
                if tests_passed:
                    created_events.append(existing["id"])
                self._log("KNOWLEDGE_UPDATED", f"{tool_id} — échec récurrent ({reason})", level="info")
        else:
            item = self.core.memory.knowledge_add(
                title=title, content=content, kind="ERROR_FIX",
                tags=["idle-learning", tool_id], tools=[tool_id], source="learning:errors",
                verification_method=method, evidence=proof)
            result["knowledge_created"] += 1
            result["no_new_knowledge"] = not bool(tests_passed)
            if tests_passed:
                created_events.append(item["id"])
            self.core.brain.add_node(
                kind="knowledge", family="ERRORS", label=title[:120],
                ref_type="knowledge", ref_id=item["id"],
                meta={"tool_id": tool_id, "reason": reason, "kind": "ERROR_FIX"})
            try:
                self.core.brain.add_edge(f"tool:{tool_id}", f"kb:{item['id']}", "teaches")
            except Exception:
                pass
            self._log("KNOWLEDGE_CREATED", f"{tool_id} — fiche d'erreur ({reason})", level="warn")
        if tests_passed:
            self._log("LEARNING_TEST_PASSED",
                      f"{tool_id} : test de résolution « {reason} » validé", level="info")

    def run_manual(self) -> dict[str, Any]:
        if self.state == "LEARNING":
            return {"ok": True, "started": False, "reason": "already_running"}
        self.state = "IDLE"
        threading.Thread(target=self.run_once, daemon=True, name="jarvis-learning-manual").start()
        return {"ok": True, "started": True}

    def _loop(self) -> None:
        while not self._stop.wait(5):
            cfg = self.settings()
            if not cfg.get("enabled", True):
                self.state = "SLEEPING"
                continue
            idle = time.time() - self.last_activity_at
            if self._manual_paused:
                continue
            if self.state in {"ACTIVE", "PAUSED", "SLEEPING"} and idle >= int(cfg.get("idle_after_s", 600)):
                self.state = "IDLE"
                self.core.events.emit("learning.idle.detected", {"idle_s": int(idle)})
            if self.state == "IDLE":
                last = self.core.db.scalar("SELECT MAX(started_at) FROM learning_sessions")
                if last and time.time() - last < max(60, int(cfg.get("session_interval_s", 3600))):
                    continue
                self.run_once()

    # ------------------------------------------------------------- migration
    def migrate(self) -> dict[str, Any]:
        """Migration explicite (déduplication canonique) + backup."""
        return self._migrate_data()

    def _migrate_data(self) -> dict[str, Any]:
        # v10 : nouvelles règles de maîtrise (coverage) -> il faut réexécuter la
        # migration une fois pour recalculer expertise/coverage des données déjà
        # dédupliquées par v9 (sinon coverage_mastered reste figé à 0).
        name = "canonical-learning-v10"
        previous = self.core.db.one("SELECT * FROM learning_migrations WHERE name=?", (name,))
        aliases = any(self._canonical(r["tool_id"]) != r["tool_id"]
                      for r in self.core.db.query("SELECT tool_id FROM tool_usage"))
        if previous and not aliases:
            return {**loads(previous["result"], {}), "already_applied": True}
        # A failed backup prevents any mutation. All merges then commit together.
        backup = self.core.db.backup("learning-v10-" + new_id())
        with self.core.db.transaction():
            result = self._migrate_rows()
            result["backup"] = str(backup)
            self.core.db.execute("INSERT OR REPLACE INTO learning_migrations VALUES(?,?,?,?)",
                                 (name, time.time(), str(backup), dumps(result)))
        return result

    def _migrate_rows(self) -> dict[str, Any]:
        id_ = identity
        backup = None
        rows = self.core.db.query("SELECT * FROM tool_usage")
        merged: dict[str, dict[str, Any]] = {}
        merged_aliases: dict[str, list[str]] = {}
        for r in rows:
            key_full = r["tool_id"]
            canonical = id_.canonical(key_full)
            bucket = merged.get(canonical)
            if bucket is None:
                merged[canonical] = dict(r)
                merged[canonical]["tool_id"] = canonical
                merged_aliases[canonical] = []
            elif key_full != canonical:
                self._merge_usage(bucket, dict(r))
            else:
                # La ligne canonique peut arriver après un bucket créé depuis
                # une ligne non canonique (ex. 'Lire un fichier distant' 23
                # puis 'ssh.read_file' 4) : on doit fusionner ses données aussi.
                self._merge_usage(bucket, dict(r))
            if key_full != canonical:
                merged_aliases[canonical].append(key_full)
            merged_aliases[canonical].extend(loads(r["aliases"], []))
        for canonical, row in merged.items():
            self._write_merged_row(canonical, row, merged_aliases.get(canonical, []))
        # Suppression de toute ligne non canonique (l'identité fusionnée a
        # déjà été écrite sur la ligne canonique).
        merged_aliases_set = {a for lst in merged_aliases.values() for a in lst}
        non_canonical = [r["tool_id"] for r in rows
                         if r["tool_id"] in merged_aliases_set
                         and id_.canonical(r["tool_id"]) != r["tool_id"]]
        for raw in non_canonical:
            self.core.db.execute("DELETE FROM tool_usage WHERE tool_id=?", (raw,))
        if non_canonical:
            self._log("LEARNING_ALIASES_MERGED",
                      f"doublons fusionnés : {', '.join(non_canonical[:8])} "
                      f"(backup {backup})", level="info")

        # Réécrit l'attachement Knowledge (tools) en clés canoniques.
        touches = 0
        for raw in self.core.db.query("SELECT * FROM knowledge"):
            k = dict(raw)
            k["tools"] = loads(k.get("tools"), [])
            tools = list(dict.fromkeys(id_.canonical(t) for t in (k.get("tools") or [])))
            if tools != (k.get("tools") or []):
                self.core.db.execute("UPDATE knowledge SET tools=? WHERE id=?", (dumps(tools), k["id"]))
                touches += 1
            # Remove the synthetic curriculum footer without rewriting historical sources.
            if "\n\nSujets de référence :" in k["content"]:
                self.core.db.execute("UPDATE knowledge SET content=?, evidence='{}' WHERE id=?",
                    (k["content"].split("\n\nSujets de référence :")[0], k["id"]))
            for tid in tools:
                self._ensure_usage_row(tid)
        # tool_id des sessions -> canonique.
        for s in self.core.db.query("SELECT id, tool_id FROM learning_sessions"):
            canon = id_.canonical(s["tool_id"])
            if canon != s["tool_id"]:
                self.core.db.execute("UPDATE learning_sessions SET tool_id=? WHERE id=?",
                                     (canon, s["id"]))
        # learning_queue -> canonique.
        for q in self.core.db.query("SELECT id, tool_id FROM learning_queue WHERE tool_id<>''"):
            canon = id_.canonical(q["tool_id"])
            if canon != q["tool_id"]:
                self.core.db.execute("UPDATE learning_queue SET tool_id=? WHERE id=?",
                                     (canon, q["id"]))

        # Recalcule l'expertise de tous les outils suivis.
        for canonical in merged:
            self._recompute_expertise(canonical)
        count = len(merged)
        if count:
            self._log("LEARNING_MIGRATION",
                      f"{count} identités canoniques, {len(non_canonical)} doublons fusionnés",
                      level="info")
        return {"merged_tools": count, "removed_duplicates": len(non_canonical),
                "knowledge_relinked": touches, "backup": str(backup) if backup else ""}

    @staticmethod
    def _merge_usage(target: dict[str, Any], source: dict[str, Any]) -> None:
        from .db import loads as _loads
        for row in (target, source):
            if not row.get("total_duration_ms") and row.get("avg_duration_ms"):
                row["total_duration_ms"] = float(row["avg_duration_ms"]) * int(row.get("total_calls") or 0)
        target["total_calls"] = int(target.get("total_calls") or 0) + int(source.get("total_calls") or 0)
        target["success_calls"] = int(target.get("success_calls") or 0) + int(source.get("success_calls") or 0)
        target["failed_calls"] = int(target.get("failed_calls") or 0) + int(source.get("failed_calls") or 0)
        target["total_duration_ms"] = int(target.get("total_duration_ms") or 0) + int(source.get("total_duration_ms") or 0)
        # first_used_at : plus ancien ; last_used_at : plus récent.
        f1 = target.get("first_used_at") or 0
        f2 = source.get("first_used_at") or 0
        target["first_used_at"] = min(f1, f2) if f1 and f2 else (f1 or f2 or None)
        l1 = target.get("last_used_at") or 0
        l2 = source.get("last_used_at") or 0
        target["last_used_at"] = max(l1, l2)
        lf1 = target.get("last_failure_at") or 0
        lf2 = source.get("last_failure_at") or 0
        target["last_failure_at"] = max(lf1, lf2) or None
        target["last_failure_error"] = (source.get("last_failure_error")
                                        if lf2 >= lf1 else target.get("last_failure_error"))
        target["last_error"] = target.get("last_failure_error") or target.get("last_error") or source.get("last_error") or ""
        cats = target.get("error_categories") or {}
        cats = _loads(cats, {}) if isinstance(cats, str) else cats
        for reason, n in (_loads(source.get("error_categories"), {}) or {}).items():
            cats[reason] = int(cats.get(reason, 0)) + int(n)
        target["error_categories"] = cats
        target["last_learning_at"] = max(target.get("last_learning_at") or 0, source.get("last_learning_at") or 0) or None
        target["importance_score"] = max(target.get("importance_score") or .5, source.get("importance_score") or .5)
        # date docs la plus récente / version conservée.
        if (source.get("docs_checked_at") or 0) > (target.get("docs_checked_at") or 0):
            target["docs_checked_at"] = source["docs_checked_at"]
            target["docs_version"] = source.get("docs_version") or target.get("docs_version")
        target["updated_at"] = max(target.get("updated_at") or 0, source.get("updated_at") or 0,
                                   time.time())

    def _write_merged_row(self, canonical: str, row: dict[str, Any], aliases: list[str]) -> None:
        from .db import dumps as _dumps
        info = identity.info(canonical)
        # Si aucune alias n'a été trouvée lors du merge (données déjà nettoyées),
        # on reconstruit à partir de l'identité centrale.
        aliases = list(dict.fromkeys(aliases))
        for key in ("error_categories", "expertise_components"):
            if isinstance(row.get(key), str):
                row[key] = loads(row[key], {})
        if not aliases and info and info.get("display_name") and info["display_name"] != canonical:
            aliases = list(dict.fromkeys(
                a for a in [info["display_name"]] + (info.get("aliases") or []) if a != canonical))
        total = max(0, int(row.get("total_calls") or 0))
        avg = int((row.get("total_duration_ms") or 0) / total) if total else 0
        self.core.db.execute(
            "INSERT INTO tool_usage(tool_id,display_name,aliases,total_calls,success_calls,"
            "failed_calls,total_duration_ms,avg_duration_ms,first_used_at,last_used_at,last_error,"
            "error_categories,last_failure_at,last_failure_error,docs_checked_at,docs_version,"
            "importance_score,expertise_score,expertise_components,knowledge_count,coverage_total,"
            "coverage_mastered,last_learning_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(tool_id) DO UPDATE SET display_name=excluded.display_name,"
            "aliases=CASE WHEN excluded.aliases='[]' THEN tool_usage.aliases "
            "ELSE excluded.aliases END,total_calls=excluded.total_calls,"
            "success_calls=excluded.success_calls,failed_calls=excluded.failed_calls,"
            "total_duration_ms=excluded.total_duration_ms,avg_duration_ms=excluded.avg_duration_ms,"
            "first_used_at=excluded.first_used_at,last_used_at=excluded.last_used_at,"
            "last_error=excluded.last_error,error_categories=excluded.error_categories,"
            "last_failure_at=excluded.last_failure_at,last_failure_error=excluded.last_failure_error,"
            "docs_checked_at=excluded.docs_checked_at,docs_version=excluded.docs_version,"
            "expertise_components=excluded.expertise_components,"
            "knowledge_count=excluded.knowledge_count,coverage_total=excluded.coverage_total,"
            "coverage_mastered=excluded.coverage_mastered,last_learning_at=excluded.last_learning_at,"
            "updated_at=excluded.updated_at WHERE tool_id=?",
            (canonical, info["display_name"] if info else canonical, _dumps(aliases or []),
             int(row.get("total_calls") or 0), int(row.get("success_calls") or 0),
             int(row.get("failed_calls") or 0), int(row.get("total_duration_ms") or 0), avg,
             row.get("first_used_at"), row.get("last_used_at"), row.get("last_error") or "",
             _dumps(row.get("error_categories") or {}), row.get("last_failure_at"),
             row.get("last_failure_error") or "", row.get("docs_checked_at"),
             row.get("docs_version") or "", float(row.get("importance_score") or 0.5),
             float(row.get("expertise_score") or 0.0), _dumps(row.get("expertise_components") or {}),
             int(row.get("knowledge_count") or 0), int(row.get("coverage_total") or 0),
             int(row.get("coverage_mastered") or 0), row.get("last_learning_at"),
             time.time(), canonical))

    # ---------------------------------------------------------------- statut
    def status(self) -> dict[str, Any]:
        queue = [dict(r) for r in self.core.db.query(
            "SELECT * FROM learning_queue WHERE status='queued' ORDER BY priority DESC LIMIT 20")]
        sessions = [dict(r) for r in self.core.db.query(
            "SELECT * FROM learning_sessions ORDER BY started_at DESC LIMIT 15")]
        usage = []
        for r in self.core.db.query("SELECT * FROM tool_usage ORDER BY total_calls DESC"):
            d = dict(r)
            d["tool_id"] = self._canonical(d["tool_id"])
            info = identity.info(d["tool_id"])
            d["display_name"] = info["display_name"] if info else d.get("display_name") or d["tool_id"]
            d["aliases"] = loads(d.get("aliases"), [])
            d["error_categories"] = loads(d.get("error_categories"), {})
            comps = loads(d.get("expertise_components"), {})
            d["expertise_components"] = comps
            d["expertise"] = int(float(d.get("expertise_score") or 0))
            d["success_rate"] = round(
                int(d.get("success_calls") or 0) / int(d.get("total_calls") or 1), 3)
            d["priority"] = self._priority(d)
            d["knowledge"] = self._knowledge_for(d["tool_id"])
            usage.append(d)
        return {"state": self.state, "last_activity_at": self.last_activity_at,
                "idle_s": int(time.time() - self.last_activity_at), "current": self.current,
                "queue": queue, "sessions": sessions, "tool_usage": usage,
                "settings": self.settings()}
