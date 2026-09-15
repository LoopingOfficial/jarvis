"""Service hôte AvatarEngine : opérations déterministes, candidat isolé."""
from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from typing import Any

from ..permissions import SAFE_WRITE
from . import identity
from .features import features_to_params, issues_to_params
from .schema import BUILD_ID, OPERATIONS, PRESETS, REQUIRED_PARTS, merge_params
from ..avatar_engine_v2.presets import BUILD_ID as MPFB_BUILD_ID, generate_from_preset

PRIMITIVE_HINTS = ("primitive_cylinder", "primitive_cube", "primitive_uv_sphere",
                   "primitive_cone")
STATIC_ONLY_BLOCKED = {"ensure_rig", "ensure_face_rig", "create_visemes", "create_animations"}


def _log(message: str) -> None:
    line = f"[avatar-engine] {message}"
    try:
        print(line, flush=True)
    except Exception:
        pass


class AvatarEngine:
    def __init__(self, core) -> None:
        self._core = core
        self._lock = threading.RLock()
        identity.ensure_dirs()

    def status(self) -> dict[str, Any]:
        snap = identity.snapshot("status")
        return {
            "build": MPFB_BUILD_ID,
            "legacy_build": BUILD_ID,
            "operations": list(OPERATIONS),
            "presets": dict(PRESETS),
            "identity": snap,
            "live_protected": True,
            "blender": self._core.blender.available(),
        }

    def inspect(self, *, conversation_id: str = "") -> dict[str, Any]:
        """Inventaire FACTUEL : identité + scène si un .blend source existe."""
        snap = identity.snapshot("inspect")
        inventory: dict[str, Any] = {
            "build": MPFB_BUILD_ID,
            "identity": snap,
            "required_parts": list(REQUIRED_PARTS),
            "live_protected": True,
        }
        current = self._core.blender.current(conversation_id) if conversation_id else None
        if current and current.get("has_blend"):
            inventory["conversation_project"] = {
                "id": current.get("current_blender_project"),
                "name": current.get("name"),
                "blend": current.get("current_model"),
            }
        src = identity.source_blend()
        inventory["source_exists"] = bool(src)
        inventory["candidate_exists"] = identity.candidate_glb().is_file()
        inventory["current_exists"] = identity.LIVE_GLB.is_file()
        if self._core.blender.available() and src:
            job = self._run_blender("inspect", {"blend_in": str(src), "preview": False,
                                                "export_formats": []})
            inventory["scene"] = (job.get("meta") or {}).get("inventory") or {}
            inventory["job_id"] = job.get("id", "")
            inventory["job_status"] = job.get("status", "")
        return {"ok": True, "inventory": inventory}

    def apply(self, operation: str, parameters: dict[str, Any] | None = None,
              *, conversation_id: str = "", features: dict[str, Any] | None = None,
              ) -> dict[str, Any]:
        operation = str(operation or "").strip()
        if operation not in OPERATIONS:
            return {"ok": False, "error": f"Opération inconnue : {operation}"}
        if operation in STATIC_ONLY_BLOCKED:
            return {"ok": False, "error": (
                "Le premier jalon MPFB_V1 est statique. Rig, face rig, visèmes et "
                "animations restent bloqués jusqu'à l'approbation utilisateur.")}
        params = merge_params(parameters)
        if features:
            params.update({k: v for k, v in features_to_params(features).items()
                           if not isinstance(v, dict)})
        identity.ensure_dirs()
        if not self._core.blender.available():
            return {"ok": False, "error": "Blender n'est pas détecté."}
        settings = {
            "operation": operation,
            "parameters": params,
            "raw_parameters": parameters or {},
            "features": features or {},
            "presets": dict(PRESETS),
            "identity": identity.snapshot(operation),
            "blend_in": str(identity.source_blend() or ""),
            "blend_out": str(identity.candidate_blend()),
            "glb_out": str(identity.candidate_glb()),
            "export_formats": ["glb"],
            "preview": True,
            "protect_live": True,
            "live_glb": str(identity.LIVE_GLB),
            "conversation_id": conversation_id,
            "requires_mpfb": True,
            "build_id": "JARVIS_MPFBA_V1_20260911_A",
            "milestone": "static",
        }
        job = self._run_blender(operation, settings)
        ok = job.get("status") == "completed"
        meta = job.get("meta") or {}
        export_check = self._verify_candidate() if operation != "inspect" else {"ok": True}
        if ok and not export_check.get("ok"):
            ok = False
            meta["export_validation"] = export_check
        return {
            "ok": ok,
            "operation": operation,
            "job_id": job.get("id", ""),
            "error": job.get("error", "") if not ok else "",
            "candidate": identity.snapshot("candidate"),
            "validation": meta.get("avatar_validation") or {},
            "meta": meta,
            "live_untouched": True,
        }

    def build_from_reference(self, features: dict[str, Any], *,
                             conversation_id: str = "",
                             milestone: str = "appearance") -> dict[str, Any]:
        if not features or not features.get("analysis_success"):
            return {"ok": False, "error": "Vision obligatoire : analyse en échec. STOP."}
        if milestone not in {"appearance", "static"}:
            return {"ok": False, "error": (
                "MPFB_V1 est limité au jalon statique jusqu'à l'approbation utilisateur. "
                "Rig, visage et animation ne sont pas déclenchés.")}
        params = features_to_params(features)
        chain = ["load_base", "set_body_proportions", "set_face_morphs",
                 "set_eyes", "set_hair", "set_outfit", "set_materials"]
        chain += ["validate", "export_glb"]
        identity.ensure_dirs()
        settings = {
            "operation": "build",
            "chain": chain,
            "parameters": params,
            "features": features,
            "milestone": milestone,
            "presets": dict(PRESETS),
            "identity": identity.snapshot("build"),
            "blend_in": str(identity.source_blend() or ""),
            "blend_out": str(identity.candidate_blend()),
            "glb_out": str(identity.candidate_glb()),
            "export_formats": ["glb"],
            "preview": True,
            "protect_live": True,
            "live_glb": str(identity.LIVE_GLB),
            "conversation_id": conversation_id,
            "requires_mpfb": True,
            "build_id": MPFB_BUILD_ID,
        }
        job = self._run_blender("build", settings)
        ok = job.get("status") == "completed"
        meta = job.get("meta") or {}
        export_check = self._verify_candidate()
        if ok and not export_check.get("ok"):
            ok = False
            meta["export_validation"] = export_check
        return {
            "ok": ok,
            "job_id": job.get("id", ""),
            "error": "" if ok else (job.get("error") or "Build candidat en échec."),
            "candidate": identity.snapshot("candidate"),
            "parameters": params,
            "chain": chain,
            "live_untouched": True,
            "meta": meta,
        }

    @staticmethod
    def _verify_candidate() -> dict[str, Any]:
        """Vérifie le GLB écrit hors du dossier de job avant de le publier."""
        path = identity.candidate_glb()
        if not path.is_file():
            return {"ok": False, "error": "Le candidat GLB n'a pas été exporté."}
        try:
            from ..blender_scripts.lib.common import verify_glb
            return verify_glb(str(path))
        except Exception as exc:
            return {"ok": False, "error": f"GLB non rechargeable : {exc}"}

    def improve(self, issues: list[str], current: dict[str, Any] | None = None) -> dict[str, Any]:
        params = issues_to_params(issues, current)
        return self.apply("modify_face", params)

    def accept_candidate(self) -> dict[str, Any]:
        """Adopte le candidat : il devient MASTER + GLB live. Jamais avant."""
        glb = identity.candidate_glb()
        blend = identity.candidate_blend()
        if not glb.is_file():
            return {"ok": False, "error": "Aucun candidat GLB à adopter."}
        verification = self._verify_candidate()
        if not verification.get("ok"):
            return {"ok": False, "error": verification.get("error") or "Candidat GLB invalide.",
                    "verification": verification}
        identity.ensure_dirs()
        identity.LIVE_GLB.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(glb, identity.LIVE_GLB)
        if blend.is_file():
            shutil.copy2(blend, identity.MASTER_BLEND)
        _log(f"candidate accepted → live={identity.LIVE_GLB}")
        self._core.events.emit("avatar.engine.accepted", {
            "live_glb": str(identity.LIVE_GLB),
            "master_blend": str(identity.MASTER_BLEND),
            "build": MPFB_BUILD_ID,
        })
        return {"ok": True, "live_glb": str(identity.LIVE_GLB),
                "master_blend": str(identity.MASTER_BLEND)}

    def reject_candidate(self) -> dict[str, Any]:
        for path in (identity.candidate_glb(), identity.candidate_blend()):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        return {"ok": True, "live_untouched": True}

    def comparison(self) -> dict[str, Any]:
        snap = identity.snapshot("compare")
        return {
            "current": snap["current_glb"],
            "candidate": snap["candidate_glb"],
            "master": snap["master_blend"],
            "current_url": "/assets/avatar/jarvis_avatar.glb",
            "candidate_url": "/api/avatar/engine/candidate/model" if identity.candidate_glb().is_file() else "",
        }

    def build_from_preset(self, preset_id: str = "jarvis_v1", *,
                          conversation_id: str = "") -> dict[str, Any]:
        """Build the deterministic MPFB static candidate from a checked-in preset."""
        generated = generate_from_preset(preset_id)
        identity.ensure_dirs()
        if not self._core.blender.available():
            return {"ok": False, "error": "Blender n'est pas détecté."}
        settings = {
            **generated,
            "operation": "build",
            "presets": dict(PRESETS),
            "identity": identity.snapshot("build_from_preset"),
            "blend_in": str(identity.source_blend() or ""),
            "blend_out": str(identity.candidate_blend()),
            "glb_out": str(identity.candidate_glb()),
            "export_formats": ["glb"],
            "preview": True,
            "protect_live": True,
            "live_glb": str(identity.LIVE_GLB),
            "conversation_id": conversation_id,
        }
        job = self._run_blender("build_from_preset", settings)
        ok = job.get("status") == "completed"
        meta = job.get("meta") or {}
        export_check = self._verify_candidate()
        if ok and not export_check.get("ok"):
            ok = False
            meta["export_validation"] = export_check
        return {
            "ok": ok,
            "job_id": job.get("id", ""),
            "error": "" if ok else (job.get("error") or "Build preset en échec."),
            "candidate": identity.snapshot("candidate"),
            "generated": generated,
            "validation": meta.get("avatar_validation") or {},
            "meta": meta,
            "live_untouched": True,
        }

    def _run_blender(self, operation: str, settings: dict[str, Any]) -> dict[str, Any]:
        return self._core.blender.submit(
            tool="avatar.engine.apply",
            action="avatar_engine",
            title=f"AvatarEngine:{operation}",
            settings=settings,
            conversation_id=str(settings.get("conversation_id") or ""),
            reuse_project=False,
            timeout=int(self._core.settings.get("blender", "default_timeout_s", 600) or 600),
        )
