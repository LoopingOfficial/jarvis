"""Atelier 3D Blender expose au modele.

Ces outils sont la SEULE facon correcte de repondre a « cree-moi un modele 3D »,
« anime ce personnage », « exporte en GLB », « fais un rendu ». Ils lancent un
vrai job Blender local, publient la progression sur le bus d'evenements, puis
renvoient des fichiers reels (.blend, .glb, preview.png) deja verifies.

Continuite : par defaut, tous les outils de MODIFICATION reprennent le projet
3D courant de la conversation. Seul `blender.create_model` part d'une scene neuve.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..blender import BlenderNotInstalled
from ..blender_sandbox import ScriptRejected, validate as validate_script
from ..permissions import SAFE_WRITE, SENSITIVE
from .base import ToolContext, ToolResult, registry

CATEGORY = "Atelier 3D"

EXPORT_FORMATS = ["glb", "gltf", "fbx", "obj", "stl", "blend"]


# ---------------------------------------------------------------------------
# Helpers communs
# ---------------------------------------------------------------------------
def _unavailable() -> ToolResult:
    return ToolResult(
        False,
        "Blender n'est pas installe ou pas detectable sur ce PC. Installe-le depuis "
        "blender.org puis indique son chemin dans Settings -> Atelier 3D. "
        "Aucun modele 3D ne peut etre produit sans lui.",
        risk=SAFE_WRITE)


def _project_required(ctx: ToolContext) -> dict[str, Any] | None:
    return ctx.core.blender.current(ctx.conversation_id)


def _no_project() -> ToolResult:
    return ToolResult(
        False,
        "Aucun modele 3D en cours dans cette conversation. Cree d'abord un modele "
        "avec blender.create_model.", risk=SAFE_WRITE)


def _summary(job: dict[str, Any]) -> str:
    """Resume FACTUEL d'un job reussi, construit uniquement sur les metadonnees."""
    meta = job.get("meta") or {}
    bits = []
    if meta.get("kind"):
        bits.append(str(meta["kind"]))
    if meta.get("polycount"):
        bits.append(f"{int(meta['polycount'])} triangles")
    if meta.get("materials"):
        bits.append(f"{len(meta['materials'])} materiau(x)")
    if meta.get("animations"):
        bits.append("animations : " + ", ".join(str(a) for a in meta["animations"][:6]))
    rig = meta.get("rig") or {}
    if rig.get("rigged"):
        bits.append(f"rig {rig.get('bones', 0)} os")
    glb = meta.get("glb") or {}
    if glb.get("name"):
        bits.append(f"{glb['name']} ({round(glb.get('size', 0) / 1024)} Ko)")
    dims = meta.get("dimensions")
    if dims:
        bits.append("dimensions " + " x ".join(str(round(float(d), 2)) for d in dims))
    return " · ".join(bits)


def _result(ctx: ToolContext, job: dict[str, Any], success_hint: str = "") -> ToolResult:
    """Traduit un job termine en resultat d'outil — jamais de faux succes."""
    if job.get("status") != "completed":
        detail = job.get("error") or "Blender n'a pas termine le job."
        return ToolResult(False, f"Le job 3D a echoue. {detail}"[:1800],
                          data={"job_id": job.get("id"), "status": job.get("status")},
                          risk=SAFE_WRITE)
    ctx.core.blender.attach_message(job["id"], "")
    facts = _summary(job)
    message = (success_hint or "Modele 3D pret.")
    if facts:
        message += f" {facts}."
    message += (f" Job {job['id']}. Le modele et son apercu sont deja affiches dans "
                f"la conversation : reponds en une phrase courte, sans redecrire le modele.")
    return ToolResult(
        True, message,
        data={"job_id": job["id"], "project_id": job.get("project_id"),
              "glb_url": job.get("glb_url"), "preview_url": job.get("preview_url"),
              "files": job.get("files"), "meta": job.get("meta")},
        risk=SAFE_WRITE,
        artifacts=[{"type": "model3d", "job_id": job["id"],
                    "glb_url": job.get("glb_url", ""),
                    "preview_url": job.get("preview_url", ""),
                    "title": job.get("title", "")}])


def _submit(ctx: ToolContext, *, action: str, title: str, settings: dict[str, Any],
            reuse: bool, timeout: int = 0) -> dict[str, Any]:
    return ctx.core.blender.submit(
        tool=f"blender.{action}", action=action, title=title, settings=settings,
        conversation_id=ctx.conversation_id, reuse_project=reuse,
        task_id=ctx.task_id, timeout=timeout)


def _run(ctx: ToolContext, *, action: str, title: str, settings: dict[str, Any],
         reuse: bool, hint: str, timeout: int = 0,
         require_project: bool = False) -> ToolResult:
    if not ctx.core.blender.available():
        return _unavailable()
    if require_project:
        current = _project_required(ctx)
        if not current or not current.get("has_blend"):
            return _no_project()
    try:
        job = _submit(ctx, action=action, title=title, settings=settings,
                      reuse=reuse, timeout=timeout)
    except BlenderNotInstalled as exc:
        return ToolResult(False, str(exc), risk=SAFE_WRITE)
    return _result(ctx, job, hint)


