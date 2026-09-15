"""Gestionnaire de références avatar : stockage, analyse, comparaison.

Gère les images de référence uploadées par l'utilisateur pour modifier
l'avatar 3D. Chaque référence est analysée pour extraire des features
visuelles (visage, coiffure, vêtements, couleurs, style) qui seront
utilisées pour piloter la modification Blender.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .db import dumps, loads, new_id

REFS_DIR = DATA_DIR / "avatar_references"
RENDERS_DIR = DATA_DIR / "generated" / "avatar_renders"
MASTER_BLEND = Path(__file__).resolve().parent.parent / "assets" / "blender" / "jarvis_avatar.blend"

REFERENCE_TYPES = (
    "human_face", "full_body", "outfit", "stylized_character",
    "hairstyle", "color_style", "pose_reference", "mixed",
)

MODIFICATION_TYPES = (
    "face", "hair", "outfit", "colors", "materials",
    "expression", "style", "posture", "proportions", "full",
)


def _ensure_dirs() -> None:
    for d in (REFS_DIR, RENDERS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _log(message: str) -> None:
    line = f"[avatar_ref] {message}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)
    except Exception:
        pass


class AvatarReferenceManager:
    """Stocke et analyse les images de référence pour modification d'avatar."""

    def __init__(self, core) -> None:
        self._core = core
        self._lock = threading.RLock()
        _ensure_dirs()

    # ------------------------------------------------------------------
    # Références — CRUD
    # ------------------------------------------------------------------
    def add(self, source_path: str, *, reference_type: str = "mixed",
            tags: list[str] | None = None, conversation_id: str = "") -> dict[str, Any]:
        src = Path(source_path)
        if not src.is_file():
            raise ValueError(f"Fichier introuvable : {source_path}")

        ext = src.suffix.lower()
        if ext not in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
            raise ValueError(f"Format non supporté : {ext}")

        ref_id = new_id("avref")
        dest = REFS_DIR / f"{ref_id}{ext}"
        shutil.copy2(str(src), str(dest))

        aliases = {"face": "human_face", "style": "stylized_character"}
        reference_type = aliases.get(reference_type, reference_type)
        now = time.time()
        ref = {
            "id": ref_id,
            "source_path": str(dest),
            "original_name": src.name,
            "reference_type": reference_type if reference_type in REFERENCE_TYPES else "mixed",
            "tags": tags or [],
            "extracted_features": {},
            "conversation_id": conversation_id,
            "uploaded_at": now,
        }
        self._core.db.execute(
            "INSERT INTO avatar_references(id, source_path, original_name, reference_type, "
            "tags, extracted_features, conversation_id, uploaded_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (ref_id, str(dest), src.name, ref["reference_type"],
             dumps(ref["tags"]), dumps({}), conversation_id, now))
        _log(f"Reference added: {ref_id} ({src.name})")
        return ref

    def get(self, ref_id: str) -> dict[str, Any] | None:
        row = self._core.db.one(
            "SELECT * FROM avatar_references WHERE id=?", (ref_id,))
        if not row:
            return None
        return self._row_to_ref(row)

    def list(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = self._core.db.query(
            "SELECT * FROM avatar_references ORDER BY uploaded_at DESC LIMIT ?",
            (limit,))
        return [self._row_to_ref(r) for r in rows]

    def delete(self, ref_id: str) -> bool:
        ref = self.get(ref_id)
        if not ref:
            return False
        try:
            Path(ref["source_path"]).unlink(missing_ok=True)
        except OSError:
            pass
        self._core.db.execute("DELETE FROM avatar_references WHERE id=?", (ref_id,))
        return True

    def _row_to_ref(self, row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "source_path": row["source_path"],
            "original_name": row["original_name"],
            "reference_type": row["reference_type"],
            "tags": loads(row["tags"], []) or [],
            "extracted_features": loads(row["extracted_features"], {}) or {},
            "conversation_id": row["conversation_id"],
            "uploaded_at": row["uploaded_at"],
            "created_at": row["uploaded_at"],
        }

    # ------------------------------------------------------------------
    # Analyse de l'image (features extraites)
    # ------------------------------------------------------------------
    def analyze(self, ref_id: str) -> dict[str, Any]:
        """Analyse l'image de référence avec un modèle de VISION.

        L'image est réellement envoyée au modèle (pas seulement son nom de
        fichier). En cas d'échec, on renvoie `analysis_success=False` et AUCUNE
        feature inventée : le pipeline doit suspendre la génération plutôt que
        de fabriquer un avatar générique.
        """
        ref = self.get(ref_id)
        if not ref:
            raise ValueError(f"Référence inconnue : {ref_id}")

        features = self._extract_features(ref)
        self._core.db.execute(
            "UPDATE avatar_references SET extracted_features=? WHERE id=?",
            (dumps(features), ref_id))
        ref["extracted_features"] = features
        if features.get("analysis_success"):
            _log(f"analysis_success=true ref={ref_id} "
                 f"champs={sorted(k for k in features if features.get(k))[:8]}")
        else:
            _log(f"analysis_success=false ref={ref_id} "
                 f"raison={features.get('analysis_error', '')[:160]}")
        return features

    # -- vision ---------------------------------------------------------
    @staticmethod
    def _image_b64(path: str) -> tuple[str, tuple[int, int]]:
        """Charge l'image en base64 et lit ses dimensions réelles (PNG/JPEG).

        Aucune dépendance externe : l'en-tête suffit pour les dimensions.
        """
        raw = Path(path).read_bytes()
        width = height = 0
        try:
            if raw[:8] == b"\x89PNG\r\n\x1a\n":
                width = int.from_bytes(raw[16:20], "big")
                height = int.from_bytes(raw[20:24], "big")
            elif raw[:2] == b"\xff\xd8":
                i = 2
                while i < len(raw) - 9:
                    if raw[i] != 0xFF:
                        i += 1
                        continue
                    marker = raw[i + 1]
                    if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                        height = int.from_bytes(raw[i + 5:i + 7], "big")
                        width = int.from_bytes(raw[i + 7:i + 9], "big")
                        break
                    i += 2 + int.from_bytes(raw[i + 2:i + 4], "big")
        except Exception:
            pass
        import base64
        return base64.b64encode(raw).decode("ascii"), (width, height)

    @staticmethod
    def _parse_json_block(text: str) -> dict[str, Any] | None:
        """Extrait le premier objet JSON d'une réponse de modèle."""
        cleaned = (text or "").strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            if "```" in cleaned:
                cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()
        start = cleaned.find("{")
        if start < 0:
            return None
        depth = 0
        for index in range(start, len(cleaned)):
            char = cleaned[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(cleaned[start:index + 1])
                    except Exception:
                        return None
                    return parsed if isinstance(parsed, dict) else None
        return None

    VISION_SCHEMA_PROMPT = (
        "You are analysing a 3D character reference sheet to rebuild the "
        "character in Blender. Look at the image carefully and answer ONLY "
        "with one JSON object, no prose, using exactly this shape:\n"
        "{\n"
        '  "character_style": "", "gender_presentation": "",\n'
        '  "body": {"build": "slim|athletic|heavy", "height_ratio": 0.0,\n'
        '           "shoulder_width": "narrow|average|broad", "leg_length": "short|average|long"},\n'
        '  "head": {"head_to_body_ratio": 0.0, "face_shape": "round|oval|square|long",\n'
        '           "jaw_shape": "", "cheek_volume": "low|medium|high"},\n'
        '  "eyes": {"size": "small|average|large", "shape": "", "color": "#RRGGBB", "spacing": ""},\n'
        '  "eyebrows": {"thickness": "thin|medium|thick", "color": "#RRGGBB"},\n'
        '  "nose": {"size": "small|medium|large", "shape": ""},\n'
        '  "mouth": {"width": "narrow|average|wide", "default_expression": ""},\n'
        '  "hair": {"color": "#RRGGBB", "style": "", "volume": "flat|medium|voluminous",\n'
        '           "direction": "", "length": "short|medium|long"},\n'
        '  "skin": {"tone": "#RRGGBB", "roughness": 0.0},\n'
        '  "outfit": {"shirt": {"present": true, "color": "#RRGGBB", "description": ""},\n'
        '             "overshirt": {"present": true, "color": "#RRGGBB", "description": ""},\n'
        '             "pants": {"present": true, "color": "#RRGGBB", "description": ""},\n'
        '             "belt": {"present": true, "color": "#RRGGBB", "description": ""},\n'
        '             "shoes": {"present": true, "color": "#RRGGBB", "description": ""}},\n'
        '  "palette": ["#RRGGBB"],\n'
        '  "style": {"realism": 0.0, "stylization": 0.0, "animation_movie_style": true},\n'
        '  "morphs": {\n'
        '    "head_scale": 1.07, "face_width": 0.94, "jaw_roundness": 0.82,\n'
        '    "eye_size": 1.20, "eye_spacing": 0.95, "cheek_volume": 0.78,\n'
        '    "leg_ratio": 1.05\n'
        '  }\n'
        "}\n"
        "morphs values are multipliers around 1.0 (0.7–1.3). All colours MUST "
        "be #RRGGBB hex sampled from the image. Never leave a field you can "
        "actually see empty. Do NOT answer with adjectives only."
    )

    def _extract_features(self, ref: dict[str, Any]) -> dict[str, Any]:
        """Analyse VISION réelle de l'image. Pas de features inventées."""
        features: dict[str, Any] = {
            "analysis_success": False,
            "analysis_error": "",
            "vision_provider": "",
            "vision_model": "",
            "image_dimensions": [0, 0],
        }
        source = str(ref.get("source_path") or "")
        if not source or not Path(source).is_file():
            features["analysis_error"] = f"Image introuvable : {source or '(vide)'}"
            _log(f"image_loaded=false path={source or '(vide)'}")
            return features

        status = self._core.llm.vision_status()
        _log(f"vision_provider={status.get('provider') or 'aucun'} "
             f"model={status.get('model') or '-'} available={status.get('available')}")
        if not status.get("available"):
            features["analysis_error"] = status.get("reason") or "Vision indisponible."
            return features
        features["vision_provider"] = status["provider"]
        features["vision_model"] = status["model"]

        try:
            image_b64, dimensions = self._image_b64(source)
        except Exception as exc:
            features["analysis_error"] = f"Lecture de l'image impossible : {exc}"
            _log("image_loaded=false")
            return features
        features["image_dimensions"] = list(dimensions)
        # On ne journalise JAMAIS la base64 : seulement sa taille.
        _log(f"image_loaded=true dimensions={dimensions[0]}x{dimensions[1]} "
             f"payload_kb={len(image_b64) // 1024}")

        response = self._core.llm.analyze_images(
            [image_b64], self.VISION_SCHEMA_PROMPT,
            system="You are a precise 3D character art director. Answer with JSON only.",
            temperature=0.1, max_tokens=2048, timeout=300.0)
        if not response.ok:
            features["analysis_error"] = f"Modèle de vision en échec : {response.error[:300]}"
            _log(f"analysis_success=false erreur={response.error[:200]}")
            return features

        parsed = self._parse_json_block(response.text)
        if not parsed:
            features["analysis_error"] = (
                "Le modèle de vision n'a pas renvoyé de JSON exploitable.")
            _log("analysis_success=false raison=json_invalide")
            return features

        features.update(parsed)
        features["analysis_success"] = True
        features["analysis_error"] = ""
        features["raw_analysis"] = response.text[:4000]

        # Deuxième passe, ciblée sur le visage. Sur une planche de personnage,
        # la tête n'occupe qu'une petite part de l'image : une passe dédiée,
        # formulée en CLASSIFICATION (liste fermée) plutôt qu'en échantillonnage
        # de couleur, corrige nettement l'iris et la taille des yeux.
        self._refine_face(features, image_b64)
        return features

    FACE_PROMPT = (
        "Look at the large close-up faces of this character sheet.\\n"
        "Answer ONLY with this JSON:\\n"
        '{"iris_colour": "brown|blue|green|hazel|grey|amber",\\n'
        ' "eye_size": "small|average|large",\\n'
        ' "eyebrow_thickness": "thin|medium|thick",\\n'
        ' "eyebrow_colour": "brown|black|blonde|red|grey",\\n'
        ' "mouth_expression": "neutral|smile|open smile|smirk"}\\n'
        "For eye_size, compare the eye height to the whole face height: "
        "stylised animation characters usually have LARGE eyes."
    )

    IRIS_HEX = {"brown": "#5B3A21", "blue": "#3D6EA5", "green": "#3F7A4B",
                "hazel": "#7B5C2E", "grey": "#7D858C", "amber": "#B07A2A"}
    def _refine_face(self, features: dict[str, Any], image_b64: str) -> None:
        """Affine yeux / sourcils / bouche. N'écrase jamais sur un échec."""
        response = self._core.llm.analyze_images(
            [image_b64], self.FACE_PROMPT,
            system="You are a precise character art director. Answer with JSON only.",
            temperature=0.0, max_tokens=512, timeout=300.0)
        if not response.ok:
            _log(f"face_pass=failed erreur={response.error[:120]}")
            return
        parsed = self._parse_json_block(response.text)
        if not parsed:
            _log("face_pass=failed raison=json_invalide")
            return

        eyes = dict(features.get("eyes") or {})
        iris = str(parsed.get("iris_colour") or "").strip().lower()
        if iris in self.IRIS_HEX:
            eyes["color"] = self.IRIS_HEX[iris]
            eyes["color_name"] = iris
        size = str(parsed.get("eye_size") or "").strip().lower()
        if size in {"small", "average", "large"}:
            eyes["size"] = size
        features["eyes"] = eyes

        brows = dict(features.get("eyebrows") or {})
        thickness = str(parsed.get("eyebrow_thickness") or "").strip().lower()
        if thickness in {"thin", "medium", "thick"}:
            brows["thickness"] = thickness
        brow_colour = str(parsed.get("eyebrow_colour") or "").strip().lower()
        if brow_colour:
            brows["color_name"] = brow_colour
        features["eyebrows"] = brows

        # Le modèle renvoie parfois la liste de choix au lieu d'en choisir un :
        # on n'accepte qu'une valeur du vocabulaire fermé.
        raw_expression = parsed.get("mouth_expression")
        expression = (str(raw_expression).strip().lower()
                      if isinstance(raw_expression, str) else "")
        if expression not in {"neutral", "smile", "open smile", "smirk"}:
            expression = ""
        if expression:
            mouth = dict(features.get("mouth") or {})
            mouth["default_expression"] = expression
            features["mouth"] = mouth
        features["face_pass_success"] = True
        _log(f"face_pass=ok iris={iris or '-'} eye_size={size or '-'}")

    @staticmethod
    def _default_modifications(ref_type: str, tags: list[str]) -> dict[str, str]:
        mods: dict[str, str] = {}
        if ref_type == "human_face":
            mods["face"] = "Adapter la forme du visage selon la référence"
            mods["hair"] = "Ajuster la coiffure"
        elif ref_type == "full_body":
            mods["face"] = "Adapter le visage"
            mods["hair"] = "Ajuster la coiffure"
            mods["outfit"] = "Changer les vêtements selon la référence"
            mods["proportions"] = "Ajuster les proportions"
        elif ref_type == "outfit":
            mods["outfit"] = "Remplacer les vêtements selon la référence"
            mods["colors"] = "Adapter les couleurs"
        elif ref_type == "hairstyle":
            mods["hair"] = "Changer la coiffure selon la référence"
        elif ref_type == "color_style":
            mods["colors"] = "Adapter la palette de couleurs"
            mods["materials"] = "Ajuster les matériaux"
        elif ref_type == "stylized_character":
            mods["style"] = "Adapter le style visuel"
            mods["face"] = "Adapter le visage au style"
        else:
            mods["face"] = "Adapter selon la référence"
            mods["hair"] = "Ajuster si nécessaire"
            mods["outfit"] = "Adapter si nécessaire"
            mods["colors"] = "Adapter la palette"
        return mods

    # ------------------------------------------------------------------
    # Master blend & révisions
    # ------------------------------------------------------------------
    def get_master_blend(self) -> str | None:
        """Retourne le chemin du fichier .blend maître."""
        if MASTER_BLEND.is_file():
            return str(MASTER_BLEND)
        blend_dir = DATA_DIR / "generated" / "3d"
        candidates = list(blend_dir.rglob("jarvis_avatar*.blend"))
        if candidates:
            return str(sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0])
        return None

    def create_revision(self, reference_id: str, *, blend_path: str = "",
                        evaluation: dict[str, Any] | None = None,
                        conversation_id: str = "") -> dict[str, Any]:
        """Crée un enregistrement de révision avatar."""
        rev_id = new_id("avrev")
        now = time.time()
        revision = {
            "id": rev_id,
            "reference_id": reference_id,
            "blend_path": blend_path,
            "preview_front": "",
            "preview_side": "",
            "preview_34": "",
            "preview_full": "",
            "evaluation": evaluation or {},
            "accepted": False,
            "active": False,
            "created_at": now,
        }
        self._core.db.execute(
            "INSERT INTO avatar_revisions(id, reference_id, blend_path, "
            "preview_front, preview_side, preview_34, preview_full, "
            "evaluation, accepted, active, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (rev_id, reference_id, blend_path, "", "", "", "",
             dumps({}), 0, 0, now))
        _log(f"Revision created: {rev_id} (ref={reference_id})")
        return revision

    def get_revision(self, rev_id: str) -> dict[str, Any] | None:
        row = self._core.db.one(
            "SELECT * FROM avatar_revisions WHERE id=?", (rev_id,))
        if not row:
            return None
        return self._row_to_rev(row)

    def update_revision(self, rev_id: str, **fields) -> dict[str, Any] | None:
        rev = self.get_revision(rev_id)
        if not rev:
            return None
        sets = []
        params = []
        for key in ("blend_path", "preview_front", "preview_side",
                     "preview_34", "preview_full", "accepted", "active"):
            if key in fields:
                sets.append(f"{key}=?")
                params.append(fields[key])
        if "evaluation" in fields:
            sets.append("evaluation=?")
            params.append(dumps(fields["evaluation"]))
        if sets:
            params.append(rev_id)
            self._core.db.execute(
                f"UPDATE avatar_revisions SET {', '.join(sets)} WHERE id=?", params)
        return self.get_revision(rev_id)

    def accept_revision(self, rev_id: str) -> bool:
        """Valide une révision comme avatar actif."""
        rev = self.get_revision(rev_id)
        if not rev:
            return False
        with self._lock:
            self._core.db.execute("UPDATE avatar_revisions SET active=0")
            self._core.db.execute(
                "UPDATE avatar_revisions SET accepted=1, active=1 WHERE id=?",
                (rev_id,))
        self._core.events.emit("avatar.revision_activated", {
            "revision_id": rev_id, "blend_path": rev.get("blend_path", "")})
        _log(f"Revision activated: {rev_id}")
        return True

    def rollback(self, rev_id: str) -> bool:
        """Annule l'activation d'une révision."""
        rev = self.get_revision(rev_id)
        if not rev:
            return False
        self._core.db.execute(
            "UPDATE avatar_revisions SET active=0, accepted=0 WHERE id=?", (rev_id,))
        return True

    def list_revisions(self, reference_id: str = "", limit: int = 20) -> list[dict[str, Any]]:
        if reference_id:
            rows = self._core.db.query(
                "SELECT * FROM avatar_revisions WHERE reference_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (reference_id, limit))
        else:
            rows = self._core.db.query(
                "SELECT * FROM avatar_revisions ORDER BY created_at DESC LIMIT ?",
                (limit,))
        return [self._row_to_rev(r) for r in rows]

    def active_revision(self) -> dict[str, Any] | None:
        row = self._core.db.one(
            "SELECT * FROM avatar_revisions WHERE active=1 LIMIT 1")
        return self._row_to_rev(row) if row else None

    def _row_to_rev(self, row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "reference_id": row["reference_id"],
            "blend_path": row["blend_path"],
            "preview_front": row["preview_front"],
            "preview_side": row["preview_side"],
            "preview_34": row["preview_34"],
            "preview_full": row["preview_full"],
            "evaluation": loads(row["evaluation"], {}) or {},
            "accepted": bool(row["accepted"]),
            "active": bool(row["active"]),
            "created_at": row["created_at"],
        }

    def evaluate_similarity(self, preview_path: str, reference_id: str) -> dict[str, Any]:
        """Compare RÉELLEMENT un rendu et l'image de référence, via la vision.

        Aucun score n'est fabriqué : si la vision est indisponible ou échoue,
        `available` vaut False et l'UI doit afficher « N/A ».
        """
        unavailable = {
            "available": False, "reason": "", "overall_score": None,
            "face_score": None, "hair_score": None, "outfit_score": None,
            "body_score": None, "style_score": None, "issues": [],
        }
        ref = self.get(reference_id)
        if not ref:
            unavailable["reason"] = "Référence introuvable."
            return unavailable
        source = str(ref.get("source_path") or "")
        if not source or not Path(source).is_file():
            unavailable["reason"] = "Image de référence introuvable."
            return unavailable
        if not preview_path or not Path(preview_path).is_file():
            unavailable["reason"] = "Rendu à comparer introuvable."
            return unavailable

        status = self._core.llm.vision_status()
        if not status.get("available"):
            unavailable["reason"] = status.get("reason") or "Vision indisponible."
            _log(f"evaluation available=false raison={unavailable['reason'][:120]}")
            return unavailable

        try:
            ref_b64, _ = self._image_b64(source)
            render_b64, _ = self._image_b64(preview_path)
        except Exception as exc:
            unavailable["reason"] = f"Lecture des images impossible : {exc}"
            return unavailable

        prompt = (
            "IMAGE 1 is the target character reference. IMAGE 2 is a render of "
            "a 3D avatar that is meant to reproduce it.\n"
            "Score how closely IMAGE 2 matches IMAGE 1. Be strict and honest: "
            "a crude or primitive-looking model must score low.\n"
            "Answer ONLY with this JSON:\n"
            "{\n"
            '  "face_similarity": 0, "hair_similarity": 0, "outfit_similarity": 0,\n'
            '  "body_similarity": 0, "style_similarity": 0, "overall": 0,\n'
            '  "issues": ["short actionable defect", "..."]\n'
            "}\n"
            "Each score is an integer 0-100. `issues` lists what must be fixed "
            "in IMAGE 2 to match IMAGE 1 (e.g. 'hair volume too low', "
            "'overshirt missing', 'eyes too small')."
        )
        response = self._core.llm.analyze_images(
            [ref_b64, render_b64], prompt,
            system="You are a strict 3D character supervisor. Answer with JSON only.",
            temperature=0.1, max_tokens=1024, timeout=300.0)
        if not response.ok:
            unavailable["reason"] = f"Vision en échec : {response.error[:200]}"
            _log(f"evaluation available=false erreur={response.error[:160]}")
            return unavailable

        parsed = self._parse_json_block(response.text)
        if not parsed:
            unavailable["reason"] = "Réponse d'évaluation illisible."
            return unavailable

        def score(key: str):
            value = parsed.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            return max(0, min(100, int(round(float(value)))))

        evaluation = {
            "available": True,
            "reason": "",
            "face_score": score("face_similarity"),
            "hair_score": score("hair_similarity"),
            "outfit_score": score("outfit_similarity"),
            "body_score": score("body_similarity"),
            "style_score": score("style_similarity"),
            "overall_score": score("overall"),
            "issues": [str(i)[:160] for i in (parsed.get("issues") or [])][:8],
            "vision_provider": status["provider"],
            "vision_model": status["model"],
        }
        if evaluation["overall_score"] is None:
            parts = [v for k, v in evaluation.items()
                     if k.endswith("_score") and isinstance(v, int)]
            evaluation["overall_score"] = int(sum(parts) / len(parts)) if parts else None
        _log(f"evaluation available=true overall={evaluation['overall_score']} "
             f"issues={len(evaluation['issues'])}")
        return evaluation
