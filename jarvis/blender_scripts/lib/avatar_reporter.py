"""AvatarJobReporter — publication temps reel de l'avancement Blender.

Execute DANS Blender. Le reporter ecrit un flux d'evenements NDJSON que
`jarvis/avatar_live.py` lit en continu cote hote, et produit les artefacts
« live » que le viewer 3D d'Avatar Studio recharge :

    <job_dir>/live/current.glb        GLB de l'etat courant (ecriture atomique)
    <job_dir>/live/front.png          rendus 4 angles fixes
    <job_dir>/live/three_quarter.png
    <job_dir>/live/side.png
    <job_dir>/live/full_body.png
    <job_dir>/live/events.ndjson      flux d'evenements (append-only)
    <job_dir>/live/state.json         derniere version publiee
    <job_dir>/revisions/<n>/          copie figee de chaque snapshot

Regles :
  * aucun evenement n'est publie avant que le fichier correspondant soit
    reellement ecrit ET renomme (pas de lecture d'un GLB en cours d'ecriture) ;
  * les snapshots sont throttles (pas d'export toutes les 100 ms) ;
  * `check_control()` honore pause / cancel / ajustements utilisateur, mais
    uniquement aux points surs ou l'appelant le demande.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import time

# Moteurs de rendu candidats par mode qualite (le premier disponible gagne).
_ENGINE_CANDIDATES = {
    "low": ("BLENDER_WORKBENCH",),
    "balanced": ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"),
    "high": ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"),
}

# Intervalle minimal entre deux snapshots complets, par mode qualite.
_THROTTLE_S = {"low": 6.0, "balanced": 2.5, "high": 1.5}

# Rendus 4 angles : off en Low (GLB seul), on sinon.
_RENDER_VIEWS = {"low": False, "balanced": True, "high": True}

_RES = {"low": 384, "balanced": 512, "high": 768}

# (nom, azimut degres autour de Z, hauteur relative, cadrage) — avatar face a -Y.
VIEW_CAMERAS = (
    ("front", 0.0, 0.86, "bust"),
    ("three_quarter", 38.0, 0.86, "bust"),
    ("side", 90.0, 0.86, "bust"),
    ("full_body", 12.0, 0.52, "full"),
)


class JobCancelled(Exception):
    """Le job a ete annule par l'utilisateur."""


