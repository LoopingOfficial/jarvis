"""Avatar Live — jobs d'avatar 3D observables en direct.

Un job lance UN seul processus Blender de longue duree (pas un processus par
etape) qui construit ou modifie l'avatar et publie, pendant qu'il travaille :

  * des evenements reels (`live/events.ndjson`), relayes tels quels sur le bus
    SSE de JARVIS ;
  * des GLB intermediaires (`live/current.glb`) recharges par le viewer 3D ;
  * quatre rendus d'apercu sous des angles fixes (face, 3/4, profil, plein pied).

Cote hote, ce module :
  * calcule une progression REELLE, ponderee par les etapes effectivement
    terminees par Blender (jamais une interpolation temporelle) ;
  * surveille le heartbeat et les timeouts par etape ;
  * gere pause / reprise / annulation / consignes utilisateur en cours de job ;
  * conserve tout sur disque pour qu'un rechargement de page retrouve le job.

Arborescence d'un job :

    data/generated/avatar/jobs/<job_id>/
        reference/          copie de l'image de reference
        before/             etat de l'avatar avant le job (GLB + rendus)
        live/               current.glb, front.png, ..., events.ndjson, state.json
        revisions/<n>/      snapshot fige de chaque version
        final/              avatar.blend, avatar.glb, rendus haute qualite
        metadata.json       etat complet du job (source de verite au reload)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .db import new_id

JOBS_ROOT = DATA_DIR / "generated" / "avatar" / "jobs"
SCRIPT_PATH = Path(__file__).resolve().parent / "blender_scripts" / "avatar_live_job.py"

# Poids de progression par etape. Les etapes desactivees sont retirees et le
# total est renormalise : 100 % ne peut pas etre atteint par une etape sautee.
STAGE_WEIGHTS: dict[str, int] = {
    "reference": 5,
    "load": 5,
    "body": 10,
    "face": 20,
    "hair": 15,
    "outfit": 15,
    "materials": 10,
    "rig": 5,
    "finalize": 10,
    "evaluation": 5,
}

STAGE_LABELS: dict[str, str] = {
    "reference": "Analyse de la référence",
    "load": "Chargement de l'avatar",
    "body": "Morphologie du corps",
    "face": "Visage",
    "hair": "Cheveux",
    "outfit": "Tenue",
    "materials": "Matériaux",
    "rig": "Rig",
    "finalize": "Rendus & export",
    "evaluation": "Évaluation",
}

# Option qui conditionne une etape Blender (absente => etape toujours active).
STAGE_OPTION = {
    "body": "modify_proportions",
    "face": "modify_face",
    "hair": "modify_hair",
    "outfit": "modify_outfit",
    "materials": "modify_materials",
}

LIVE_VIEWS = ("front", "three_quarter", "side", "full_body")

QUALITIES = ("low", "balanced", "high")

# Delai sans le moindre evenement Blender au-dela duquel le job est signale
# comme bloque (l'UI l'affiche, la progression n'avance pas toute seule).
STALL_AFTER_S = 45.0
DEFAULT_STAGE_TIMEOUT_S = 420.0
DEFAULT_JOB_TIMEOUT_S = 2400.0

# Exigence 40 : un job n'est pas « terminé » parce que Blender n'a pas planté.
# Sous ces seuils, le résultat est une candidate de qualité insuffisante.
QUALITY_THRESHOLDS = {
    "face_score": 70,
    "hair_score": 65,
    "outfit_score": 70,
    "style_score": 70,
    "overall_score": 70,
}

MAX_TIMELINE = 400


class AnalysisFailed(Exception):
    """L'analyse visuelle de la reference a echoue : generation suspendue."""


def _log(message: str) -> None:
    line = f"[avatar_live] {message}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)
    except Exception:
        pass


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:
        _log(f"metadata write failed: {exc}")