# ---------------------------------------------------------------------------
# blender.status
# ---------------------------------------------------------------------------
def _status(ctx: ToolContext) -> ToolResult:
    status = ctx.core.blender.status()
    if not status["available"]:
        return ToolResult(False, "Blender n'est pas detecte sur ce PC. " + status["hint"],
                          data=status)
    gpu = status.get("gpu") or {}
    current = ctx.core.blender.current(ctx.conversation_id) or {}
    lines = [
        f"Blender {status['version']} detecte ({status['executable_path']}).",
        f"Python Blender {status['python_version'] or 'inconnu'} · moteurs : "
        f"{', '.join(status['render_engines']) or 'inconnus'}.",
        ("GPU : " + gpu.get("backend", "") + " " + ", ".join(gpu.get("devices", []))
         if gpu.get("available") else "GPU : aucun peripherique Cycles detecte (rendu CPU)."),
        f"Jobs : {status['jobs']['total']} au total, {status['jobs']['running']} en cours.",
    ]
    if current.get("current_blender_project"):
        lines.append(f"Projet 3D courant : {current.get('name') or current['current_blender_project']}"
                     + (" (.blend disponible)" if current.get("has_blend") else ""))
    return ToolResult(True, "\n".join(lines), data=status)


registry.add(
    id="blender.status", name="Etat de l'atelier 3D", category=CATEGORY,
    description=("Etat REEL de Blender sur ce PC : installe ou non, version, chemin, "
                 "Python, moteurs EEVEE/Cycles, GPU, jobs en cours et projet 3D courant. "
                 "A utiliser avant de dire quoi que ce soit sur la disponibilite de Blender."),
    handler=_status, risk=SAFE_WRITE, permissions=("read",), confirmation_policy="never",
    input_schema={"type": "object", "properties": {}, "required": []},
)


# ---------------------------------------------------------------------------
# blender.create_model
# ---------------------------------------------------------------------------
def _create(ctx: ToolContext) -> ToolResult:
    args = ctx.arguments
    prompt = str(args.get("prompt") or args.get("description") or "").strip()
    kind = str(args.get("kind") or "").strip()
    if not prompt and not kind and not args.get("parts"):
        return ToolResult(False, "Precise ce qu'il faut modeliser (prompt ou kind).")
    settings: dict[str, Any] = {
        "prompt": prompt, "kind": kind,
        "options": dict(args.get("options") or {}),
        "color": str(args.get("color") or ""),
        "material": str(args.get("material") or ""),
        "join": bool(args.get("join", False)),
        "rig": bool(args.get("rig", True)),
        "animate": bool(args.get("animate", True)),
        "animations": args.get("animations") or [],
        "optimize": bool(args.get("optimize", False)),
        "optimize_target": str(args.get("optimize_target")
                               or ctx.core.settings.get("blender", "optimize_target", "web")),
        "export_formats": args.get("formats") or ["glb"],
        "name": str(args.get("name") or ""),
    }
    if args.get("parts"):
        settings["parts"] = args["parts"]
    if args.get("reference_image"):
        # Une image de reference oriente le style : silhouette, palette, materiaux.
        # Elle n'est PAS une reconstruction 3D (voir description de l'outil).
        settings["reference_image"] = str(args["reference_image"])
        settings["prompt"] = (prompt + " (reference : "
                              + Path(str(args["reference_image"])).name + ")").strip()
    title = (prompt or kind or "Modele 3D")[:120]
    return _run(ctx, action="create", title=title, settings=settings, reuse=False,
                hint="Modele 3D cree.",
                timeout=int(args.get("timeout") or 0))


registry.add(
    id="blender.create_model", name="Creer un modele 3D", category=CATEGORY,
    description=(
        "Cree un VRAI modele 3D avec Blender en local (objet, produit, meuble, logo 3D, "
        "decor, vehicule, architecture, forme procedurale, personnage humanoide de base). "
        "A utiliser des que l'utilisateur demande de creer/modeliser/faire un objet ou une "
        "scene EN 3D. N'utilise JAMAIS web.search ni image.generate pour cela. "
        "Produit un .blend, un .glb, un apercu PNG et des metadonnees verifiees. "
        "Si une image de reference est fournie, elle sert de reference de style "
        "(silhouette, proportions, couleurs) : ce n'est pas une reconstruction 3D exacte."),
    handler=_create, risk=SAFE_WRITE, permissions=("execute", "write"),
    confirmation_policy="never",
    input_schema={
        "type": "object",
        "properties": {
            "prompt": {"type": "string",
                       "description": "Description complete de l'objet a modeliser."},
            "kind": {"type": "string",
                     "description": ("Blueprint parametrique a utiliser : lamp, table, chair, "
                                     "shelf, bottle, can, cup, vase, text3d, phone, laptop, "
                                     "monitor, drone, rocket, building, tree, gear, ring, "
                                     "sword, panel, speaker, planet, column, robot, "
                                     "character, abstract. Laisser vide pour deduire du prompt.")},
            "color": {"type": "string", "description": "Couleur dominante (nom ou #RRGGBB)."},
            "material": {"type": "string",
                         "description": ("Matiere dominante : metal, chrome, gold, plastic, "
                                         "glass, wood, fabric, matte, glossy, ceramic, "
                                         "rubber, concrete, emissive, neon, skin.")},
            "options": {"type": "object",
                        "description": ("Parametres du blueprint : height, width, radius, "
                                        "floors, levels, teeth, text...")},
            "parts": {"type": "array", "items": {"type": "object"},
                      "description": ("Geometrie explicite (avancé). Chaque part : shape "
                                      "(cube/sphere/cylinder/cone/torus/plane/icosphere/"
                                      "capsule/text), name, size, scale, location, rotation, "
                                      "material, modifiers, cut_from.")},
            "rig": {"type": "boolean",
                    "description": "Rigger automatiquement si le modele est humanoide."},
            "animations": {"type": "array", "items": {"type": "string"},
                           "description": "Clips a creer d'emblee (idle, walk, wave...)."},
            "optimize": {"type": "boolean", "description": "Optimiser pour le temps reel."},
            "formats": {"type": "array", "items": {"type": "string", "enum": EXPORT_FORMATS},
                        "description": "Formats d'export. Par defaut : glb."},
            "reference_image": {"type": "string",
                                "description": "Chemin d'une image de reference de style."},
            "join": {"type": "boolean", "description": "Fusionner les pieces en un mesh."},
        },
        "required": [],
    },
)