class AvatarJobReporter:
    """Publie l'avancement reel du pipeline avatar vers l'hote JARVIS."""

    def __init__(self, job_dir, job_id="", quality="balanced",
                 stages=None, reference_id=""):
        self.job_dir = job_dir
        self.job_id = job_id
        self.quality = quality if quality in _ENGINE_CANDIDATES else "balanced"
        self.reference_id = reference_id
        self.live_dir = os.path.join(job_dir, "live")
        self.revisions_dir = os.path.join(job_dir, "revisions")
        for d in (self.live_dir, self.revisions_dir):
            os.makedirs(d, exist_ok=True)
        self.events_path = os.path.join(self.live_dir, "events.ndjson")
        self.state_path = os.path.join(self.live_dir, "state.json")
        self.control_path = os.path.join(self.live_dir, "control.json")

        self.stages = list(stages or [])
        self.current_stage = ""
        self.completed_stages = []
        self.version = 0
        self.seq = 0
        self.blend_revision = 0
        self._last_snapshot = 0.0
        self._cameras = {}
        self._adjustments = []
        self._heartbeat = 0.0

    # ------------------------------------------------------------------ flux
    def _emit(self, event_type, **payload):
        self.seq += 1
        record = {
            "seq": self.seq,
            "type": event_type,
            "ts": time.time(),
            "job_id": self.job_id,
            "stage": self.current_stage,
        }
        record.update(payload)
        try:
            with open(self.events_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except Exception:
            pass

    def heartbeat(self, message=""):
        """Signal de vie : l'hote detecte un Blender bloque sans lui."""
        now = time.time()
        if now - self._heartbeat < 2.0:
            return
        self._heartbeat = now
        self._emit("avatar.heartbeat", message=str(message)[:200])

    # ---------------------------------------------------------------- etapes
    def stage(self, name, progress=0.0, message=""):
        """Declare l'etape courante et sa progression interne (0..1)."""
        progress = max(0.0, min(1.0, float(progress)))
        if name != self.current_stage:
            self.current_stage = name
            self._emit("avatar.stage.started", stage=name, message=str(message)[:200])
        self._emit("avatar.stage.progress", stage=name,
                   stage_progress=progress, message=str(message)[:200])
        self.heartbeat(message)

    def operation(self, message):
        """Operation precise en cours (« Modification de la forme du nez »)."""
        self._emit("avatar.operation", message=str(message)[:200])
        self.heartbeat(message)

    def stage_complete(self, name, message=""):
        if name not in self.completed_stages:
            self.completed_stages.append(name)
        self._emit("avatar.stage.completed", stage=name, message=str(message)[:200],
                   completed_stages=list(self.completed_stages))

    def stage_skipped(self, name, reason=""):
        self._emit("avatar.stage.skipped", stage=name, message=str(reason)[:200])

    def warn(self, message):
        self._emit("avatar.warning", message=str(message)[:300])

    # ------------------------------------------------------------- controles
    def _read_control(self):
        try:
            with open(self.control_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def check_control(self, allow_pause=True):
        """Point sur : applique cancel / pause et renvoie les ajustements neufs.

        Ne doit jamais etre appele pendant une ecriture de fichier.
        """
        control = self._read_control()
        if control.get("cancel"):
            self._emit("avatar.control.cancelled")
            raise JobCancelled("Annule par l'utilisateur.")
        paused_announced = False
        while allow_pause and control.get("pause"):
            if not paused_announced:
                paused_announced = True
                self._emit("avatar.control.paused")
            time.sleep(0.5)
            self.heartbeat("En pause")
            control = self._read_control()
            if control.get("cancel"):
                self._emit("avatar.control.cancelled")
                raise JobCancelled("Annule par l'utilisateur.")
        if paused_announced:
            self._emit("avatar.control.resumed")

        pending = control.get("adjustments") or []
        known = set()
        for x in self._adjustments:
            known.add(x.get("id"))
        fresh = [a for a in pending if a.get("id") not in known]
        if fresh:
            self._adjustments.extend(fresh)
            for adj in fresh:
                self._emit("avatar.adjustment.applied",
                           message=str(adj.get("text") or "")[:200],
                           adjustment_id=adj.get("id", ""))
        return fresh

    def adjustments(self):
        """Tous les ajustements utilisateur deja acceptes."""
        return list(self._adjustments)

    # ------------------------------------------------------------- snapshots
    def snapshot(self, stage="", force=False, renders=None, label=""):
        """Exporte l'etat courant : GLB live + rendus 4 angles + evenements.

        Throttle : sans `force`, un snapshot trop proche du precedent est
        ignore (retourne {"skipped": True}).
        """
        now = time.time()
        if not force and (now - self._last_snapshot) < _THROTTLE_S[self.quality]:
            return {"skipped": True, "reason": "throttled"}
        self._last_snapshot = now
        stage = stage or self.current_stage
        self.version += 1
        version = self.version
        rev_dir = os.path.join(self.revisions_dir, str(version))
        os.makedirs(rev_dir, exist_ok=True)

        result = {"version": version, "stage": stage, "glb": "", "renders": {}}

        # --- GLB live (ecriture atomique) ---------------------------------
        self.operation("Export de l'aperçu 3D")
        tmp_glb = os.path.join(self.live_dir, ".current.%d.tmp.glb" % version)
        final_glb = os.path.join(self.live_dir, "current.glb")
        if self._export_glb(tmp_glb):
            try:
                os.replace(tmp_glb, final_glb)
                shutil.copy2(final_glb, os.path.join(rev_dir, "model.glb"))
                result["glb"] = final_glb
                self._write_state(version, stage)
                self._emit("avatar.preview.glb", version=version, stage=stage,
                           label=label or stage, size=os.path.getsize(final_glb),
                           blend_revision=self.blend_revision,
                           reference_id=self.reference_id)
            except Exception as exc:
                self.warn("Swap GLB impossible : %s" % exc)
        else:
            self.warn("Export GLB v%d echoue — version precedente conservee" % version)

        # --- rendus 4 angles ----------------------------------------------
        want_renders = _RENDER_VIEWS[self.quality] if renders is None else bool(renders)
        if want_renders:
            rendered = self._render_views(rev_dir, version)
            result["renders"] = rendered
            if rendered:
                self._emit("avatar.preview.render", version=version, stage=stage,
                           views=sorted(rendered.keys()), label=label or stage)
        return result

    def _write_state(self, version, stage):
        payload = {
            "version": version, "stage": stage, "ts": time.time(),
            "job_id": self.job_id, "blend_revision": self.blend_revision,
            "reference_id": self.reference_id,
            "completed_stages": list(self.completed_stages),
        }
        tmp = self.state_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp, self.state_path)
        except Exception:
            pass

    # ----------------------------------------------------------------- export
    def _export_glb(self, path):
        import bpy
        base = {
            "filepath": path,
            "export_format": 'GLB',
            "use_selection": False,
            # Live = leger. Les modificateurs ne sont pas appliques et les clips
            # d'animation ne sont PAS exportes : ils representaient l'essentiel
            # du cout de l'export (l'apercu doit montrer la forme, pas jouer les
            # animations). Le squelette reste present via le skinning, et
            # l'export final, lui, embarque tout.
            "export_apply": False,
            "export_animations": False,
            "export_materials": 'EXPORT',
            "export_yup": True,
        }
        for kwargs in (base, {"filepath": path, "export_format": 'GLB'}):
            try:
                bpy.ops.export_scene.gltf(**kwargs)
                if os.path.isfile(path) and os.path.getsize(path) > 64:
                    return True
            except Exception:
                continue
        return False

    # ---------------------------------------------------------------- rendus
    @staticmethod
    def _pick_engine(candidates):
        import bpy
        available = set()
        try:
            items = bpy.types.RenderSettings.bl_rna.properties['engine'].enum_items
            available = set(i.identifier for i in items)
        except Exception:
            pass
        for name in candidates:
            if not available or name in available:
                return name
        return 'BLENDER_WORKBENCH'

    @staticmethod
    def _subject_bounds():
        """Boite englobante monde des meshes visibles (ignore cameras/lampes)."""
        import bpy
        from mathutils import Vector
        lo = Vector((1e9, 1e9, 1e9))
        hi = Vector((-1e9, -1e9, -1e9))
        found = False
        for obj in bpy.data.objects:
            if obj.type != 'MESH' or obj.hide_render:
                continue
            for corner in obj.bound_box:
                world = obj.matrix_world @ Vector(corner)
                found = True
                for i in range(3):
                    lo[i] = min(lo[i], world[i])
                    hi[i] = max(hi[i], world[i])
        if not found:
            return Vector((0.0, 0.0, 0.0)), Vector((1.0, 1.0, 1.8))
        return lo, hi

    def _ensure_render_scene(self):
        """Cree une fois les 4 cameras fixes + l'eclairage d'apercu, puis recadre."""
        import bpy
        from mathutils import Vector
        lo, hi = self._subject_bounds()
        center = (lo + hi) / 2.0
        height = max(0.2, hi.z - lo.z)
        width = max(0.2, max(hi.x - lo.x, hi.y - lo.y))

        if not self._cameras:
            for name, azimuth, height_ratio, framing in VIEW_CAMERAS:
                cam_data = bpy.data.cameras.new("JARVIS_LIVE_" + name)
                cam_data.lens = 50 if framing == "bust" else 35
                cam = bpy.data.objects.new("JARVIS_LIVE_" + name, cam_data)
                bpy.context.scene.collection.objects.link(cam)
                target = bpy.data.objects.new("JARVIS_LIVE_T_" + name, None)
                bpy.context.scene.collection.objects.link(target)
                constraint = cam.constraints.new(type='TRACK_TO')
                constraint.target = target
                constraint.track_axis = 'TRACK_NEGATIVE_Z'
                constraint.up_axis = 'UP_Y'
                self._cameras[name] = (cam, target, azimuth, height_ratio, framing)

            # Lumiere d'apercu dediee : le live reste lisible meme sans lampes.
            if not any(o.type == 'LIGHT' for o in bpy.data.objects):
                light_data = bpy.data.lights.new("JARVIS_LIVE_KEY", type='AREA')
                light_data.energy = 320.0
                light_data.size = 4.0
                light = bpy.data.objects.new("JARVIS_LIVE_KEY", light_data)
                bpy.context.scene.collection.objects.link(light)
                light.location = (center.x + 2.5, center.y - 3.0, center.z + 2.5)

        # Recadre a chaque snapshot : le modele grandit pendant le job.
        for name in self._cameras:
            cam, target, azimuth, height_ratio, framing = self._cameras[name]
            focus_z = lo.z + height * height_ratio
            if framing == "full":
                # 35 mm, rendu carre : ~1.25 x la hauteur suffit a cadrer le corps.
                distance = max(1.2, height * 1.25 + width * 0.15)
            else:
                # Buste : cadrage serre sur tete + epaules, pas sur le corps entier.
                distance = max(0.45, height * 0.40 + width * 0.30)
            angle = math.radians(azimuth)
            target.location = Vector((center.x, center.y, focus_z))
            cam.location = Vector((
                center.x + distance * math.sin(angle),
                center.y - distance * math.cos(angle),
                focus_z + height * 0.06,
            ))

        scene = bpy.context.scene
        engine = self._pick_engine(_ENGINE_CANDIDATES[self.quality])
        scene.render.engine = engine
        size = _RES[self.quality]
        scene.render.resolution_x = size
        scene.render.resolution_y = size
        scene.render.resolution_percentage = 100
        scene.render.film_transparent = True
        scene.render.image_settings.file_format = 'PNG'
        scene.render.image_settings.color_mode = 'RGBA'
        if engine.startswith('BLENDER_EEVEE'):
            try:
                scene.eevee.taa_render_samples = 32 if self.quality == "high" else 16
            except Exception:
                pass
        elif engine == 'BLENDER_WORKBENCH':
            try:
                scene.display.render_aa = 'FXAA'
                scene.display.shading.light = 'STUDIO'
                scene.display.shading.color_type = 'MATERIAL'
            except Exception:
                pass

    def _render_views(self, rev_dir, version):
        import bpy
        try:
            self._ensure_render_scene()
        except Exception as exc:
            self.warn("Scene de rendu live indisponible : %s" % exc)
            return {}
        scene = bpy.context.scene
        previous_camera = scene.camera
        out = {}
        for name in self._cameras:
            cam = self._cameras[name][0]
            self.operation("Rendu de l'aperçu %s" % name.replace("_", " "))
            tmp = os.path.join(self.live_dir, ".%s.%d.tmp.png" % (name, version))
            scene.camera = cam
            scene.render.filepath = tmp
            try:
                bpy.ops.render.render(write_still=True)
            except Exception as exc:
                self.warn("Rendu %s echoue : %s" % (name, exc))
                continue
            if not os.path.isfile(tmp):
                continue
            try:
                final = os.path.join(self.live_dir, "%s.png" % name)
                os.replace(tmp, final)
                shutil.copy2(final, os.path.join(rev_dir, "%s.png" % name))
                out[name] = final
            except Exception as exc:
                self.warn("Publication du rendu %s impossible : %s" % (name, exc))
        scene.camera = previous_camera
        return out

    # ------------------------------------------------------------- sauvegarde
    def save_blend(self, path):
        """Sauvegarde le .blend courant (revision incrementee)."""
        import bpy
        tmp = path + ".tmp.blend"
        try:
            bpy.ops.wm.save_as_mainfile(filepath=tmp, copy=True)
            os.replace(tmp, path)
            self.blend_revision += 1
            return True
        except Exception as exc:
            self.warn("Sauvegarde .blend impossible : %s" % exc)
            return False
