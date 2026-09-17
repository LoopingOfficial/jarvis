"""Pipeline de mise à jour d'avatar : analyse → modification → rendu → comparaison → itération.

Orchestre le flux complet :
1. Réception de l'image de référence
2. Analyse des features
3. Construction du plan de modification
4. Ouverture du master blend → copie en révision
5. Modification via Blender (update_avatar script)
6. Rendu multi-vues
7. Évaluation / comparaison
8. Itération si nécessaire
9. Validation ou rollback
"""
from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .db import dumps, loads, new_id

REVISIONS_DIR = DATA_DIR / "generated" / "avatar_revisions"
RENDERS_DIR = DATA_DIR / "generated" / "avatar_renders"

VIEWS = ("front", "side", "34", "full")

MAX_ITERATIONS = 3
IMPROVEMENT_THRESHOLD = 0.05


def _log(message: str) -> None:
    line = f"[avatar_pipeline] {message}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)
    except Exception:
        pass


class AvatarUpdatePipeline:
    """Pipeline complet de mise à jour d'avatar depuis une image de référence."""

    def __init__(self, core) -> None:
        self._core = core
        self._lock = threading.RLock()
        for d in (REVISIONS_DIR, RENDERS_DIR):
            d.mkdir(parents=True, exist_ok=True)

    def update_from_reference(
        self,
        reference_id: str,
        *,
        options: dict[str, Any] | None = None,
        conversation_id: str = "",
        max_iterations: int = MAX_ITERATIONS,
    ) -> dict[str, Any]:
        """Lance le pipeline complet de mise à jour d'avatar.

        Args:
            reference_id: ID de l'image de référence
            options: Options de modification (modify_face, modify_hair, etc.)
            conversation_id: Conversation pour le suivi
            max_iterations: Nombre max de passes d'amélioration

        Returns:
            Résultat du pipeline avec révision, scores, aperçus
        """
        opts = AvatarUpdateOptions.from_dict(options or {})
        ref = self._core.avatar_ref.get(reference_id)
        if not ref:
            return {"ok": False, "error": f"Référence inconnue : {reference_id}"}

        self._core.events.emit("avatar.update_started", {
            "reference_id": reference_id, "options": opts.to_dict()})
        self._core.activity(
            title="Mise à jour avatar",
            detail="Analyse de l'image de référence",
            kind="avatar_update", state="ACTING")

        try:
            features = self._analyze_reference(ref)
            plan = self._build_modification_plan(features, opts)
            rev = self._create_revision(reference_id, conversation_id)

            result = self._execute_modification(
                rev, ref, features, plan, opts, conversation_id, max_iterations)

            if result.get("ok"):
                self._core.events.emit("avatar.update_completed", {
                    "revision_id": rev["id"],
                    "overall_score": result.get("evaluation", {}).get("overall_score", 0)})
            else:
                self._core.events.emit("avatar.update_failed", {
                    "error": result.get("error", "")})

            return result

        except Exception as exc:
            _log(f"Pipeline error: {exc}")
            self._core.events.emit("avatar.update_failed", {"error": str(exc)})
            return {"ok": False, "error": str(exc)}

    def _analyze_reference(self, ref: dict[str, Any]) -> dict[str, Any]:
        self._core.events.emit("avatar.update_progress", {
            "stage": "analyzing", "progress": 0.1,
            "message": "Analyse de l'image de référence"})
        features = ref.get("extracted_features") or {}
        if not features:
            features = self._core.avatar_ref.analyze(ref["id"])
        return features

    def _build_modification_plan(
        self, features: dict[str, Any], options: AvatarUpdateOptions
    ) -> dict[str, Any]:
        self._core.events.emit("avatar.update_progress", {
            "stage": "planning", "progress": 0.2,
            "message": "Construction du plan de modification"})

        mods = features.get("modifications", {})
        plan: dict[str, Any] = {
            "operations": [],
            "materials": {},
            "target_art_style": features.get("art_style", ""),
            "preserve_identity": options.preserve_identity,
            "style_strength": options.style_strength,
            "realism_level": options.realism_level,
        }

        mapping = {
            "modify_face": "face",
            "modify_hair": "hair",
            "modify_outfit": "outfit",
            "modify_colors": "colors",
            "modify_materials": "materials",
            "modify_pose": "pose",
            "modify_proportions": "proportions",
        }
        for opt_key, mod_key in mapping.items():
            if getattr(options, opt_key, False) and mod_key in mods:
                plan["operations"].append({
                    "type": mod_key,
                    "description": mods[mod_key],
                    "features": {k: v for k, v in features.items()
                                 if k not in {"modifications", "dominant_colors"}},
                })

        if not plan["operations"]:
            for mod_key, desc in mods.items():
                if desc:
                    plan["operations"].append({
                        "type": mod_key,
                        "description": desc,
                        "features": features,
                    })

        if features.get("dominant_colors"):
            plan["materials"]["dominant_colors"] = features["dominant_colors"]
        if features.get("outfit_colors"):
            plan["materials"]["outfit_colors"] = features["outfit_colors"]
        if features.get("hair_color"):
            plan["materials"]["hair_color"] = features["hair_color"]
        if features.get("skin_tone"):
            plan["materials"]["skin_tone"] = features["skin_tone"]

        plan["style_directive"] = self._build_style_directive(features, options)
        return plan

    @staticmethod
    def _build_style_directive(
        features: dict[str, Any], options: AvatarUpdateOptions
    ) -> str:
        parts = []
        art = features.get("art_style", "")
        if art:
            parts.append(f"Style : {art}")
        mood = features.get("mood", "")
        if mood:
            parts.append(f"Ambiance : {mood}")
        if options.realism_level == "realistic":
            parts.append("Rendu réaliste")
        elif options.realism_level == "cartoon":
            parts.append("Style cartoon/stylisé")
        elif options.realism_level == "anime":
            parts.append("Style anime/manga")
        strength = options.style_strength
        if strength < 0.3:
            parts.append("Légères touches de la référence")
        elif strength > 0.7:
            parts.append("Forte influence de la référence")
        else:
            parts.append("Influence modérée de la référence")
        return " · ".join(parts) if parts else "Adapter selon la référence"

    def _create_revision(
        self, reference_id: str, conversation_id: str
    ) -> dict[str, Any]:
        return self._core.avatar_ref.create_revision(
            reference_id, conversation_id=conversation_id)

    def _execute_modification(
        self,
        rev: dict[str, Any],
        ref: dict[str, Any],
        features: dict[str, Any],
        plan: dict[str, Any],
        options: AvatarUpdateOptions,
        conversation_id: str,
        max_iterations: int,
    ) -> dict[str, Any]:
        master = self._core.avatar_ref.get_master_blend()
        if not master:
            return {"ok": False, "error": "Aucun fichier Blender maître trouvé "
                    "(jarvis_avatar.blend). Crée d'abord un avatar avec blender.create_model."}

        rev_dir = REVISIONS_DIR / rev["id"]
        rev_dir.mkdir(parents=True, exist_ok=True)
        rev_blend = rev_dir / "avatar_revision.blend"
        shutil.copy2(master, str(rev_blend))

        blender_result = None
        last_score = 0.0
        best_blend = str(rev_blend)
        best_eval: dict[str, Any] = {}
        iteration = 0

        for iteration in range(1, max_iterations + 1):
            _log(f"Iteration {iteration}/{max_iterations}")

            self._core.events.emit("avatar.update_progress", {
                "stage": "modifying", "progress": 0.2 + 0.15 * iteration,
                "message": f"Modification de l'avatar (passage {iteration}/{max_iterations})"})

            blender_result = self._run_blender_modification(
                str(rev_blend), ref, features, plan, options, conversation_id)

            if not blender_result.get("ok"):
                _log(f"Blender modification failed: {blender_result.get('error')}")
                break

            new_blend = blender_result.get("blend_path", str(rev_blend))
            if new_blend and Path(new_blend).is_file():
                rev_blend = Path(new_blend)

            self._core.events.emit("avatar.update_progress", {
                "stage": "rendering", "progress": 0.55 + 0.1 * iteration,
                "message": f"Rendu des aperçus (passage {iteration})"})

            previews = self._render_preview_views(str(rev_blend), rev["id"], iteration)

            self._core.events.emit("avatar.update_progress", {
                "stage": "evaluating", "progress": 0.75 + 0.05 * iteration,
                "message": f"Évaluation (passage {iteration})"})

            evaluation = self._evaluate_iteration(
                previews, ref["id"], features, iteration)

            current_score = evaluation.get("overall_score", 0)
            _log(f"Iteration {iteration} score: {current_score:.3f} "
                 f"(previous: {last_score:.3f})")

            if current_score > last_score + IMPROVEMENT_THRESHOLD:
                last_score = current_score
                best_blend = str(rev_blend)
                best_eval = evaluation
                self._update_revision_previews(rev["id"], previews, evaluation)
                _log(f"Score improved → keeping iteration {iteration}")
            elif current_score >= last_score:
                best_eval = evaluation
                self._update_revision_previews(rev["id"], previews, evaluation)
                _log(f"Score stable → keeping iteration {iteration}")
            else:
                _log(f"Score decreased ({current_score:.3f} < {last_score:.3f}) → rollback")
                break

        self._core.avatar_ref.update_revision(
            rev["id"],
            blend_path=best_blend,
            evaluation=best_eval)

        return {
            "ok": True,
            "revision_id": rev["id"],
            "blend_path": best_blend,
            "evaluation": best_eval,
            "iterations": iteration,
            "score_history": last_score,
        }

    def _run_blender_modification(
        self,
        blend_path: str,
        ref: dict[str, Any],
        features: dict[str, Any],
        plan: dict[str, Any],
        options: AvatarUpdateOptions,
        conversation_id: str,
    ) -> dict[str, Any]:
        settings = {
            "blend_in": blend_path,
            "reference_image": ref.get("source_path", ""),
            "features": features,
            "plan": plan,
            "options": options.to_dict(),
            "preview": False,
            "export_formats": ["glb"],
            "output_blend": True,
        }
        try:
            job = self._core.blender.submit(
                tool="blender.update_avatar", action="update_avatar",
                title="Mise à jour avatar depuis référence",
                settings=settings, conversation_id=conversation_id,
                reuse_project=True, timeout=600)
            if job.get("status") == "completed":
                outputs = job.get("outputs") or []
                blend_out = ""
                for out in outputs:
                    if out.get("name", "").endswith(".blend"):
                        blend_out = str(
                            Path(job.get("output_dir", "")) / out["name"])
                        break
                return {"ok": True, "blend_path": blend_out or blend_path,
                        "job": job}
            return {"ok": False, "error": job.get("error", "Blender échoué")}
        except Exception as exc:
            _log(f"Blender submit error: {exc}")
            return {"ok": False, "error": str(exc)}

    def _render_preview_views(
        self, blend_path: str, rev_id: str, iteration: int
    ) -> dict[str, str]:
        rev_dir = RENDERS_DIR / rev_id
        rev_dir.mkdir(parents=True, exist_ok=True)
        previews: dict[str, str] = {}
        for view in VIEWS:
            out_path = str(rev_dir / f"preview_{view}_iter{iteration}.png")
            try:
                settings = {
                    "blend_in": blend_path,
                    "view": view,
                    "output_path": out_path,
                    "engine": "eevee",
                    "samples": 64,
                    "width": 512,
                    "height": 512,
                    "preview": False,
                    "export_formats": [],
                }
                job = self._core.blender.submit(
                    tool="blender.render_avatar_view",
                    action="render_avatar_view",
                    title=f"Rendu aperçu {view}",
                    settings=settings,
                    reuse_project=True,
                    timeout=120)
                if job.get("status") == "completed":
                    for out in job.get("outputs", []):
                        if out.get("name", "").endswith(".png"):
                            actual = str(
                                Path(job.get("output_dir", "")) / out["name"])
                            previews[view] = actual
                            break
            except Exception as exc:
                _log(f"Render view {view} failed: {exc}")
        return previews

    def _evaluate_iteration(
        self,
        previews: dict[str, str],
        reference_id: str,
        features: dict[str, Any],
        iteration: int,
    ) -> dict[str, Any]:
        front = previews.get("front", "")
        if front:
            return self._core.avatar_ref.evaluate_similarity(front, reference_id)
        return {
            "overall_score": 0.5, "face_score": 0.5, "hair_score": 0.5,
            "outfit_score": 0.5, "style_score": 0.5,
            "similarity_score": 0.5, "defects": ["no_front_preview"]}

    def _update_revision_previews(
        self,
        rev_id: str,
        previews: dict[str, str],
        evaluation: dict[str, Any],
    ) -> None:
        self._core.avatar_ref.update_revision(
            rev_id,
            preview_front=previews.get("front", ""),
            preview_side=previews.get("side", ""),
            preview_34=previews.get("34", ""),
            preview_full=previews.get("full", ""),
            evaluation=evaluation)