# ---------------------------------------------------------------------------
# blender.modify_model
# ---------------------------------------------------------------------------
def _modify(ctx: ToolContext) -> ToolResult:
    args = ctx.arguments
    instruction = str(args.get("instruction") or args.get("prompt") or "").strip()
    operations = args.get("operations") or []
    if not instruction and not operations:
        return ToolResult(False, "Precise la modification a appliquer.")
    settings = {"instruction": instruction, "operations": operations,
                "export_formats": args.get("formats") or ["glb"]}
    return _run(ctx, action="modify", title=instruction[:120] or "Modification 3D",
                settings=settings, reuse=True, require_project=True,
                hint="Modele mis a jour.")


registry.add(
    id="blender.modify_model", name="Modifier le modele 3D", category=CATEGORY,
    description=(
        "Modifie le modele 3D COURANT de la conversation sans le recreer : "
        "« fais-la plus fine », « rends-le plus grand », « ajoute une lumiere cyan », "
        "« change la couleur ». Reprend automatiquement le .blend du projet en cours. "
        "Utilise cet outil, et non create_model, quand l'utilisateur parle d'un modele "
        "deja cree."),
    handler=_modify, risk=SAFE_WRITE, permissions=("execute", "write"),
    confirmation_policy="never",
    input_schema={
        "type": "object",
        "properties": {
            "instruction": {"type": "string",
                            "description": "Modification demandee, en langage naturel."},
            "operations": {
                "type": "array", "items": {"type": "object"},
                "description": ("Operations explicites (optionnel) : "
                                "{op:'scale', axis:'xy'|'z'|'all', factor:0.7}, "
                                "{op:'material', material:{preset,color}}, "
                                "{op:'light', color:'cyan'}, {op:'move', offset:[x,y,z]}, "
                                "{op:'rotate', angles:[x,y,z]}, {op:'delete', target:'nom'}, "
                                "{op:'add_parts', parts:[...]}.")},
            "formats": {"type": "array", "items": {"type": "string", "enum": EXPORT_FORMATS}},
        },
        "required": [],
    },
)


# ---------------------------------------------------------------------------
# blender.inspect
# ---------------------------------------------------------------------------
def _inspect(ctx: ToolContext) -> ToolResult:
    if not ctx.core.blender.available():
        engine = getattr(ctx.core, "avatar_engine", None)
        if engine is not None:
            result = engine.inspect(conversation_id=ctx.conversation_id)
            return ToolResult(True, "Avatar canonique inspecté (Blender absent pour la scène).",
                              data=result, risk=SAFE_WRITE)
        return _unavailable()
    current = _project_required(ctx)
    if not current or not current.get("has_blend"):
        engine = getattr(ctx.core, "avatar_engine", None)
        if engine is not None:
            result = engine.inspect(conversation_id=ctx.conversation_id)
            inv = (result.get("inventory") or {})
            identity = inv.get("identity") or {}
            lines = [
                "Aucun projet 3D de conversation — inspection de l'avatar canonique.",
                f"CURRENT : {identity.get('current_glb') or 'absent'}",
                f"CANDIDATE : {identity.get('candidate_glb') or 'aucun'}",
                f"MASTER : {identity.get('master_blend') or 'aucun'}",
                f"Source : {identity.get('source_blend') or 'base loft à générer'}",
            ]
            scene = inv.get("scene") or {}
            counts = scene.get("counts") or {}
            if counts:
                lines.append(
                    f"Scène : {counts.get('objects', 0)} objets · "
                    f"{counts.get('meshes', 0)} meshes · {counts.get('triangles', 0)} triangles")
            return ToolResult(True, "\n".join(lines), data=result, risk=SAFE_WRITE)
        return _no_project()
    job = _submit(ctx, action="inspect", title="Inspection du modele",
                  settings={"export_formats": [], "preview": True}, reuse=True)
    if job.get("status") != "completed":
        return _result(ctx, job)
    inv = (job.get("meta") or {}).get("inventory") or {}
    counts = inv.get("counts") or {}
    rig = inv.get("rig") or {}
    lines = [
        f"Projet : {current.get('name') or current['current_blender_project']}",
        f"Objets : {counts.get('objects', 0)} · meshes : {counts.get('meshes', 0)} · "
        f"triangles : {counts.get('triangles', 0)}",
        f"Materiaux : {', '.join(m['name'] for m in inv.get('materials', [])) or 'aucun'}",
        f"Textures : {len(inv.get('textures', []))} · lumieres : {len(inv.get('lights', []))}",
        f"Rig : {'oui, ' + str(rig.get('bones', 0)) + ' os' if rig.get('rigged') else 'non'}",
        f"Animations : {', '.join(a['name'] for a in inv.get('animations', [])) or 'aucune'}",
    ]
    return ToolResult(True, "\n".join(lines), data={"job_id": job["id"], "inventory": inv},
                      risk=SAFE_WRITE)