class AvatarLiveManager:
    """Cycle de vie des jobs d'avatar live."""

    def __init__(self, core) -> None:
        self._core = core
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._procs: dict[str, subprocess.Popen] = {}
        JOBS_ROOT.mkdir(parents=True, exist_ok=True)
        self._load_from_disk()

    # ------------------------------------------------------------- helpers
    @staticmethod
    def job_dir(job_id: str) -> Path:
        return JOBS_ROOT / job_id

    def _emit(self, event_type: str, job: dict[str, Any], **extra) -> None:
        payload = {
            "job_id": job["id"],
            "status": job.get("status", ""),
            "stage": job.get("stage", ""),
            "stage_label": STAGE_LABELS.get(job.get("stage", ""), job.get("stage", "")),
            "progress": round(float(job.get("progress") or 0.0), 4),
            "version": int(job.get("version") or 0),
            "conversation_id": job.get("conversation_id", ""),
            "reference_id": job.get("reference_id", ""),
        }
        payload.update(extra)
        try:
            self._core.events.emit(event_type, payload)
        except Exception as exc:
            _log(f"emit {event_type} failed: {exc}")

    def _timeline(self, job: dict[str, Any], message: str, kind: str = "info") -> None:
        if not message:
            return
        entry = {"ts": time.time(), "message": str(message)[:200], "kind": kind,
                 "stage": job.get("stage", "")}
        job.setdefault("timeline", []).append(entry)
        if len(job["timeline"]) > MAX_TIMELINE:
            del job["timeline"][:len(job["timeline"]) - MAX_TIMELINE]

    def _save(self, job: dict[str, Any]) -> None:
        _atomic_write_json(self.job_dir(job["id"]) / "metadata.json", job)

    def _load_from_disk(self) -> None:
        """Reprend les jobs connus. Un job « running » orphelin est marque echoue."""
        if not JOBS_ROOT.is_dir():
            return
        for path in sorted(JOBS_ROOT.glob("*/metadata.json")):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(job, dict) or not job.get("id"):
                continue
            if job.get("status") in {"running", "paused", "queued"}:
                # Le processus Blender ne survit pas a un redemarrage de JARVIS.
                job["status"] = "failed"
                job["error"] = job.get("error") or (
                    "JARVIS a redémarré pendant le job — dernière preview conservée.")
                _atomic_write_json(path, job)
            self._jobs[job["id"]] = job

    # --------------------------------------------------------------- etapes
    @staticmethod
    def plan_stages(options: dict[str, Any]) -> list[str]:
        """Etapes reellement prevues pour ces options (ordre d'execution)."""
        stages = []
        for name in ("reference", "load", "body", "face", "hair", "outfit",
                     "materials", "rig", "finalize", "evaluation"):
            option = STAGE_OPTION.get(name)
            if option and not options.get(option, True):
                continue
            stages.append(name)
        return stages

    @staticmethod
    def weights_for(stages: list[str]) -> dict[str, float]:
        total = sum(STAGE_WEIGHTS.get(s, 5) for s in stages) or 1
        return {s: STAGE_WEIGHTS.get(s, 5) / total for s in stages}

    def _recompute_progress(self, job: dict[str, Any]) -> float:
        """Progression = etapes REELLEMENT terminees + avancement de l'etape courante.

        Une etape sautee est retiree du denominateur : sa part n'est jamais
        « offerte ». 100 % n'est atteint que par `_finish_ok`.
        """
        stages = [s for s in job.get("stages", [])
                  if s not in job.get("skipped_stages", [])]
        weights = self.weights_for(stages)
        done = 0.0
        for stage in job.get("completed_stages", []):
            done += weights.get(stage, 0.0)
        current = job.get("stage", "")
        if current and current not in job.get("completed_stages", []):
            done += weights.get(current, 0.0) * float(job.get("stage_progress") or 0.0)
        return max(0.0, min(0.99, done))

    # ---------------------------------------------------------------- public
    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(),
                          key=lambda j: j.get("created_at", 0), reverse=True)
        return [self.public(j) for j in jobs[:limit]]

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
        return self.public(job) if job else None

    def active(self) -> dict[str, Any] | None:
        with self._lock:
            for job in sorted(self._jobs.values(),
                              key=lambda j: j.get("created_at", 0), reverse=True):
                if job.get("status") in {"queued", "running", "paused"}:
                    return self.public(job)
        return None

    def public(self, job: dict[str, Any]) -> dict[str, Any]:
        """Vue API du job : tout ce dont Avatar Studio a besoin au reload."""
        job_id = job["id"]
        version = int(job.get("version") or 0)
        base = f"/api/avatar/jobs/{job_id}"
        live_dir = self.job_dir(job_id) / "live"
        renders = {}
        for view in LIVE_VIEWS:
            if (live_dir / f"{view}.png").is_file():
                renders[view] = f"{base}/live/{view.replace('_', '-')}?v={version}"
        stages = job.get("stages", [])
        weights = self.weights_for([s for s in stages
                                    if s not in job.get("skipped_stages", [])])
        return {
            "id": job_id,
            "status": job.get("status", ""),
            "stage": job.get("stage", ""),
            "stage_label": STAGE_LABELS.get(job.get("stage", ""), job.get("stage", "")),
            "stage_progress": round(float(job.get("stage_progress") or 0.0), 3),
            "operation": job.get("operation", ""),
            "progress": round(float(job.get("progress") or 0.0), 4),
            "version": version,
            "quality": job.get("quality", "balanced"),
            "options": job.get("options", {}),
            "reference_id": job.get("reference_id", ""),
            "conversation_id": job.get("conversation_id", ""),
            "created_at": job.get("created_at", 0),
            "started_at": job.get("started_at", 0),
            "finished_at": job.get("finished_at", 0),
            "error": job.get("error", ""),
            "stalled": bool(job.get("stalled")),
            "paused": job.get("status") == "paused",
            "stages": [{"id": s, "label": STAGE_LABELS.get(s, s),
                        "weight": round(weights.get(s, 0.0), 4),
                        "state": ("done" if s in job.get("completed_stages", [])
                                  else "skipped" if s in job.get("skipped_stages", [])
                                  else "active" if s == job.get("stage", "")
                                  else "pending"),
                        "progress": (round(float(job.get("stage_progress") or 0), 3)
                                     if s == job.get("stage", "") else
                                     1.0 if s in job.get("completed_stages", []) else 0.0)}
                       for s in stages],
            "completed_stages": job.get("completed_stages", []),
            "skipped_stages": job.get("skipped_stages", []),
            "latest_glb": (f"{base}/live/model?v={version}" if version else ""),
            "latest_renders": renders,
            "before": {
                "glb": (f"{base}/before/model" if (self.job_dir(job_id) / "before"
                                                   / "model.glb").is_file() else ""),
            },
            "final": {
                "glb": (f"{base}/final/model" if (self.job_dir(job_id) / "final"
                                                  / "avatar.glb").is_file() else ""),
                "renders": {v: f"{base}/final/{v.replace('_', '-')}"
                            for v in LIVE_VIEWS
                            if (self.job_dir(job_id) / "final" / f"{v}.png").is_file()},
            },
            "reference_url": (f"/api/avatar/references/{job.get('reference_id')}/image"
                              if job.get("reference_id") else ""),
            "revisions": job.get("revisions", []),
            "iterations": job.get("iterations", []),
            "evaluation": job.get("evaluation", {}),
            "vision": job.get("vision", {}),
            "features": job.get("features", {}),
            "retryable": bool(job.get("retryable")),
            "quality_gate": job.get("quality_gate", {}),
            "quality_ok": job.get("quality_ok"),
            "adjustments": job.get("adjustments", []),
            "timeline": job.get("timeline", [])[-120:],
            "warnings": job.get("warnings", [])[-20:],
            "accepted": bool(job.get("accepted")),
            "rejected": bool(job.get("rejected")),
            "revision_id": job.get("revision_id", ""),
            "polycount": job.get("polycount", 0),
            "heartbeat_at": job.get("heartbeat_at", 0),
        }

    # ----------------------------------------------------------------- start
    def start(self, reference_id: str, *, options: dict[str, Any] | None = None,
              quality: str = "balanced", conversation_id: str = "",
              title: str = "") -> dict[str, Any]:
        """Cree et lance un job live. Retourne immediatement (job en tache de fond)."""
        ref = self._core.avatar_ref.get(reference_id) if reference_id else None
        if reference_id and not ref:
            return {"ok": False, "error": f"Référence inconnue : {reference_id}"}
        master = self._core.avatar_ref.get_master_blend()
        if not master:
            return {"ok": False, "error": "Aucun avatar Blender maître "
                                          "(assets/blender/jarvis_avatar.blend)."}
        if not self._core.blender.available():
            return {"ok": False, "error": "Blender n'est pas détecté "
                                          "(Réglages → Atelier 3D)."}
        with self._lock:
            running = [j for j in self._jobs.values()
                       if j.get("status") in {"queued", "running", "paused"}]
        if running:
            return {"ok": False, "error": "Un job avatar est déjà en cours.",
                    "job_id": running[0]["id"]}

        opts = dict(options or {})
        opts.setdefault("modify_face", True)
        opts.setdefault("modify_hair", True)
        opts.setdefault("modify_outfit", True)
        opts.setdefault("modify_materials", True)
        opts.setdefault("modify_proportions", False)
        opts.setdefault("preserve_rig", True)
        opts.setdefault("preserve_identity", True)
        opts.setdefault("realism_level", "balanced")
        quality = quality if quality in QUALITIES else "balanced"

        job_id = new_id("avj")
        root = self.job_dir(job_id)
        for sub in ("reference", "before", "live", "revisions", "final"):
            (root / sub).mkdir(parents=True, exist_ok=True)

        stages = self.plan_stages(opts)
        job: dict[str, Any] = {
            "id": job_id,
            "title": title or "Refonte de l'avatar",
            "status": "queued",
            "stage": "reference",
            "stage_progress": 0.0,
            "operation": "Préparation du job",
            "progress": 0.0,
            "version": 0,
            "quality": quality,
            "options": opts,
            "stages": stages,
            "completed_stages": [],
            "skipped_stages": [],
            "reference_id": reference_id,
            "conversation_id": conversation_id,
            "master_blend": master,
            "created_at": time.time(),
            "started_at": 0.0,
            "finished_at": 0.0,
            "heartbeat_at": time.time(),
            "error": "",
            "stalled": False,
            "revisions": [],
            "iterations": [],
            "evaluation": {},
            "adjustments": [],
            "timeline": [],
            "warnings": [],
            "polycount": 0,
        }
        with self._lock:
            self._jobs[job_id] = job
        self._write_control(job_id, {"pause": False, "cancel": False, "adjustments": []})
        self._timeline(job, "Job créé", "start")
        self._save(job)
        self._emit("avatar.job.started", job, title=job["title"],
                   quality=quality, stages=self.public(job)["stages"])

        threading.Thread(target=self._run, args=(job_id,),
                         name=f"avatar-live-{job_id}", daemon=True).start()
        return {"ok": True, "job": self.public(job)}

    # ------------------------------------------------------------- controles
    def _control_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "live" / "control.json"

    def _read_control(self, job_id: str) -> dict[str, Any]:
        try:
            return json.loads(self._control_path(job_id).read_text(encoding="utf-8"))
        except Exception:
            return {"pause": False, "cancel": False, "adjustments": []}

    def _write_control(self, job_id: str, control: dict[str, Any]) -> None:
        path = self._control_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(control, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def pause(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
        if not job or job.get("status") != "running":
            return {"ok": False, "error": "Aucun job en cours à mettre en pause."}
        control = self._read_control(job_id)
        control["pause"] = True
        self._write_control(job_id, control)
        # Blender confirmera la pause a son prochain point sûr.
        self._timeline(job, "Pause demandée — effective à la fin de l'étape", "control")
        self._save(job)
        return {"ok": True, "job": self.public(job)}

    def resume(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
        if not job:
            return {"ok": False, "error": "Job inconnu."}
        control = self._read_control(job_id)
        control["pause"] = False
        self._write_control(job_id, control)
        self._timeline(job, "Reprise demandée", "control")
        self._save(job)
        return {"ok": True, "job": self.public(job)}

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
        if not job:
            return {"ok": False, "error": "Job inconnu."}
        if job.get("status") not in {"queued", "running", "paused"}:
            return {"ok": False, "error": "Ce job n'est plus en cours."}
        control = self._read_control(job_id)
        control["cancel"] = True
        control["pause"] = False
        self._write_control(job_id, control)
        job["cancel_requested"] = True
        self._timeline(job, "Annulation demandée", "control")
        self._save(job)
        return {"ok": True, "job": self.public(job)}

    def adjust(self, job_id: str, text: str) -> dict[str, Any]:
        """Consigne utilisateur appliquee au prochain point sûr (jamais pendant un export)."""
        with self._lock:
            job = self._jobs.get(job_id)
        if not job or job.get("status") not in {"running", "paused", "queued"}:
            return {"ok": False, "error": "Aucun job en cours."}
        text = str(text or "").strip()
        if not text:
            return {"ok": False, "error": "Consigne vide."}
        entry = {"id": new_id("adj"), "text": text[:400], "ts": time.time(),
                 "applied": False}
        control = self._read_control(job_id)
        control.setdefault("adjustments", []).append(entry)
        self._write_control(job_id, control)
        job.setdefault("adjustments", []).append(entry)
        self._timeline(job, f"Consigne ajoutée : {text[:120]}", "adjust")
        self._save(job)
        self._emit("avatar.adjustment.queued", job, text=entry["text"],
                   adjustment_id=entry["id"])
        return {"ok": True, "adjustment": entry}

    # ------------------------------------------------------------------ run
    def _run(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
        if not job:
            return
        root = self.job_dir(job_id)
        try:
            self._stage_reference(job)
            self._snapshot_before(job)
            self._run_blender(job)
        except AnalysisFailed as exc:
            self._finish_analysis_failed(job, str(exc))
            return
        except Exception as exc:
            _log(f"job {job_id} crashed: {exc}")
            self._finish_failed(job, str(exc))
            return
        if job.get("status") in {"failed", "cancelled"}:
            return
        try:
            self._stage_evaluation(job)
        except Exception as exc:
            job.setdefault("warnings", []).append(f"Évaluation indisponible : {exc}")
        self._finish_ok(job)
        _ = root

    # -- etape hote 1 : reference ----------------------------------------
    def _stage_reference(self, job: dict[str, Any]) -> None:
        job["status"] = "running"
        job["started_at"] = time.time()
        self._set_stage(job, "reference", 0.1, "Analyse de l'image de référence")
        ref_id = job.get("reference_id") or ""
        if not ref_id:
            self._skip_stage(job, "reference", "Aucune référence fournie")
            return
        ref = self._core.avatar_ref.get(ref_id) or {}
        source = ref.get("source_path") or ""
        if source and Path(source).is_file():
            try:
                dest = self.job_dir(job["id"]) / "reference" / Path(source).name
                shutil.copy2(source, dest)
                job["reference_file"] = str(dest)
            except Exception as exc:
                job.setdefault("warnings", []).append(f"Copie référence : {exc}")
        self._set_stage(job, "reference", 0.5, "Analyse visuelle de la référence")
        features = ref.get("extracted_features") or {}
        if not features.get("analysis_success"):
            features = self._core.avatar_ref.analyze(ref_id) or {}
        job["features"] = features
        job["vision"] = {
            "provider": features.get("vision_provider", ""),
            "model": features.get("vision_model", ""),
            "image_dimensions": features.get("image_dimensions", [0, 0]),
            "success": bool(features.get("analysis_success")),
        }
        # Exigence 4 : PAS de fallback silencieux. Sans analyse visuelle
        # reelle, on ne fabrique pas un avatar generique.
        if not features.get("analysis_success"):
            raise AnalysisFailed(
                features.get("analysis_error")
                or "Analyse de la référence indisponible.")
        self._set_stage(job, "reference", 1.0, "Référence analysée")
        self._complete_stage(job, "reference")

    # -- avant / apres ----------------------------------------------------
    def _snapshot_before(self, job: dict[str, Any]) -> None:
        """Copie l'avatar actif (GLB deja publie) comme etat « avant »."""
        before_dir = self.job_dir(job["id"]) / "before"
        candidates = [
            Path(__file__).resolve().parent.parent / "ui" / "assets" / "avatar"
            / "jarvis_avatar.glb",
        ]
        active = self._core.avatar_ref.active_revision() or {}
        for key in ("glb_path", "blend_path"):
            value = active.get(key) or ""
            if value.endswith(".glb") and Path(value).is_file():
                candidates.insert(0, Path(value))
        for src in candidates:
            if src.is_file():
                try:
                    shutil.copy2(src, before_dir / "model.glb")
                    self._timeline(job, "État « avant » capturé", "info")
                    return
                except Exception:
                    continue

    # -- etape Blender ----------------------------------------------------
    def _blender_settings_path(self, job: dict[str, Any]) -> Path:
        root = self.job_dir(job["id"])
        work_blend = root / "work.blend"
        if not work_blend.is_file():
            shutil.copy2(job["master_blend"], work_blend)
        gpu = False
        try:
            gpu = bool(self._core.blender.gpu_stats())
        except Exception:
            gpu = False
        settings = {
            "job_id": job["id"],
            "job_dir": str(root),
            "blend_in": str(work_blend),
            "reference_id": job.get("reference_id", ""),
            "reference_image": job.get("reference_file", ""),
            "features": job.get("features", {}),
            "options": job.get("options", {}),
            "quality": job.get("quality", "balanced"),
            "stages": job.get("stages", []),
            "final_engine": "cycles" if (gpu and job.get("quality") == "high")
                            else "eevee",
            "gpu_available": gpu,
        }
        path = root / "blender_settings.json"
        _atomic_write_json(path, settings)
        return path

    def _run_blender(self, job: dict[str, Any]) -> None:
        settings_path = self._blender_settings_path(job)
        events_path = self.job_dir(job["id"]) / "live" / "events.ndjson"
        events_path.write_text("", encoding="utf-8")

        # Blender lourd : on libère la VRAM occupée par les modèles Ollama
        # résidents (le plan est déjà produit, le spécialiste n'a plus besoin
        # d'être chargé pendant le rendu).
        try:
            freed = self._core.gpu.free_vram_for_blender()
            if freed.get("unloaded"):
                self._timeline(job, "VRAM libérée : " + ", ".join(freed["unloaded"]),
                               "info")
        except Exception as exc:
            _log(f"gpu manager indisponible: {exc}")

        cmd = [self._core.blender.executable(), "--background", "--factory-startup",
               "--disable-autoexec", "--python", str(SCRIPT_PATH), "--",
               str(settings_path)]
        log_path = self.job_dir(job["id"]) / "blender.log"
        _log(f"launching Blender for {job['id']}")
        try:
            logf = open(log_path, "w", encoding="utf-8", errors="replace")
        except OSError:
            logf = None
        env = dict(os.environ)
        env["JARVIS_AVATAR_JOB"] = str(settings_path)
        try:
            proc = subprocess.Popen(
                cmd, stdout=logf or subprocess.DEVNULL, stderr=subprocess.STDOUT,
                cwd=str(self.job_dir(job["id"])), env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as exc:
            if logf:
                logf.close()
            raise RuntimeError(f"Lancement de Blender impossible : {exc}")
        with self._lock:
            self._procs[job["id"]] = proc

        offset = 0
        job_deadline = time.time() + DEFAULT_JOB_TIMEOUT_S
        stage_started = time.time()
        last_stage = job.get("stage", "")
        blender_outcome = {"done": False, "failed": "", "cancelled": False}
        try:
            while True:
                offset = self._drain_events(job, events_path, offset, blender_outcome)
                if job.get("stage") != last_stage:
                    last_stage = job.get("stage")
                    stage_started = time.time()
                code = proc.poll()

                silent = time.time() - float(job.get("heartbeat_at") or 0)
                if silent > STALL_AFTER_S and not job.get("stalled") \
                        and job.get("status") == "running":
                    job["stalled"] = True
                    self._timeline(job, "Blender semble bloqué — aucun signal reçu",
                                   "warn")
                    self._save(job)
                    self._emit("avatar.job.stalled", job,
                               silent_for=round(silent, 1))
                elif silent <= STALL_AFTER_S and job.get("stalled"):
                    job["stalled"] = False
                    self._emit("avatar.job.progress", job)

                if code is not None:
                    # Vide la queue d'evenements restants apres la sortie.
                    time.sleep(0.2)
                    self._drain_events(job, events_path, offset, blender_outcome)
                    break
                if time.time() - stage_started > DEFAULT_STAGE_TIMEOUT_S:
                    self._terminate(proc)
                    raise RuntimeError(
                        f"Étape « {STAGE_LABELS.get(last_stage, last_stage)} » "
                        f"dépassée ({int(DEFAULT_STAGE_TIMEOUT_S)} s). "
                        "Dernière preview conservée.")
                if time.time() > job_deadline:
                    self._terminate(proc)
                    raise RuntimeError("Délai global du job dépassé. "
                                       "Dernière preview conservée.")
                time.sleep(0.25)
        finally:
            with self._lock:
                self._procs.pop(job["id"], None)
            if logf:
                logf.close()

        if blender_outcome["cancelled"] or job.get("cancel_requested"):
            self._finish_cancelled(job)
            return
        if blender_outcome["failed"]:
            self._finish_failed(job, blender_outcome["failed"])
            return
        if not blender_outcome["done"]:
            tail = ""
            try:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-900:]
            except Exception:
                pass
            self._finish_failed(
                job, f"Blender s'est arrêté (code {proc.returncode}) sans terminer "
                     f"le pipeline.\n{tail}".strip())

    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # -- lecture du flux NDJSON de Blender --------------------------------
    def _drain_events(self, job: dict[str, Any], path: Path, offset: int,
                      outcome: dict[str, Any]) -> int:
        if not path.is_file():
            return offset
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(offset)
                chunk = fh.read()
                new_offset = fh.tell()
        except Exception:
            return offset
        if not chunk:
            return offset
        # Une derniere ligne incomplete est relue au tour suivant.
        lines = chunk.split("\n")
        if not chunk.endswith("\n"):
            new_offset -= len(lines[-1].encode("utf-8"))
            lines = lines[:-1]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except Exception:
                continue
            self._handle_blender_event(job, event, outcome)
        self._save(job)
        return new_offset

    def _handle_blender_event(self, job: dict[str, Any], event: dict[str, Any],
                              outcome: dict[str, Any]) -> None:
        etype = str(event.get("type") or "")
        stage = str(event.get("stage") or "")
        message = str(event.get("message") or "")
        job["heartbeat_at"] = time.time()
        if job.get("stalled"):
            job["stalled"] = False

        if etype == "avatar.heartbeat":
            return

        if etype == "avatar.reference.measured":
            measured = event.get("measured") or {}
            job.setdefault("features", {}).update(
                {k: v for k, v in measured.items()
                 if not job.get("features", {}).get(k)})
            job["reference_measured"] = measured
            filled = event.get("filled") or []
            if filled:
                self._timeline(job, "Référence mesurée : " + ", ".join(filled), "info")
            return

        if etype == "avatar.blender.started":
            self._timeline(job, "Blender démarré", "start")
            return

        if etype == "avatar.stage.started":
            self._set_stage(job, stage, 0.0, message)
            return

        if etype == "avatar.stage.progress":
            self._set_stage(job, stage, float(event.get("stage_progress") or 0.0),
                            message, timeline=False)
            return

        if etype == "avatar.operation":
            job["operation"] = message
            self._timeline(job, message, "op")
            self._emit("avatar.operation", job, message=message)
            return

        if etype == "avatar.stage.completed":
            self._complete_stage(job, stage, message)
            return

        if etype == "avatar.stage.skipped":
            self._skip_stage(job, stage, message)
            return

        if etype == "avatar.preview.glb":
            version = int(event.get("version") or 0)
            job["version"] = version
            entry = {
                "version": version, "stage": stage,
                "label": str(event.get("label") or stage),
                "ts": event.get("ts", time.time()),
                "blend_revision": event.get("blend_revision", 0),
                "reference_id": job.get("reference_id", ""),
                "job_id": job["id"],
                "size": event.get("size", 0),
                "glb": f"/api/avatar/jobs/{job['id']}/revisions/{version}/model",
            }
            job.setdefault("revisions", []).append(entry)
            job["progress"] = self._recompute_progress(job)
            self._timeline(job, f"Preview v{version} · "
                                f"{STAGE_LABELS.get(stage, stage)}", "preview")
            self._save(job)
            self._emit("avatar.preview.glb", job, version=version,
                       label=entry["label"],
                       url=f"/api/avatar/jobs/{job['id']}/live/model?v={version}",
                       provenance=entry)
            return

        if etype == "avatar.preview.render":
            version = int(event.get("version") or job.get("version") or 0)
            views = event.get("views") or []
            urls = {v: f"/api/avatar/jobs/{job['id']}/live/"
                       f"{v.replace('_', '-')}?v={version}" for v in views}
            for entry in job.get("revisions", []):
                if entry.get("version") == version:
                    entry["renders"] = {
                        v: f"/api/avatar/jobs/{job['id']}/revisions/{version}/"
                           f"{v.replace('_', '-')}" for v in views}
            self._save(job)
            self._emit("avatar.preview.render", job, version=version,
                       views=views, urls=urls)
            return

        if etype == "avatar.warning":
            job.setdefault("warnings", []).append(message)
            self._timeline(job, message, "warn")
            self._emit("avatar.job.warning", job, message=message)
            return

        if etype == "avatar.control.paused":
            job["status"] = "paused"
            self._timeline(job, "Job en pause", "control")
            self._save(job)
            self._emit("avatar.job.paused", job)
            return

        if etype == "avatar.control.resumed":
            job["status"] = "running"
            self._timeline(job, "Job repris", "control")
            self._save(job)
            self._emit("avatar.job.resumed", job)
            return

        if etype == "avatar.control.cancelled":
            outcome["cancelled"] = True
            return

        if etype == "avatar.adjustment.applied":
            adj_id = event.get("adjustment_id", "")
            for adj in job.get("adjustments", []):
                if adj.get("id") == adj_id:
                    adj["applied"] = True
            self._timeline(job, f"Consigne appliquée : {message}", "adjust")
            self._emit("avatar.adjustment.applied", job, message=message,
                       adjustment_id=adj_id)
            return

        if etype == "avatar.blender.completed":
            outcome["done"] = True
            job["polycount"] = int(event.get("polycount") or 0)
            job["rigged"] = bool(event.get("rigged"))
            self._timeline(job, "Blender a terminé", "info")
            return

        if etype == "avatar.blender.cancelled":
            outcome["cancelled"] = True
            return

        if etype == "avatar.blender.failed":
            outcome["failed"] = message or "Blender a échoué."
            job["blender_traceback"] = str(event.get("traceback") or "")[-1500:]
            return

    # -- transitions d'etape ---------------------------------------------
    def _set_stage(self, job: dict[str, Any], stage: str, progress: float,
                   message: str = "", timeline: bool = True) -> None:
        previous = job.get("stage", "")
        job["stage"] = stage
        job["stage_progress"] = max(0.0, min(1.0, float(progress)))
        if message:
            job["operation"] = message
        job["progress"] = self._recompute_progress(job)
        if stage != previous:
            if timeline:
                self._timeline(job, STAGE_LABELS.get(stage, stage), "stage")
            self._save(job)
            self._emit("avatar.stage.started", job, message=message)
        self._emit("avatar.stage.progress", job, message=message,
                   stage_progress=job["stage_progress"])
        self._emit("avatar.job.progress", job, message=message)

    def _complete_stage(self, job: dict[str, Any], stage: str,
                        message: str = "") -> None:
        if stage and stage not in job.setdefault("completed_stages", []):
            job["completed_stages"].append(stage)
        job["stage_progress"] = 1.0
        job["progress"] = self._recompute_progress(job)
        self._timeline(job, f"{STAGE_LABELS.get(stage, stage)} — terminé", "done")
        self._save(job)
        self._emit("avatar.stage.completed", job, stage=stage, message=message)
        self._emit("avatar.job.progress", job)

    def _skip_stage(self, job: dict[str, Any], stage: str, reason: str = "") -> None:
        if stage and stage not in job.setdefault("skipped_stages", []):
            job["skipped_stages"].append(stage)
        job["progress"] = self._recompute_progress(job)
        self._timeline(job, f"{STAGE_LABELS.get(stage, stage)} — ignoré"
                            + (f" ({reason})" if reason else ""), "skip")
        self._save(job)
        self._emit("avatar.stage.skipped", job, stage=stage, message=reason)

    # -- etape hote finale : evaluation -----------------------------------
    def _stage_evaluation(self, job: dict[str, Any]) -> None:
        if "evaluation" not in job.get("stages", []):
            return
        self._set_stage(job, "evaluation", 0.2, "Comparaison avec la référence")
        final_front = self.job_dir(job["id"]) / "final" / "front.png"
        live_front = self.job_dir(job["id"]) / "live" / "front.png"
        preview = final_front if final_front.is_file() else live_front
        ref_id = job.get("reference_id") or ""
        if not preview.is_file() or not ref_id:
            self._skip_stage(job, "evaluation",
                             "Aucun rendu de face ou aucune référence à comparer")
            return
        self._emit("avatar.iteration.started", job, iteration=len(job.get("iterations", [])) + 1)
        evaluation = self._core.avatar_ref.evaluate_similarity(str(preview), ref_id) or {}
        job["evaluation"] = evaluation
        iteration = {
            "index": len(job.get("iterations", [])) + 1,
            "version": job.get("version", 0),
            "scores": evaluation,
            "ts": time.time(),
        }
        job.setdefault("iterations", []).append(iteration)
        if not evaluation.get("available"):
            # Exigence 5/39 : aucun score fabrique. L'UI affichera « N/A ».
            self._timeline(job, "Évaluation indisponible — "
                                + str(evaluation.get("reason", ""))[:120], "warn")
            self._set_stage(job, "evaluation", 1.0, "Évaluation indisponible")
            self._complete_stage(job, "evaluation")
            self._emit("avatar.evaluation.completed", job, evaluation=evaluation,
                       iterations=job["iterations"])
            return
        score = evaluation.get("overall_score")
        for entry in job.get("revisions", []):
            if entry.get("version") == job.get("version"):
                entry["score"] = score
        self._timeline(job, f"Score global {score}/100" if score is not None
                       else "Score global indisponible", "score")
        for issue in (evaluation.get("issues") or [])[:6]:
            self._timeline(job, "À améliorer : " + str(issue)[:150], "warn")
        self._set_stage(job, "evaluation", 1.0, "Évaluation terminée")
        self._complete_stage(job, "evaluation")
        self._emit("avatar.iteration.completed", job, iteration=iteration)
        self._emit("avatar.evaluation.completed", job, evaluation=job["evaluation"],
                   iterations=job["iterations"])

    @staticmethod
    def _quality_gate(evaluation: dict[str, Any]) -> dict[str, Any]:
        """Confronte les scores RÉELS aux seuils. Sans évaluation : indécidable."""
        if not evaluation.get("available"):
            return {"decided": False, "passed": False, "failures": [],
                    "reason": evaluation.get("reason") or "Évaluation indisponible."}
        failures = []
        for key, minimum in QUALITY_THRESHOLDS.items():
            value = evaluation.get(key)
            if not isinstance(value, int):
                continue
            if value < minimum:
                failures.append({"metric": key, "score": value, "required": minimum})
        return {"decided": True, "passed": not failures, "failures": failures,
                "reason": "", "thresholds": dict(QUALITY_THRESHOLDS)}

    # -- fins de job -------------------------------------------------------
    def _finish_ok(self, job: dict[str, Any]) -> None:
        final_glb = self.job_dir(job["id"]) / "final" / "avatar.glb"
        if not final_glb.is_file():
            self._finish_failed(job, "Le GLB final est absent : job non validé.")
            return
        if final_glb.stat().st_size < 128:
            self._finish_failed(job, "Le GLB final est illisible : job non validé.")
            return
        gate = self._quality_gate(job.get("evaluation") or {})
        job["quality_gate"] = gate
        job["progress"] = 1.0
        job["finished_at"] = time.time()
        if gate["decided"] and not gate["passed"]:
            # Exigence 22/40 : pas de « Terminé » complaisant.
            job["status"] = "completed"
            job["quality_ok"] = False
            job["stage"] = "completed"
            job["operation"] = "Qualité insuffisante — candidate à revoir"
            worst = ", ".join(
                f"{f['metric'].replace('_score', '')} {f['score']}/{f['required']}"
                for f in gate["failures"][:4])
            self._timeline(job, f"Qualité sous les seuils ({worst}) — "
                                "résultat NON proposé comme abouti", "warn")
        else:
            job["status"] = "completed"
            job["quality_ok"] = True
            job["stage"] = "completed"
            job["operation"] = "Avatar prêt"
            self._timeline(job, "Job terminé — avatar prêt à être accepté", "done")
        self._save(job)
        self._emit("avatar.job.completed", job,
                   quality_gate=gate, quality_ok=job["quality_ok"],
                   final_glb=f"/api/avatar/jobs/{job['id']}/final/model",
                   evaluation=job.get("evaluation", {}),
                   polycount=job.get("polycount", 0))

    def _finish_analysis_failed(self, job: dict[str, Any], reason: str) -> None:
        """Génération suspendue : aucun avatar n'est fabriqué au hasard."""
        job["status"] = "analysis_failed"
        job["error"] = str(reason)[:2000]
        job["finished_at"] = time.time()
        job["operation"] = "Analyse de la référence indisponible"
        job["retryable"] = True
        self._timeline(job, "Analyse de la référence indisponible — "
                            "génération suspendue", "error")
        self._save(job)
        self._emit("avatar.job.analysis_failed", job, error=job["error"],
                   retryable=True)

    def _finish_failed(self, job: dict[str, Any], error: str) -> None:
        job["status"] = "failed"
        job["error"] = str(error)[:2000]
        job["finished_at"] = time.time()
        job["operation"] = "Échec"
        self._timeline(job, f"Échec : {job['error'][:160]}", "error")
        self._save(job)
        self._emit("avatar.job.failed", job, error=job["error"])

    def _finish_cancelled(self, job: dict[str, Any]) -> None:
        job["status"] = "cancelled"
        job["finished_at"] = time.time()
        job["operation"] = "Annulé"
        self._timeline(job, "Job annulé — avatar actif inchangé", "control")
        self._save(job)
        self._emit("avatar.job.cancelled", job)

    # -------------------------------------------------------- accept/reject
    def accept(self, job_id: str) -> dict[str, Any]:
        """Adopte le resultat : cree une revision active depuis les fichiers finaux."""
        with self._lock:
            job = self._jobs.get(job_id)
        if not job:
            return {"ok": False, "error": "Job inconnu."}
        if job.get("status") != "completed":
            return {"ok": False, "error": "Ce job n'est pas terminé."}
        final = self.job_dir(job_id) / "final"
        glb = final / "avatar.glb"
        blend = final / "avatar.blend"
        if not glb.is_file():
            return {"ok": False, "error": "Aucun GLB final à adopter."}

        rev = self._core.avatar_ref.create_revision(
            job.get("reference_id", ""),
            blend_path=str(blend) if blend.is_file() else "",
            conversation_id=job.get("conversation_id", ""))
        fields: dict[str, Any] = {"evaluation": job.get("evaluation", {})}
        for view, key in (("front", "preview_front"), ("side", "preview_side"),
                          ("three_quarter", "preview_34"), ("full_body", "preview_full")):
            path = final / f"{view}.png"
            if path.is_file():
                fields[key] = str(path)
        self._core.avatar_ref.update_revision(rev["id"], **fields)
        self._core.avatar_ref.accept_revision(rev["id"])

        # L'avatar affiche par le Command Center n'est remplace qu'ici.
        published = ""
        try:
            target = (Path(__file__).resolve().parent.parent / "ui" / "assets"
                      / "avatar" / "jarvis_avatar.glb")
            target.parent.mkdir(parents=True, exist_ok=True)
            backup = target.with_suffix(".previous.glb")
            if target.is_file():
                shutil.copy2(target, backup)
            tmp = target.with_suffix(".tmp.glb")
            shutil.copy2(glb, tmp)
            os.replace(tmp, target)
            published = str(target)
        except Exception as exc:
            job.setdefault("warnings", []).append(f"Publication de l'avatar : {exc}")

        job["accepted"] = True
        job["rejected"] = False
        job["revision_id"] = rev["id"]
        self._timeline(job, "Révision adoptée comme avatar actif", "done")
        self._save(job)
        self._emit("avatar.job.accepted", job, revision_id=rev["id"],
                   published=published)
        try:
            self._core.events.emit("avatar.revision_activated",
                                   {"revision_id": rev["id"], "job_id": job_id})
        except Exception:
            pass
        return {"ok": True, "revision_id": rev["id"], "job": self.public(job)}

    def reject(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
        if not job:
            return {"ok": False, "error": "Job inconnu."}
        job["rejected"] = True
        job["accepted"] = False
        self._timeline(job, "Résultat rejeté — avatar précédent conservé", "control")
        self._save(job)
        self._emit("avatar.job.rejected", job)
        return {"ok": True, "job": self.public(job)}

    # ---------------------------------------------------------------- assets
    def asset(self, job_id: str, kind: str, name: str = "") -> Path | None:
        """Resout un chemin d'asset servi par l'API. None si absent."""
        root = self.job_dir(job_id)
        if not root.is_dir():
            return None
        view = name.replace("-", "_")
        if kind == "live":
            path = root / "live" / ("current.glb" if view == "model" else f"{view}.png")
        elif kind == "before":
            path = root / "before" / "model.glb"
        elif kind == "final":
            path = root / "final" / ("avatar.glb" if view == "model" else f"{view}.png")
        elif kind == "revision":
            rev, _, sub = name.partition("/")
            sub_view = (sub or "model").replace("-", "_")
            path = (root / "revisions" / rev
                    / ("model.glb" if sub_view == "model" else f"{sub_view}.png"))
        else:
            return None
        return path if path.is_file() else None

    def shutdown(self) -> None:
        with self._lock:
            procs = list(self._procs.values())
        for proc in procs:
            self._terminate(proc)