class AvatarUpdateOptions:
    """Options de contrôle de la mise à jour d'avatar."""

    def __init__(
        self,
        modify_face: bool = True,
        modify_hair: bool = True,
        modify_outfit: bool = True,
        modify_colors: bool = True,
        modify_materials: bool = True,
        modify_pose: bool = False,
        modify_proportions: bool = False,
        preserve_identity: bool = True,
        style_strength: float = 0.6,
        realism_level: str = "balanced",
    ) -> None:
        self.modify_face = modify_face
        self.modify_hair = modify_hair
        self.modify_outfit = modify_outfit
        self.modify_colors = modify_colors
        self.modify_materials = modify_materials
        self.modify_pose = modify_pose
        self.modify_proportions = modify_proportions
        self.preserve_identity = preserve_identity
        self.style_strength = max(0.0, min(1.0, style_strength))
        self.realism_level = realism_level if realism_level in (
            "realistic", "balanced", "cartoon", "anime") else "balanced"

    def to_dict(self) -> dict[str, Any]:
        return {
            "modify_face": self.modify_face,
            "modify_hair": self.modify_hair,
            "modify_outfit": self.modify_outfit,
            "modify_colors": self.modify_colors,
            "modify_materials": self.modify_materials,
            "modify_pose": self.modify_pose,
            "modify_proportions": self.modify_proportions,
            "preserve_identity": self.preserve_identity,
            "style_strength": self.style_strength,
            "realism_level": self.realism_level,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AvatarUpdateOptions:
        if not data:
            return cls()
        return cls(
            modify_face=bool(data.get("modify_face", True)),
            modify_hair=bool(data.get("modify_hair", True)),
            modify_outfit=bool(data.get("modify_outfit", True)),
            modify_colors=bool(data.get("modify_colors", True)),
            modify_materials=bool(data.get("modify_materials", True)),
            modify_pose=bool(data.get("modify_pose", False)),
            modify_proportions=bool(data.get("modify_proportions", False)),
            preserve_identity=bool(data.get("preserve_identity", True)),
            style_strength=float(data.get("style_strength", 0.6)),
            realism_level=str(data.get("realism_level", "balanced")),
        )

    @classmethod
    def from_text(cls, text: str) -> AvatarUpdateOptions:
        low = (text or "").lower()
        opts = cls()
        if "visage" in low or "face" in low:
            opts.modify_face = True
        if "coiffure" in low or "cheveux" in low or "hair" in low:
            opts.modify_hair = True
        if "tenue" in low or "vetement" in low or "outfit" in low:
            opts.modify_outfit = True
        if "couleur" in low:
            opts.modify_colors = True
        if "pose" in low or "posture" in low:
            opts.modify_pose = True
        if "proportion" in low:
            opts.modify_proportions = True
        if "identit" in low or "conserve" in low or "garde" in low:
            opts.preserve_identity = True
        if "seulement" in low or "uniquement" in low:
            opts.modify_face = "visage" in low
            opts.modify_hair = "coiffure" in low or "cheveux" in low
            opts.modify_outfit = "tenue" in low or "vetement" in low
            opts.modify_colors = "couleur" in low
            opts.modify_materials = False
            opts.modify_pose = False
            opts.modify_proportions = False
        if "réaliste" in low or "realistic" in low:
            opts.realism_level = "realistic"
        if "cartoon" in low or "stylisé" in low or "style" in low:
            opts.realism_level = "cartoon"
        if "anime" in low or "manga" in low:
            opts.realism_level = "anime"
        if "inspir" in low or "sans copier" in low:
            opts.style_strength = 0.4
        if "fort" in low or "exact" in low or "identique" in low:
            opts.style_strength = 0.9
        return opts