registry.add(
    id="blender.inspect", name="Inspecter le modele 3D", category=CATEGORY,
    description=("Inventaire FACTUEL du modele 3D courant : objets, polycount reel, "
                 "materiaux, textures, lumieres, rig, animations, dimensions. "
                 "A utiliser pour repondre a « combien de polygones », « est-ce rigge », "
                 "« quels materiaux »."),
    handler=_inspect, risk=SAFE_WRITE, permissions=("read",), confirmation_policy="never",
    input_schema={"type": "object", "properties": {}, "required": []},
)


# ---------------------------------------------------------------------------
# blender.material
# ---------------------------------------------------------------------------
def _material(ctx: ToolContext) -> ToolResult:
    args = ctx.arguments
    spec: dict[str, Any] = dict(args.get("material") or {})
    if args.get("preset"):
        spec["preset"] = str(args["preset"])
    for key in ("color", "roughness", "metallic", "emission", "alpha", "transmission"):
        if args.get(key) is not None:
            spec[key] = args[key]
    if not spec:
        return ToolResult(False, "Precise le materiau (preset ou parametres).")
    settings = {"material": spec, "target": str(args.get("target") or ""),
                "targets": args.get("targets") or [],
                "export_formats": ["glb"]}
    label = spec.get("preset") or spec.get("color") or "materiau"
    return _run(ctx, action="material", title=f"Materiau {label}", settings=settings,
                reuse=True, require_project=True, hint="Materiau applique.")


registry.add(
    id="blender.material", name="Appliquer un materiau", category=CATEGORY,
    description=("Applique un materiau PBR reel (Principled BSDF) au modele courant : "
                 "metal, chrome, gold, plastic, glass, wood, fabric, matte, glossy, "
                 "ceramic, rubber, concrete, emissive, neon, skin, stylized. "
                 "Repond a « ajoute un materiau metal », « rends-le noir mat »."),
    handler=_material, risk=SAFE_WRITE, permissions=("execute", "write"),
    confirmation_policy="never",
    input_schema={
        "type": "object",
        "properties": {
            "preset": {"type": "string",
                       "description": ("metal, chrome, gold, brushed_metal, plastic, glossy, "
                                       "matte, glass, wood, fabric, rubber, concrete, "
                                       "ceramic, emissive, neon, skin, stylized")},
            "color": {"type": "string", "description": "Couleur (#RRGGBB ou nom)."},
            "roughness": {"type": "number"}, "metallic": {"type": "number"},
            "emission": {"type": "number", "description": "Intensite d'emission."},
            "alpha": {"type": "number"}, "transmission": {"type": "number"},
            "target": {"type": "string", "description": "Nom de l'objet vise (sinon tous)."},
        },
        "required": [],
    },
)


# ---------------------------------------------------------------------------
# blender.texture
# ---------------------------------------------------------------------------
def _texture(ctx: ToolContext) -> ToolResult:
    args = ctx.arguments
    maps = dict(args.get("textures") or {})
    if args.get("image"):
        maps.setdefault("base_color", str(args["image"]))
    if args.get("image_job_id"):
        # Texture issue d'une generation d'image (ComfyUI / autre backend local).
        source = ctx.core.imagegen.get(str(args["image_job_id"]))
        if not source or not source.get("file_path"):
            return ToolResult(False, "Job image introuvable pour servir de texture.")
        maps.setdefault("base_color", source["file_path"])
    if not maps:
        return ToolResult(False, "Aucune texture fournie (chemin de fichier ou image_job_id).")
    missing = [p for p in maps.values() if not Path(str(p)).is_file()]
    if len(missing) == len(maps):
        return ToolResult(False, "Aucun fichier de texture lisible : "
                          + ", ".join(str(m) for m in missing))
    settings = {"textures": maps, "target": str(args.get("target") or ""),
                "scale": float(args.get("scale") or 1.0), "export_formats": ["glb"]}
    return _run(ctx, action="texture", title="Textures", settings=settings, reuse=True,
                require_project=True, hint="Textures appliquees.")


registry.add(
    id="blender.texture", name="Appliquer des textures", category=CATEGORY,
    description=("Importe et applique de VRAIES textures (PNG, JPG, EXR, HDRI) sur le "
                 "modele courant, avec UV mapping automatique. Accepte des chemins de "
                 "fichiers ou l'identifiant d'une image generee par JARVIS "
                 "(image_job_id), ce qui permet un flux ComfyUI -> Blender."),
    handler=_texture, risk=SAFE_WRITE, permissions=("execute", "write"),
    confirmation_policy="never",
    input_schema={
        "type": "object",
        "properties": {
            "image": {"type": "string", "description": "Chemin de la texture de couleur."},
            "image_job_id": {"type": "string",
                             "description": "Job d'image JARVIS a utiliser comme texture."},
            "textures": {"type": "object",
                         "description": ("Jeu complet : {base_color, roughness, metallic, "
                                         "normal, emission, alpha} -> chemins de fichiers.")},
            "target": {"type": "string"}, "scale": {"type": "number"},
        },
        "required": [],
    },
)


