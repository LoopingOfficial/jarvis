"""Atelier 3D Blender de JARVIS — moteur local, agentique et verifie.

Detection reelle de Blender, execution headless de `blender_scripts/job.py`,
progression temps reel publiee sur le bus SSE, fichiers reels ecrits sous
`data/generated/3d/` et metadonnees JSON verifiees.

Rien n'est simule : sans Blender detecte, `available()` renvoie False et les
outils le disent. Un job dont le GLB n'est pas relisible est un job en echec.

Chaine d'un job :
    submit() -> queued -> starting -> running (geometry / materials / rig /
                animation / optimize / exporting / preview) -> completed | failed
                                                             | cancelled

Continuite de conversation : chaque conversation possede un PROJET 3D
(`blender_projects`) qui porte le .blend courant. Un « rends-la plus fine »
rouvre ce .blend au lieu de repartir de zero.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .db import dumps, loads, new_id

BLENDER_ROOT = DATA_DIR / "generated" / "3d"
PROJECTS_DIR = BLENDER_ROOT / "projects"
MODELS_DIR = BLENDER_ROOT / "models"
RENDERS_DIR = BLENDER_ROOT / "renders"
PREVIEWS_DIR = BLENDER_ROOT / "previews"
TEXTURES_DIR = BLENDER_ROOT / "textures"
EXPORTS_DIR = BLENDER_ROOT / "exports"
TEMP_DIR = BLENDER_ROOT / "temp"

SCRIPT_DIR = Path(__file__).resolve().parent / "blender_scripts"
JOB_SCRIPT = "job.py"

# Etapes affichees dans le loader de l'UI.
STAGE_LABELS = {
    "queued": "Preparation",
    "starting": "Demarrage de Blender",
    "preparing": "Preparation",
    "running": "Construction 3D",
    "geometry": "Creation de la geometrie",
    "materials": "Materiaux",
    "textures": "Textures",
    "rig": "Rig",
    "animation": "Animation",
    "optimize": "Optimisation",
    "rendering": "Rendu",
    "exporting": "Export",
    "preview": "Previsualisation",
    "finalizing": "Finalisation",
    "completed": "Modele pret",
    "failed": "Echec",
    "cancelled": "Annule",
}

# Etape -> evenement de fin publie une seule fois quand elle est depassee.
STAGE_EVENTS = {
    "geometry": "blender.geometry.completed",
    "materials": "blender.materials.completed",
    "textures": "blender.textures.completed",
    "rig": "blender.rig.completed",
    "animation": "blender.animation.completed",
    "optimize": "blender.optimize.completed",
    "exporting": "blender.export.completed",
    "preview": "blender.preview.ready",
}

STAGE_ORDER = ["queued", "starting", "preparing", "geometry", "materials", "textures",
               "rig", "animation", "optimize", "rendering", "exporting", "preview",
               "finalizing", "completed"]

CONTENT_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
    ".obj": "text/plain", ".mtl": "text/plain", ".stl": "model/stl",
    ".fbx": "application/octet-stream", ".blend": "application/octet-stream",
    ".json": "application/json", ".txt": "text/plain",
}

OUTPUT_KINDS = {".glb": "model", ".gltf": "model", ".fbx": "model", ".obj": "model",
                ".stl": "model", ".mtl": "model", ".blend": "blend", ".png": "image",
                ".jpg": "image", ".jpeg": "image"}


def _log(message: str) -> None:
    """Trace de diagnostic. Une console cp1252 ne doit jamais casser un job."""
    line = f"[blender] {message}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)
    except Exception:
        pass


class BlenderNotInstalled(Exception):
    pass


class BlenderJobError(Exception):
    pass


def _ensure_dirs() -> None:
    for d in (BLENDER_ROOT, PROJECTS_DIR, MODELS_DIR, RENDERS_DIR, PREVIEWS_DIR,
              TEXTURES_DIR, EXPORTS_DIR, TEMP_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def _parse_version_line(line: str) -> str:
    m = re.search(r"Blender\s+(\d+(?:\.\d+)?(?:\.\d+)?)", line or "")
    return m.group(1) if m else ""


def _version_tuple(version: str) -> tuple:
    parts = []
    for p in str(version or "").split(".")[:3]:
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


class BlenderManager:
    """Detection de Blender, projets 3D persistants et execution des jobs."""

    def __init__(self, core) -> None:
        self._core = core
        self._lock = threading.RLock()
        self._detection: dict[str, Any] = {"installed": False}
        self._detection_ts = 0.0
        self._probes: dict[str, dict[str, Any]] = {}
        self._procs: dict[str, subprocess.Popen] = {}
        self._cancelled: set[str] = set()
        _ensure_dirs()

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------
    def _candidate_executables(self) -> list[Path]:
        out: list[Path] = []
        configured = str(self._core.settings.get("blender", "executable_path", "") or "").strip()
        for raw in (configured, os.getenv("BLENDER_EXECUTABLE", "").strip()):
            if raw:
                p = Path(raw).expanduser()
                if p.is_file():
                    out.append(p)
        try:
            found = shutil.which("blender")
            if found:
                out.append(Path(found))
        except Exception:
            pass
        if os.name == "nt":
            roots = [
                Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Blender Foundation",
                Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Blender Foundation",
                Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Programs" / "Blender Foundation",
                Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Steam" / "steamapps" / "common",
            ]
            for root in roots:
                for pattern in ("Blender*/blender.exe", "Blender*/*/blender.exe"):
                    try:
                        out.extend(root.glob(pattern))
                    except Exception:
                        pass
        else:
            for base in (Path("/Applications"), Path.home() / "Applications"):
                try:
                    out.extend(base.glob("Blender.app/Contents/MacOS/Blender"))
                except Exception:
                    pass
        seen: set[str] = set()
        uniq: list[Path] = []
        for p in out:
            try:
                key = str(p.resolve()).lower()
            except OSError:
                continue
            if key in seen:
                continue
            seen.add(key)
            uniq.append(p)
        return uniq

    def _blender_version(self, exe: Path) -> str:
        try:
            proc = subprocess.run(
                [str(exe), "--version"], capture_output=True, text=True, timeout=90,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            return ""
        for stream in ((proc.stdout or ""), (proc.stderr or "")):
            for line in stream.splitlines():
                version = _parse_version_line(line)
                if version:
                    return version
        return ""

    def _probe_env(self, exe: str | Path, force: bool = False) -> dict[str, Any]:
        """Python, moteurs et GPU REELS, en une execution headless (cache 5 min)."""
        exe = Path(exe)
        key = str(exe).lower()
        with self._lock:
            cached = self._probes.get(key)
            if cached and not force and time.time() - cached.get("ts", 0) < 300:
                return cached
        result: dict[str, Any] = {"python": "", "gpu": None, "env": {}, "ok": False,
                                  "ts": time.time(), "error": ""}
        script = SCRIPT_DIR / "probe_env.py"
        if not script.is_file():
            result["error"] = "probe_env.py introuvable"
            with self._lock:
                self._probes[key] = result
            return result
        try:
            proc = subprocess.run(
                [str(exe), "--background", "--factory-startup", "--disable-autoexec",
                 "--python", str(script)],
                capture_output=True, text=True, timeout=180,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as exc:
            result["error"] = str(exc)[:200]
            _log(f"probe env KO: {exc}")
            with self._lock:
                self._probes[key] = result
            return result
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            for prefix, field in (("JARVIS_PY ", "python"), ("JARVIS_GPU ", "gpu"),
                                  ("JARVIS_ENV ", "env")):
                if not line.startswith(prefix):
                    continue
                payload = line[len(prefix):].strip()
                if field == "python":
                    result["python"] = payload
                else:
                    try:
                        result[field] = json.loads(payload)
                    except Exception:
                        pass
        result["ok"] = bool(result["python"])
        if not result["ok"]:
            result["error"] = (proc.stderr or proc.stdout or "")[-400:]
        with self._lock:
            self._probes[key] = result
        return result

    def detect(self, force: bool = False) -> dict[str, Any]:
        """Installations Blender reelles, par version decroissante."""
        now = time.time()
        if not force and self._detection.get("installed") and now - self._detection_ts < 60:
            return self._detection

        versions: list[dict[str, Any]] = []
        for exe in self._candidate_executables():
            ver = self._blender_version(exe)
            if not ver:
                continue
            versions.append({"version": ver, "executable_path": str(exe),
                             "name": exe.parent.name or exe.stem})
        versions.sort(key=lambda v: _version_tuple(v["version"]), reverse=True)

        preferred = str(self._core.settings.get("blender", "preferred_version", "") or "").strip()
        chosen = None
        if preferred:
            chosen = next((v for v in versions if str(v["version"]).startswith(preferred)), None)
        chosen = chosen or (versions[0] if versions else None)

        if chosen:
            info = self._probe_env(chosen["executable_path"], force=force)
            env = info.get("env") or {}
            self._detection = {
                "installed": True,
                "version": chosen["version"],
                "executable_path": chosen["executable_path"],
                "python_version": info.get("python", ""),
                "background_mode": True,
                "render_engines": self._engines(env),
                "gpu_support": bool((info.get("gpu") or {}).get("available")),
                "gpu": info.get("gpu") or {"available": False},
                "exporters": {"gltf": bool(env.get("gltf_exporter", True)),
                              "fbx": bool(env.get("fbx_exporter", True))},
                "preferred_version": preferred,
                "versions": versions,
                "probe_error": info.get("error", ""),
                "probed_at": info.get("ts", now),
            }
        else:
            self._detection = {"installed": False, "versions": [], "render_engines": [],
                               "gpu_support": False, "preferred_version": preferred}
        self._detection_ts = now
        return self._detection

    @staticmethod
    def _engines(env: dict[str, Any]) -> list[str]:
        engines = []
        if env.get("eevee"):
            engines.append("EEVEE")
        if env.get("cycles"):
            engines.append("Cycles")
        if not engines and env.get("engines"):
            engines = [e for e in env["engines"] if e.startswith("BLENDER") or e == "CYCLES"]
        return engines or ["EEVEE", "Cycles"]

    def available(self) -> bool:
        return bool(self.detect().get("installed"))

    def executable(self) -> str:
        det = self.detect()
        if not det.get("installed"):
            raise BlenderNotInstalled(
                "Blender n'est pas installe ou n'est pas detectable. Installe-le depuis "
                "blender.org, ou renseigne le chemin dans Settings -> Atelier 3D "
                "(ou la variable BLENDER_EXECUTABLE).")
        return det["executable_path"]

    def gpu_stats(self) -> dict[str, Any] | None:
        det = self.detect()
        return det.get("gpu") if det.get("installed") else None

    # ------------------------------------------------------------------
    # Test explicite (bouton Tester)
    # ------------------------------------------------------------------
    def test(self) -> dict[str, Any]:
        """Lance REELLEMENT Blender et verifie qu'il repond."""
        started = time.time()
        det = self.detect(force=True)
        if not det.get("installed"):
            result = {"ok": False, "error": "Blender introuvable sur ce PC.",
                      "duration_ms": int((time.time() - started) * 1000)}
        else:
            probe = self._probe_env(det["executable_path"], force=True)
            result = {
                "ok": bool(probe.get("ok")),
                "version": det.get("version", ""),
                "executable_path": det.get("executable_path", ""),
                "python_version": probe.get("python", ""),
                "render_engines": det.get("render_engines", []),
                "gpu": probe.get("gpu") or {"available": False},
                "error": "" if probe.get("ok") else (probe.get("error")
                                                     or "Blender n'a pas repondu."),
                "duration_ms": int((time.time() - started) * 1000),
            }
        self._core.db.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES('blender_last_test', ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (dumps({**result, "ts": time.time()}), time.time()))
        self._core.events.emit("connector.connected" if result["ok"] else "connector.failed",
                               {"connector_id": "blender", "name": "Blender",
                                "detail": result.get("error", "")})
        return result

    def last_test(self) -> dict[str, Any]:
        row = self._core.db.one("SELECT value FROM settings WHERE key='blender_last_test'")
        return loads(row["value"], {}) if row else {}

    # ------------------------------------------------------------------
    # Etat
    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        det = self.detect()
        section = self._core.settings.section("blender")
        jobs = self.list(limit=200)
        running = [j for j in jobs if j["status"] in {"queued", "running"}]
        return {
            "available": bool(det.get("installed")),
            "installed": bool(det.get("installed")),
            "version": det.get("version", ""),
            "executable_path": det.get("executable_path", ""),
            "python_version": det.get("python_version", ""),
            "background_mode": bool(det.get("background_mode", False)),
            "render_engines": det.get("render_engines", []),
            "gpu_support": bool(det.get("gpu_support", False)),
            "gpu": det.get("gpu") or {"available": False},
            "exporters": det.get("exporters", {}),
            "versions": det.get("versions", []),
            "settings": section,
            "last_test": self.last_test(),
            "dirs": {"root": str(BLENDER_ROOT), "projects": str(PROJECTS_DIR),
                     "renders": str(RENDERS_DIR), "exports": str(EXPORTS_DIR),
                     "previews": str(PREVIEWS_DIR), "textures": str(TEXTURES_DIR)},
            "jobs": {"total": len(jobs), "running": len(running)},
            "projects": len(self._core.db.query("SELECT id FROM blender_projects")),
            "hint": ("" if det.get("installed") else
                     "Blender introuvable : installe-le depuis blender.org, puis "
                     "renseigne son chemin dans Settings -> Atelier 3D."),
        }

    # ------------------------------------------------------------------
    # Projets 3D (continuite de conversation)
    # ------------------------------------------------------------------
    def project(self, project_id: str = "", conversation_id: str = "") -> dict[str, Any] | None:
        if project_id:
            row = self._core.db.one("SELECT * FROM blender_projects WHERE id=?", (project_id,))
        elif conversation_id:
            row = self._core.db.one(
                "SELECT * FROM blender_projects WHERE conversation_id=? "
                "ORDER BY updated_at DESC LIMIT 1", (conversation_id,))
        else:
            row = self._core.db.one(
                "SELECT * FROM blender_projects ORDER BY updated_at DESC LIMIT 1")
        if not row:
            return None
        project = {k: row[k] for k in row.keys()}
        project["meta"] = loads(project.get("meta"), {}) or {}
        project["history"] = loads(project.get("history"), []) or []
        project["has_blend"] = bool(project.get("blend_path")
                                    and Path(project["blend_path"]).is_file())
        return project

    def current(self, conversation_id: str = "") -> dict[str, Any] | None:
        """Contexte 3D courant : projet, modele, dernier export/rendu/apercu."""
        project = self.project(conversation_id=conversation_id)
        if not project:
            return None
        job = self.get(project.get("last_job_id") or "")
        return {
            "current_blender_project": project["id"],
            "name": project.get("name", ""),
            "prompt": project.get("prompt", ""),
            "current_model": project.get("blend_path", ""),
            "has_blend": project["has_blend"],
            "last_export": project.get("last_export", ""),
            "last_render": project.get("last_render", ""),
            "last_preview": project.get("last_preview", ""),
            "last_job_id": project.get("last_job_id", ""),
            "history": project.get("history", [])[-12:],
            "meta": project.get("meta", {}),
            "glb_url": (job or {}).get("glb_url", ""),
            "preview_url": (job or {}).get("preview_url", ""),
        }

    def create_project(self, conversation_id: str = "", name: str = "",
                       prompt: str = "") -> dict[str, Any]:
        pid = new_id("p3d")
        now = time.time()
        self._core.db.execute(
            "INSERT INTO blender_projects(id, conversation_id, name, prompt, blend_path, "
            "last_job_id, last_export, last_render, last_preview, meta, history, "
            "created_at, updated_at) VALUES(?,?,?,?,'','','','','','{}','[]',?,?)",
            (pid, conversation_id, (name or prompt or "Projet 3D")[:120],
             prompt[:2000], now, now))
        return self.project(pid)

    def _update_project(self, project_id: str, job: dict[str, Any]) -> None:
        project = self.project(project_id)
        if not project:
            return
        outputs = job.get("outputs") or []
        directory = Path(job.get("output_dir") or "")

        def _pick(names: tuple[str, ...]) -> str:
            for out in outputs:
                if str(out.get("name", "")).lower() in names:
                    return str(directory / out["name"])
            return ""

        blend = _pick(("model.blend",)) or project.get("blend_path", "")
        export = _pick(("model.glb", "model.gltf", "model.fbx")) or project.get("last_export", "")
        render = _pick(("render.png",)) or project.get("last_render", "")
        preview = _pick(("preview.png",)) or project.get("last_preview", "")
        history = list(project.get("history") or [])
        history.append({"job_id": job["id"], "action": job.get("action", ""),
                        "title": job.get("title", ""), "ts": time.time()})
        meta = dict(project.get("meta") or {})
        meta.update({k: v for k, v in (job.get("meta") or {}).items()
                     if k in {"kind", "polycount", "materials", "animations",
                              "dimensions", "rig", "blueprint"}})
        self._core.db.execute(
            "UPDATE blender_projects SET blend_path=?, last_job_id=?, last_export=?, "
            "last_render=?, last_preview=?, meta=?, history=?, updated_at=? WHERE id=?",
            (blend, job["id"], export, render, preview, dumps(meta),
             dumps(history[-60:]), time.time(), project_id))

    def list_projects(self, conversation_id: str = "", limit: int = 30) -> list[dict[str, Any]]:
        if conversation_id:
            rows = self._core.db.query(
                "SELECT id FROM blender_projects WHERE conversation_id=? "
                "ORDER BY updated_at DESC LIMIT ?", (conversation_id, limit))
        else:
            rows = self._core.db.query(
                "SELECT id FROM blender_projects ORDER BY updated_at DESC LIMIT ?", (limit,))
        return [p for p in (self.project(r["id"]) for r in rows) if p]

    # ------------------------------------------------------------------
    # Jobs — persistance
    # ------------------------------------------------------------------
    def _save(self, job: dict[str, Any]) -> None:
        self._core.db.execute(
            "INSERT INTO blender_jobs(id, conversation_id, message_id, project_id, tool, "
            "action, title, status, stage, progress, output_dir, outputs, meta, error, "
            "settings, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, stage=excluded.stage, "
            "progress=excluded.progress, output_dir=excluded.output_dir, "
            "outputs=excluded.outputs, meta=excluded.meta, error=excluded.error, "
            "message_id=excluded.message_id, project_id=excluded.project_id, "
            "updated_at=excluded.updated_at",
            (job["id"], job.get("conversation_id", ""), job.get("message_id", ""),
             job.get("project_id", ""), job.get("tool", ""), job.get("action", ""),
             job.get("title", ""), job.get("status", "queued"),
             job.get("stage", "queued"), float(job.get("progress") or 0),
             job.get("output_dir", ""), dumps(job.get("outputs") or []),
             dumps(job.get("meta") or {}), job.get("error", ""),
             dumps(job.get("settings") or {}), job.get("created_at", time.time()),
             time.time()))

    def get(self, job_id: str) -> dict[str, Any] | None:
        if not job_id:
            return None
        row = self._core.db.one("SELECT * FROM blender_jobs WHERE id=?", (job_id,))
        if not row:
            return None
        job = {k: row[k] for k in row.keys()}
        job["outputs"] = loads(job.get("outputs"), []) or []
        job["meta"] = loads(job.get("meta"), {}) or {}
        job["settings"] = self._public_settings(loads(job.get("settings"), {}) or {})
        job["stage_label"] = STAGE_LABELS.get(job.get("stage", ""), "")
        job["preview_url"] = self._file_url(job, ("preview.png", "render.png"))
        job["render_url"] = self._file_url(job, ("render.png",))
        job["glb_url"] = self._file_url(job, ("model.glb",))
        job["files"] = [{"name": o.get("name", ""), "kind": o.get("kind", "file"),
                         "url": f"/api/blender/jobs/{job['id']}/files/{o.get('name', '')}"}
                        for o in job["outputs"]]
        return job

    @staticmethod
    def _public_settings(settings: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in settings.items() if not k.startswith("__")}

    def _file_url(self, job: dict[str, Any], names: tuple[str, ...]) -> str:
        for out in job.get("outputs", []):
            if str(out.get("name", "")).lower() in names:
                return f"/api/blender/jobs/{job['id']}/files/{out['name']}"
        return ""

    def list(self, conversation_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        if conversation_id:
            rows = self._core.db.query(
                "SELECT id FROM blender_jobs WHERE conversation_id=? "
                "ORDER BY created_at DESC LIMIT ?", (conversation_id, limit))
        else:
            rows = self._core.db.query(
                "SELECT id FROM blender_jobs ORDER BY created_at DESC LIMIT ?", (limit,))
        return [j for j in (self.get(r["id"]) for r in rows) if j]

    def last_job(self, conversation_id: str = "") -> dict[str, Any] | None:
        sql = "SELECT id FROM blender_jobs WHERE status='completed'"
        params: list[Any] = []
        if conversation_id:
            sql += " AND conversation_id=?"
            params.append(conversation_id)
        sql += " ORDER BY created_at DESC LIMIT 1"
        row = self._core.db.one(sql, params)
        return self.get(row["id"]) if row else None

    def file(self, job_id: str, filename: str) -> tuple[bytes, str] | None:
        job = self.get(job_id)
        if not job or not job.get("output_dir"):
            return None
        base = Path(job["output_dir"]).resolve()
        target = (base / Path(filename or "").name).resolve()
        if base != target and base not in target.parents:
            return None
        if not target.is_file():
            return None
        return target.read_bytes(), CONTENT_TYPES.get(target.suffix.lower(),
                                                      "application/octet-stream")

    def attach_message(self, job_id: str, message_id: str) -> None:
        self._core.db.execute("UPDATE blender_jobs SET message_id=? WHERE id=?",
                              (message_id, job_id))

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def submit(self, *, tool: str, action: str, title: str, settings: dict[str, Any],
               conversation_id: str = "", project_id: str = "", reuse_project: bool = False,
               meta: dict[str, Any] | None = None, timeout: int = 0,
               task_id: str = "") -> dict[str, Any]:
        """Cree et execute un job 3D reel. Retourne le job final (jamais None)."""
        settings = dict(settings or {})
        conf = self._core.settings.section("blender")

        project = None
        if reuse_project or project_id:
            project = self.project(project_id, conversation_id)
        if project is None and not reuse_project:
            project = self.create_project(conversation_id,
                                          name=title, prompt=str(settings.get("prompt") or ""))
        if project is None:
            project = self.create_project(conversation_id, name=title)

        job_dir = PROJECTS_DIR / new_id("b3d")
        try:
            job_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BlenderJobError(f"Dossier de sortie impossible : {exc}")

        if reuse_project and project.get("blend_path") and Path(project["blend_path"]).is_file():
            settings.setdefault("blend_in", project["blend_path"])

        settings.setdefault("preview", bool(conf.get("auto_preview", True)))
        settings.setdefault("preview_size", int(conf.get("preview_size", 640)))
        settings.setdefault("use_gpu", bool(conf.get("use_gpu", True)))

        job: dict[str, Any] = {
            "id": job_dir.name, "conversation_id": conversation_id, "message_id": "",
            "project_id": project["id"], "tool": tool, "action": action,
            "title": (title or action)[:200], "status": "queued", "stage": "queued",
            "progress": 0.0, "output_dir": str(job_dir), "outputs": [],
            "meta": dict(meta or {}), "error": "", "settings": settings,
            "created_at": time.time(),
        }
        settings.update({
            "output_dir": str(job_dir),
            "__job_id": job["id"], "__action": action, "__tool": tool,
            "__progress_file": str(TEMP_DIR / f"{job['id']}.progress.json"),
            "__timeout": max(30, int(timeout or 0)
                             or int(conf.get("default_timeout_s", 600))),
        })
        self._save(job)
        self._emit("blender.job.started", job)
        _log(f"JOB STARTED: {job['id']} {tool}/{action} project={project['id']}")

        if not self.available():
            job["error"] = ("Blender n'est pas installe ou pas detectable "
                            "(Settings -> Atelier 3D).")
            job["status"], job["stage"] = "failed", "failed"
            self._save(job)
            self._emit("blender.job.failed", job)
            _log(f"JOB FAILED: {job['id']} blender absent")
            return self.get(job["id"]) or job

        try:
            self._execute(job, task_id=task_id)
        except BlenderJobError as exc:
            job["error"] = str(exc)[:2000]
            job["status"], job["stage"] = "failed", "failed"
            self._save(job)
            self._emit("blender.job.failed", job)
            _log(f"JOB FAILED: {job['id']} {job['error'][:300]}")
            return self.get(job["id"]) or job

        if job.get("status") == "completed":
            self._update_project(project["id"], job)
        return self.get(job["id"]) or job

    # -- evenements / etat -------------------------------------------------
    def _emit(self, event: str, job: dict[str, Any], **extra) -> None:
        payload: dict[str, Any] = {
            "job_id": job["id"], "conversation_id": job.get("conversation_id", ""),
            "project_id": job.get("project_id", ""),
            "tool": job.get("tool", ""), "action": job.get("action", ""),
            "title": job.get("title", ""), "status": job.get("status", ""),
            "stage": job.get("stage", ""),
            "stage_label": STAGE_LABELS.get(job.get("stage", ""), ""),
            "progress": round(float(job.get("progress") or 0), 3),
            "preview_url": self._file_url(job, ("preview.png", "render.png")),
            "glb_url": self._file_url(job, ("model.glb",)),
            "error": job.get("error", ""),
        }
        payload.update(extra)
        self._core.events.emit(event, payload)

    def _set(self, job: dict[str, Any], *, status: str = "", stage: str = "",
             progress: float | None = None, message: str = "", emit: bool = True) -> None:
        previous_stage = job.get("stage", "")
        if status:
            job["status"] = status
        if stage:
            job["stage"] = stage
        if progress is not None:
            job["progress"] = max(0.0, min(1.0, float(progress)))
        self._save(job)
        if not emit:
            return
        self._emit("blender.job.progress", job, message=message)
        if stage and stage != previous_stage and previous_stage in STAGE_EVENTS:
            self._emit(STAGE_EVENTS[previous_stage], job)
        if stage == "rendering" and previous_stage != "rendering":
            self._emit("blender.render.started", job)
        _log(f"JOB PROGRESS: {job['id']} {job.get('stage')} "
             f"{round(float(job.get('progress') or 0) * 100)}%")

    # -- processus ---------------------------------------------------------
    def _build_command(self, settings_path: Path, settings: dict[str, Any] | None = None) -> list[str]:
        conf = self._core.settings.section("blender")
        settings = settings or {}
        cmd = [self.executable()]
        if not conf.get("show_blender_ui", False):
            cmd.append("--background")
        cmd.append("--disable-autoexec")
        # MPFB is an installed Blender extension. Factory startup strips the
        # extension preferences, so MPFB jobs must keep the normal startup
        # context and enable the dependency explicitly in the job script.
        if conf.get("factory_startup", True) and not settings.get("requires_mpfb"):
            cmd.append("--factory-startup")
        cmd += ["--python", str(SCRIPT_DIR / JOB_SCRIPT), "--", str(settings_path)]
        return cmd

    def _execute(self, job: dict[str, Any], task_id: str = "") -> None:
        settings = job["settings"]
        settings_path = TEMP_DIR / f"{job['id']}.settings.json"
        progress_file = Path(settings["__progress_file"])
        log_file = TEMP_DIR / f"{job['id']}.log"
        for stale in (progress_file,):
            try:
                stale.unlink(missing_ok=True)
            except Exception:
                pass
        settings_path.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")

        script_path = SCRIPT_DIR / JOB_SCRIPT
        if not script_path.is_file():
            raise BlenderJobError(f"Script Blender introuvable : {script_path}")

        cmd = self._build_command(settings_path, settings)
        self._set(job, status="running", stage="starting", progress=0.04)

        env = dict(os.environ)
        env["JARVIS_BLENDER_SETTINGS"] = str(settings_path)
        try:
            logf = open(log_file, "w", encoding="utf-8", errors="replace")
        except OSError:
            logf = None
        try:
            proc = subprocess.Popen(
                cmd, stdout=logf or subprocess.DEVNULL, stderr=subprocess.STDOUT,
                cwd=str(TEMP_DIR), env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as exc:
            if logf:
                logf.close()
            raise BlenderJobError(f"Impossible de lancer Blender : {exc}")
        with self._lock:
            self._procs[job["id"]] = proc

        deadline = time.time() + int(settings.get("__timeout") or 600)
        started = time.time()
        last_state: dict[str, Any] = {}
        try:
            while True:
                code = proc.poll()
                state = self._read_progress(progress_file)
                if state and state != last_state:
                    last_state = state
                    self._set(job, stage=state.get("stage") or "running",
                              progress=state.get("progress"),
                              message=state.get("message", ""))
                if self._is_cancelled(job["id"], task_id):
                    self._terminate(proc)
                    job["error"] = "Job annule."
                    self._set(job, status="cancelled", stage="cancelled", emit=False)
                    self._emit("blender.job.cancelled", job)
                    _log(f"JOB CANCELLED: {job['id']}")
                    return
                if code is not None:
                    break
                if time.time() > deadline:
                    self._terminate(proc)
                    raise BlenderJobError(
                        f"Delai depasse ({int(time.time() - started)} s). "
                        f"Augmente blender.default_timeout_s si le modele est lourd.")
                time.sleep(0.2)
        finally:
            with self._lock:
                self._procs.pop(job["id"], None)
                self._cancelled.discard(job["id"])
            if logf:
                logf.close()

        error_file = Path(job["output_dir"]) / "error.txt"
        if proc.returncode != 0 or error_file.is_file():
            detail = ""
            if error_file.is_file():
                detail = error_file.read_text(encoding="utf-8", errors="replace")[:1200]
            if not detail:
                detail = self._tail_log(log_file, 30)
            raise BlenderJobError(
                f"Blender a echoue (code {proc.returncode}).\n{detail}".strip())

        meta = self._read_metadata(job)
        job["outputs"] = [{"name": name, "kind": kind}
                          for name, kind in self._collect_outputs(job["output_dir"])]
        job["meta"] = dict(meta or {})

        problem = self._validate(job)
        if problem:
            job["error"] = problem
            self._set(job, status="failed", stage="failed", emit=False)
            self._emit("blender.job.failed", job)
            _log(f"JOB FAILED (validation): {job['id']} {problem[:200]}")
            return

        self._set(job, status="completed", stage="completed", progress=1.0, emit=False)
        self._emit("blender.job.completed", job, outputs=job["outputs"], meta=job["meta"])
        if any(o["name"] == "render.png" for o in job["outputs"]):
            self._emit("blender.render.completed", job)
        _log(f"JOB COMPLETED: {job['id']} outputs={len(job['outputs'])} "
             f"tris={job['meta'].get('polycount')}")

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

    def _is_cancelled(self, job_id: str, task_id: str = "") -> bool:
        with self._lock:
            if job_id in self._cancelled:
                return True
        if not task_id:
            return False
        try:
            return bool(self._core.tasks.is_cancelled(task_id))
        except Exception:
            return False

    def _read_progress(self, path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        return {"progress": float(data.get("progress") or 0),
                "message": str(data.get("message") or "")[:200],
                "stage": str(data.get("stage") or "")}

    def _read_metadata(self, job: dict[str, Any]) -> dict[str, Any]:
        path = Path(job["output_dir"]) / "metadata.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if isinstance(data, dict):
            data.pop("output_dir", None)
            return data
        return {}

    @staticmethod
    def _collect_outputs(output_dir: str) -> list[tuple[str, str]]:
        root = Path(output_dir)
        out: list[tuple[str, str]] = []
        try:
            entries = sorted(root.iterdir())
        except OSError:
            return out
        for f in entries:
            if not f.is_file() or f.name in {"error.txt"}:
                continue
            out.append((f.name, OUTPUT_KINDS.get(f.suffix.lower(), "file")))
        return out

    def _validate(self, job: dict[str, Any]) -> str:
        """Refuse un faux succes. Retourne un message d'erreur, ou ''."""
        meta = job.get("meta") or {}
        names = {o["name"].lower() for o in job.get("outputs", [])}
        if not job.get("outputs"):
            return "Blender s'est termine sans produire le moindre fichier."
        if not meta:
            return "Aucune metadonnee produite : le job n'est pas alle a son terme."
        verified = meta.get("verified") or {}
        failures = list(verified.get("exports_failed") or [])
        wanted = job["settings"].get("export_formats")
        expects_export = wanted is None or bool(wanted)
        if expects_export and failures:
            return "Export echoue : " + "; ".join(str(f) for f in failures)[:600]
        if expects_export and "model.glb" in names:
            glb = (verified.get("glb") or {})
            if glb and not glb.get("ok", True):
                return "GLB illisible : " + str(glb.get("error", "verification echouee"))
            if glb.get("meshes", 0) == 0:
                return "Le GLB exporte ne contient aucun maillage."
        rig = meta.get("rig") or {}
        if rig.get("rigged") and rig.get("skinned_meshes") and not rig.get("has_weights"):
            return ("Le maillage est lie a l'armature mais sans aucun poids : "
                    "le personnage ne se deformerait pas dans le viewer.")
        if (rig.get("rigged") and rig.get("has_weights") and "model.glb" in names
                and (verified.get("glb") or {}).get("skins", 0) == 0):
            return "Le GLB exporte ne contient pas le skin de l'armature."
        action = job.get("action", "")
        if action in {"animate"} and not meta.get("animations"):
            return "Aucune animation n'a ete produite dans le fichier."
        if action in {"rig"} and not (meta.get("rig") or {}).get("rigged"):
            return "Le rig n'a pas ete cree."
        if action == "render" and "render.png" not in names:
            return "Le rendu n'a produit aucune image."
        if meta.get("polycount", 0) == 0 and action in {"create", "modify", "convert"}:
            return "Le modele final ne contient aucune geometrie."
        return ""

    @staticmethod
    def _tail_log(path: Path, lines: int = 30) -> str:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
        keep = [ln for ln in text.splitlines() if ln.strip()]
        return "\n".join(keep[-lines:])[:2000]

    # -- annulation --------------------------------------------------------
    def cancel(self, job_id: str) -> dict[str, Any] | None:
        job = self.get(job_id)
        if not job:
            return None
        if job["status"] in {"queued", "running"}:
            with self._lock:
                self._cancelled.add(job_id)
                proc = self._procs.get(job_id)
            if proc is not None:
                self._terminate(proc)
            _log(f"JOB CANCEL REQUESTED: {job_id}")
            for _ in range(40):
                time.sleep(0.1)
                refreshed = self.get(job_id)
                if refreshed and refreshed["status"] not in {"queued", "running"}:
                    return refreshed
        return self.get(job_id)

    def shutdown(self) -> None:
        with self._lock:
            procs = list(self._procs.values())
        for proc in procs:
            self._terminate(proc)