# ---------------------------------------------------------------------------
# blender.rig
# ---------------------------------------------------------------------------
def _rig(ctx: ToolContext) -> ToolResult:
    args = ctx.arguments
    settings = {"rebuild": bool(args.get("rebuild", False)),
                "scale": float(args.get("scale") or 1.0),
                "ik": args.get("ik") or ["arm", "leg"],
                "export_formats": ["glb"]}
    return _run(ctx, action="rig", title="Rig humanoide", settings=settings, reuse=True,
                require_project=True, hint="Rig cree.", timeout=int(args.get("timeout") or 0))


registry.add(
    id="blender.rig", name="Rigger le modele", category=CATEGORY,
    description=("Cree une VRAIE armature humanoide (spine, neck, head, clavicules, bras, "
                 "mains, jambes, pieds) avec skinning automatique et contraintes IK bras "
                 "et jambes, prete pour l'export GLTF/GLB anime. Prerequis pour les "
                 "animations full body (walk, wave, sit...)."),
    handler=_rig, risk=SAFE_WRITE, permissions=("execute", "write"),
    confirmation_policy="never",
    input_schema={
        "type": "object",
        "properties": {
            "rebuild": {"type": "boolean", "description": "Recreer le rig existant."},
            "scale": {"type": "number", "description": "Echelle du squelette (1.0 = 1.78 m)."},
            "ik": {"type": "array", "items": {"type": "string", "enum": ["arm", "leg"]}},
        },
        "required": [],
    },
)


# ---------------------------------------------------------------------------
# blender.animate
# ---------------------------------------------------------------------------
def _animate(ctx: ToolContext) -> ToolResult:
    args = ctx.arguments
    clips = args.get("clips") or ([str(args["clip"])] if args.get("clip") else [])
    if not clips:
        return ToolResult(False, "Precise l'animation a creer (clip ou clips).")
    RIG_CLIPS = {"idle", "walk", "run", "wave", "talk", "head_turn", "look_around",
                 "raise_hand", "point", "sit", "turn", "jump", "t_pose"}
    OBJECT_CLIPS = {"spin", "rotate", "turn", "twirl", "float", "pulse", "bounce", "orbit"}
    SUPPORTED = RIG_CLIPS | OBJECT_CLIPS
    unknown = [c for c in clips if str(c).strip().lower() not in SUPPORTED]
    if unknown:
        return ToolResult(
            False,
            "Animation non disponible : " + ", ".join(unknown)
            + ". Clips d'objet : " + ", ".join(sorted(OBJECT_CLIPS - {"turn"}))
            + ". Clips de personnage (nécessitent un rig, voir blender.make_rig) : "
            + ", ".join(sorted(RIG_CLIPS)), risk=SAFE_WRITE)
    settings = {"clips": [str(c).lower() for c in clips], "fps": int(args.get("fps") or 30),
                "duration": float(args.get("duration") or 0),
                "axis": str(args.get("axis") or "Z"),
                "amplitude": float(args.get("amplitude") or 1.0),
                "target": str(args.get("target") or ""),
                "replace": bool(args.get("replace", False)),
                "options": dict(args.get("options") or {}),
                "export_formats": ["glb"]}
    return _run(ctx, action="animate", title="Animation " + ", ".join(clips[:3]),
                settings=settings, reuse=True, require_project=True,
                hint="Animation creee.")


registry.add(
    id="blender.animate", name="Animer le modele", category=CATEGORY,
    description=(
        "Cree de VRAIES animations sur le modele courant, exportees dans le GLB. "
        "Clips d'objet (sans rig) : spin, rotate, turn, twirl, float, bounce, orbit, pulse. "
        "Clips de personnage (nécessitent un rig, voir blender.make_rig) : idle, walk, run, "
        "wave, talk, head_turn, look_around, raise_hand, point, sit, jump. "
        "Repond a « fais-la tourner », « fais-le marcher », « fais-lui coucou »."),
    handler=_animate, risk=SAFE_WRITE, permissions=("execute", "write"),
    confirmation_policy="never",
    input_schema={
        "type": "object",
        "properties": {
            "clip": {"type": "string",
                     "description": "Clips d'objet : spin, rotate, turn, twirl, float, bounce, "
                                    "orbit, pulse. Clips de personnage (rig) : idle, walk, run, "
                                    "wave, talk, head_turn, look_around, raise_hand, point, sit, "
                                    "turn, jump, t_pose."},
            "clips": {"type": "array", "items": {"type": "string"},
                      "description": "Plusieurs clips d'un coup (objets et/ou personnage)."},
            "duration": {"type": "number", "description": "Duree en secondes (0 = defaut)."},
            "fps": {"type": "integer"},
            "axis": {"type": "string", "enum": ["X", "Y", "Z"],
                     "description": "Axe pour les clips d'objet."},
            "amplitude": {"type": "number"},
            "target": {"type": "string", "description": "Objet ou armature vise."},
            "replace": {"type": "boolean",
                        "description": "Supprimer les animations existantes d'abord."},
            "options": {"type": "object",
                        "description": "Options du clip : side (L/R, wave/hand), angle (degres, "
                                       "turn), stride (walk/run)."},
        },
        "required": [],
    },
)


# ---------------------------------------------------------------------------
# blender.optimize
# ---------------------------------------------------------------------------
def _optimize(ctx: ToolContext) -> ToolResult:
    args = ctx.arguments
    settings = {"target_profile": str(args.get("target") or "web"),
                "lod": bool(args.get("lod", False)),
                "overrides": dict(args.get("overrides") or {}),
                "export_formats": args.get("formats") or ["glb"]}
    if not ctx.core.blender.available():
        return _unavailable()
    current = _project_required(ctx)
    if not current or not current.get("has_blend"):
        return _no_project()
    job = _submit(ctx, action="optimize", title="Optimisation "
                  + settings["target_profile"], settings=settings, reuse=True)
    if job.get("status") != "completed":
        return _result(ctx, job)
    report = (job.get("meta") or {}).get("optimize") or {}
    before = (report.get("before") or {}).get("triangles", 0)
    after = (report.get("after") or {}).get("triangles", 0)
    gain = round(100 * (1 - after / before)) if before else 0
    detail = (f"Optimisation « {settings['target_profile']} » : {before} -> {after} "
              f"triangles ({gain} % de moins), "
              f"{report.get('draw_calls_estimate', '?')} draw calls estimes.")
    result = _result(ctx, job, detail)
    return result


registry.add(
    id="blender.optimize", name="Optimiser pour le temps reel", category=CATEGORY,
    description=("Optimise REELLEMENT le modele courant pour Three.js / temps reel : "
                 "decimate vers un budget de triangles, fusion des sommets doubles, "
                 "normales corrigees, transforms appliquees, materiaux inutilises "
                 "supprimes, textures redimensionnees, geometrie cachee supprimee, "
                 "LOD optionnels. Profils : web (generaliste) ou jarvis_avatar "
                 "(preserve rig et morph targets)."),
    handler=_optimize, risk=SAFE_WRITE, permissions=("execute", "write"),
    confirmation_policy="never",
    input_schema={
        "type": "object",
        "properties": {
            "target": {"type": "string", "enum": ["web", "jarvis_avatar", "none"]},
            "lod": {"type": "boolean", "description": "Generer des niveaux de detail."},
            "overrides": {"type": "object",
                          "description": "max_triangles, texture_max, merge_distance..."},
        },
        "required": [],
    },
)


# ---------------------------------------------------------------------------
# blender.render
# ---------------------------------------------------------------------------
def _render(ctx: ToolContext) -> ToolResult:
    args = ctx.arguments
    engine = str(args.get("engine") or "cycles").lower()
    conf = ctx.core.settings.section("blender")
    settings = {
        "engine": engine,
        "samples": int(args.get("samples") or (128 if engine == "cycles" else 64)),
        "width": int(args.get("width") or 1080),
        "height": int(args.get("height") or 1080),
        "transparent": bool(args.get("transparent", False)),
        "relight": bool(args.get("relight", False)),
        "azimuth": float(args.get("azimuth") or 42.0),
        "elevation": float(args.get("elevation") or 22.0),
        "use_gpu": bool(args.get("use_gpu", conf.get("use_gpu", True))),
        "export_formats": [],
    }
    return _run(ctx, action="render", title=f"Rendu {engine}", settings=settings,
                reuse=True, require_project=True, hint="Rendu termine.",
                timeout=int(args.get("timeout") or conf.get("render_timeout_s", 1200)))


registry.add(
    id="blender.render", name="Rendre une image du modele", category=CATEGORY,
    description=("Produit un VRAI rendu du modele 3D courant. EEVEE pour un apercu "
                 "rapide, Cycles pour un rendu realiste final (GPU utilise seulement "
                 "s'il est reellement detecte). Repond a « fais-moi un rendu realiste »."),
    handler=_render, risk=SAFE_WRITE, permissions=("execute", "write"),
    confirmation_policy="never",
    input_schema={
        "type": "object",
        "properties": {
            "engine": {"type": "string", "enum": ["eevee", "cycles"]},
            "samples": {"type": "integer"}, "width": {"type": "integer"},
            "height": {"type": "integer"}, "transparent": {"type": "boolean"},
            "azimuth": {"type": "number"}, "elevation": {"type": "number"},
            "relight": {"type": "boolean", "description": "Recreer l'eclairage studio."},
        },
        "required": [],
    },
)


# ---------------------------------------------------------------------------
# blender.generate_preview
# ---------------------------------------------------------------------------
def _preview(ctx: ToolContext) -> ToolResult:
    args = ctx.arguments
    settings = {"preview": True, "export_formats": [],
                "preview_size": int(args.get("size") or 720),
                "preview_engine": str(args.get("engine") or "eevee"),
                "preview_transparent": bool(args.get("transparent", False))}
    return _run(ctx, action="preview", title="Apercu 3D", settings=settings, reuse=True,
                require_project=True, hint="Apercu genere.")


registry.add(
    id="blender.generate_preview", name="Generer un apercu", category=CATEGORY,
    description=("Regenere l'image d'apercu du modele 3D courant (EEVEE, cadrage "
                 "automatique). Rapide, a utiliser pour rafraichir la vignette."),
    handler=_preview, risk=SAFE_WRITE, permissions=("execute", "write"),
    confirmation_policy="never",
    input_schema={"type": "object",
                  "properties": {"size": {"type": "integer"},
                                 "engine": {"type": "string", "enum": ["eevee", "cycles"]},
                                 "transparent": {"type": "boolean"}},
                  "required": []},
)


# ---------------------------------------------------------------------------
# blender.export
# ---------------------------------------------------------------------------
def _export(ctx: ToolContext) -> ToolResult:
    args = ctx.arguments
    formats = args.get("formats") or [str(args.get("format") or "glb")]
    formats = [str(f).lower().lstrip(".") for f in formats]
    unknown = [f for f in formats if f not in EXPORT_FORMATS]
    if unknown:
        return ToolResult(False, "Format non supporte : " + ", ".join(unknown)
                          + ". Formats disponibles : " + ", ".join(EXPORT_FORMATS))
    settings = {"formats": formats, "export_formats": formats,
                "export_animations": bool(args.get("animations", True)),
                "export_textures": bool(args.get("textures", True)),
                "draco": bool(args.get("draco", False)),
                "preview": False}
    if not ctx.core.blender.available():
        return _unavailable()
    current = _project_required(ctx)
    if not current or not current.get("has_blend"):
        return _no_project()
    job = _submit(ctx, action="export", title="Export " + ", ".join(formats),
                  settings=settings, reuse=True)
    if job.get("status") != "completed":
        return _result(ctx, job)
    exports = (job.get("meta") or {}).get("exports") or []
    ok = [e for e in exports if e.get("ok")]
    detail = "Export reussi : " + ", ".join(
        f"{e['name']} ({round(e.get('size', 0) / 1024)} Ko)" for e in ok)
    return _result(ctx, job, detail)


registry.add(
    id="blender.export", name="Exporter le modele", category=CATEGORY,
    description=("Exporte le modele 3D courant et VERIFIE le fichier produit (le GLB est "
                 "relu : maillages, materiaux et animations sont comptes). "
                 "Formats : glb (defaut), gltf, fbx, obj, stl, blend. "
                 "Repond a « exporte-le en GLB »."),
    handler=_export, risk=SAFE_WRITE, permissions=("execute", "write"),
    confirmation_policy="never",
    input_schema={
        "type": "object",
        "properties": {
            "format": {"type": "string", "enum": EXPORT_FORMATS},
            "formats": {"type": "array", "items": {"type": "string", "enum": EXPORT_FORMATS}},
            "animations": {"type": "boolean"}, "textures": {"type": "boolean"},
            "draco": {"type": "boolean", "description": "Compression Draco (glTF)."},
        },
        "required": [],
    },
)


# ---------------------------------------------------------------------------
# blender.import / blender.convert
# ---------------------------------------------------------------------------
def _import(ctx: ToolContext) -> ToolResult:
    args = ctx.arguments
    source = str(args.get("path") or args.get("source") or "").strip()
    if not source:
        return ToolResult(False, "Precise le fichier 3D a importer.")
    if not Path(source).is_file():
        return ToolResult(False, f"Fichier introuvable : {source}")
    into = bool(args.get("into_current", True))
    return _run(ctx, action="import", title="Import " + Path(source).name,
                settings={"source": source, "export_formats": ["glb"]},
                reuse=into, hint="Fichier importe.")


registry.add(
    id="blender.import", name="Importer un fichier 3D", category=CATEGORY,
    description=("Importe un vrai fichier 3D existant (GLB, GLTF, FBX, OBJ, STL, DAE, "
                 "PLY, BLEND) dans le projet courant ou dans un nouveau projet."),
    handler=_import, risk=SAFE_WRITE, permissions=("execute", "read"),
    confirmation_policy="never",
    input_schema={"type": "object",
                  "properties": {"path": {"type": "string"},
                                 "into_current": {"type": "boolean"}},
                  "required": ["path"]},
)


def _convert(ctx: ToolContext) -> ToolResult:
    args = ctx.arguments
    source = str(args.get("source") or args.get("path") or "").strip()
    if not source or not Path(source).is_file():
        return ToolResult(False, f"Fichier source introuvable : {source or '(vide)'}")
    formats = args.get("formats") or [str(args.get("format") or "glb")]
    settings = {"source": source, "formats": formats, "export_formats": formats,
                "optimize": bool(args.get("optimize", False)),
                "target_profile": str(args.get("target") or "web")}
    return _run(ctx, action="convert",
                title=f"Conversion {Path(source).name} -> {', '.join(formats)}",
                settings=settings, reuse=False, hint="Conversion terminee.")


registry.add(
    id="blender.convert", name="Convertir un fichier 3D", category=CATEGORY,
    description=("Convertit un fichier 3D d'un format vers un autre via Blender "
                 "(FBX -> GLB, OBJ -> GLB, STL -> GLB...), avec optimisation optionnelle "
                 "pour le web."),
    handler=_convert, risk=SAFE_WRITE, permissions=("execute", "write"),
    confirmation_policy="never",
    input_schema={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Fichier a convertir."},
            "format": {"type": "string", "enum": EXPORT_FORMATS},
            "formats": {"type": "array", "items": {"type": "string", "enum": EXPORT_FORMATS}},
            "optimize": {"type": "boolean"},
            "target": {"type": "string", "enum": ["web", "jarvis_avatar", "none"]},
        },
        "required": ["source"],
    },
)


# ---------------------------------------------------------------------------
# blender.run_script  (sandbox)
# ---------------------------------------------------------------------------
def _run_script(ctx: ToolContext) -> ToolResult:
    if not ctx.core.settings.get("blender", "allow_run_script", True):
        return ToolResult(False, "L'execution de scripts Blender est desactivee "
                                 "dans Settings -> Atelier 3D.")
    code = str(ctx.arguments.get("code") or "").strip()
    if not code:
        return ToolResult(False, "Aucun script fourni.")
    from ..blender import BLENDER_ROOT

    try:
        validate_script(code, BLENDER_ROOT)
    except ScriptRejected as exc:
        return ToolResult(False, str(exc), risk=SENSITIVE)
    settings = {"code": code, "export_formats": ctx.arguments.get("formats") or ["glb"]}
    return _run(ctx, action="run_script",
                title=str(ctx.arguments.get("title") or "Script Blender")[:100],
                settings=settings, reuse=bool(ctx.arguments.get("use_current", True)),
                hint="Script Blender execute.",
                timeout=int(ctx.arguments.get("timeout") or 0))


registry.add(
    id="blender.run_script", name="Executer un script Blender", category=CATEGORY,
    description=(
        "Execute un script Python bpy dans Blender pour une geometrie que les autres "
        "outils ne couvrent pas. Le script tourne en sandbox : imports systeme, reseau, "
        "eval/exec et ecriture hors de l'atelier 3D sont refuses AVANT execution. "
        "Variables disponibles : bpy, math, builder, materials, modifiers, lighting, "
        "rigging, animation, optimize, textures, blueprints, out(), progress(). "
        "Prefere les outils dedies quand ils suffisent."),
    handler=_run_script, risk=SENSITIVE, permissions=("execute", "write"),
    confirmation_policy="auto",
    dangerous_hint="Un script Blender arbitraire va etre execute (sandbox atelier 3D).",
    input_schema={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Script Python bpy."},
            "title": {"type": "string"},
            "use_current": {"type": "boolean",
                            "description": "Partir du modele courant (defaut) ou d'une scene vide."},
        },
        "required": ["code"],
    },
)


# ---------------------------------------------------------------------------
# blender.job_status / blender.cancel_job
# ---------------------------------------------------------------------------
def _job_status(ctx: ToolContext) -> ToolResult:
    job_id = str(ctx.arguments.get("job_id") or "").strip()
    if job_id:
        job = ctx.core.blender.get(job_id)
        if not job:
            return ToolResult(False, f"Job 3D inconnu : {job_id}")
        line = (f"Job {job['id']} · {job['action']} · {job['status']} · "
                f"{job['stage_label'] or job['stage']} · "
                f"{round(float(job['progress']) * 100)} %")
        if job.get("error"):
            line += f"\nErreur : {job['error'][:400]}"
        return ToolResult(True, line, data=job)
    jobs = ctx.core.blender.list(ctx.conversation_id, limit=int(ctx.arguments.get("limit") or 10))
    if not jobs:
        return ToolResult(True, "Aucun job 3D dans cette conversation.", data={"jobs": []})
    lines = [f"{j['id']} · {j['action']} · {j['status']} · "
             f"{round(float(j['progress']) * 100)} %" for j in jobs]
    return ToolResult(True, "\n".join(lines), data={"jobs": jobs})


registry.add(
    id="blender.job_status", name="Etat d'un job 3D", category=CATEGORY,
    description=("Etat reel d'un job Blender (queued, running, completed, failed, "
                 "cancelled), son etape et sa progression. Sans job_id, liste les jobs "
                 "de la conversation."),
    handler=_job_status, risk=SAFE_WRITE, permissions=("read",), confirmation_policy="never",
    input_schema={"type": "object",
                  "properties": {"job_id": {"type": "string"}, "limit": {"type": "integer"}},
                  "required": []},
)


def _cancel_job(ctx: ToolContext) -> ToolResult:
    job_id = str(ctx.arguments.get("job_id") or "").strip()
    if not job_id:
        running = [j for j in ctx.core.blender.list(ctx.conversation_id, limit=20)
                   if j["status"] in {"queued", "running"}]
        if not running:
            return ToolResult(False, "Aucun job 3D en cours a annuler.")
        job_id = running[0]["id"]
    job = ctx.core.blender.cancel(job_id)
    if not job:
        return ToolResult(False, f"Job 3D inconnu : {job_id}")
    if job["status"] == "cancelled":
        return ToolResult(True, f"Job 3D {job_id} annule.", data=job, risk=SAFE_WRITE)
    return ToolResult(job["status"] == "completed",
                      f"Job {job_id} : {job['status']} (annulation trop tardive).",
                      data=job, risk=SAFE_WRITE)


registry.add(
    id="blender.cancel_job", name="Annuler un job 3D", category=CATEGORY,
    description="Annule un job Blender en cours (arrete reellement le processus).",
    handler=_cancel_job, risk=SAFE_WRITE, permissions=("execute",),
    confirmation_policy="never",
    input_schema={"type": "object", "properties": {"job_id": {"type": "string"}},
                  "required": []},
)
