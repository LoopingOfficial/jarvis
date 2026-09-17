"""Pipeline avatar LIVE — execute dans Blender, publie chaque etape reellement.

Lance par `jarvis/avatar_live.py` (pas par job.py) :

    blender --background --factory-startup --python avatar_live_job.py -- <settings.json>

Chaque grande fonction recoit le `AvatarJobReporter` et publie :
  * l'etape en cours + l'operation precise,
  * un snapshot (GLB live + rendus 4 angles) une fois l'etape reellement faite,
  * son achevement — ou son abandon explicite si la scene n'a rien a modifier.

Aucun evenement n'est emis pour une operation qui n'a pas eu lieu.
"""
from __future__ import annotations

import json
import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

import reference_pixels  # noqa: E402
from avatar_reporter import AvatarJobReporter, JobCancelled  # noqa: E402

import bpy  # noqa: E402


class BaseMeshMissing(Exception):
    """Aucune base humanoide exploitable : on refuse de generer (exigence 51)."""


# --------------------------------------------------------------------- outils
def _settings() -> dict:
    path = os.environ.get("JARVIS_AVATAR_JOB", "").strip()
    if not path and "--" in sys.argv:
        tail = [a for a in sys.argv[sys.argv.index("--") + 1:] if a]
        path = tail[-1] if tail else ""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


NAMED_COLORS = {
    "noir": (0.02, 0.02, 0.02), "black": (0.02, 0.02, 0.02),
    "blanc": (0.95, 0.95, 0.95), "white": (0.95, 0.95, 0.95),
    "rouge": (0.8, 0.1, 0.1), "red": (0.8, 0.1, 0.1),
    "bleu": (0.1, 0.2, 0.8), "blue": (0.1, 0.2, 0.8),
    "vert": (0.1, 0.7, 0.2), "green": (0.1, 0.7, 0.2),
    "jaune": (0.95, 0.85, 0.1), "yellow": (0.95, 0.85, 0.1),
    "orange": (0.9, 0.5, 0.1), "violet": (0.5, 0.1, 0.8),
    "purple": (0.5, 0.1, 0.8), "rose": (0.9, 0.4, 0.6), "pink": (0.9, 0.4, 0.6),
    "gris": (0.5, 0.5, 0.5), "gray": (0.5, 0.5, 0.5), "grey": (0.5, 0.5, 0.5),
    "brun": (0.4, 0.25, 0.1), "brown": (0.4, 0.25, 0.1),
    "chatain": (0.4, 0.25, 0.1), "roux": (0.7, 0.3, 0.1),
    "blond": (0.85, 0.75, 0.4), "cyan": (0.1, 0.8, 0.9),
    "dore": (0.85, 0.65, 0.1), "gold": (0.85, 0.65, 0.1),
    "argent": (0.75, 0.75, 0.8), "silver": (0.75, 0.75, 0.8),
}


def parse_color(value):
    """'#RRGGBB' ou nom connu -> (r, g, b, 1.0). None si non interpretable."""
    if not value:
        return None
    s = str(value).strip()
    if s.startswith("#") and len(s) == 7:
        try:
            return (int(s[1:3], 16) / 255.0, int(s[3:5], 16) / 255.0,
                    int(s[5:7], 16) / 255.0, 1.0)
        except ValueError:
            return None
    rgb = NAMED_COLORS.get(s.lower())
    return (rgb[0], rgb[1], rgb[2], 1.0) if rgb else None


def meshes():
    return [o for o in bpy.data.objects if o.type == 'MESH']


def find_by_keywords(keywords):
    out = []
    for obj in meshes():
        low = obj.name.lower()
        for mat in (obj.data.materials or []):
            if mat:
                low += " " + mat.name.lower()
        if any(k in low for k in keywords):
            out.append(obj)
    return out


def body_object():
    """Le mesh le plus volumineux : le corps."""
    best, best_volume = None, -1.0
    for obj in meshes():
        dims = obj.dimensions
        volume = max(0.0, dims.x) * max(0.0, dims.y) * max(0.0, dims.z)
        if volume > best_volume:
            best, best_volume = obj, volume
    return best


def set_base_color(material, color):
    """Applique une couleur de base reelle (nodes ou diffuse legacy)."""
    if not material:
        return False
    if material.use_nodes and material.node_tree:
        for node in material.node_tree.nodes:
            if node.type in {'BSDF_PRINCIPLED', 'BSDF_DIFFUSE', 'EMISSION'}:
                key = 'Base Color' if 'Base Color' in node.inputs else 'Color'
                if key in node.inputs:
                    node.inputs[key].default_value = color
                    material.diffuse_color = color
                    return True
    material.diffuse_color = color
    return True


def ensure_material(obj, name, color):
    """Garantit un materiau nomme sur l'objet et lui donne la couleur voulue."""
    for mat in (obj.data.materials or []):
        if mat and name.lower() in mat.name.lower():
            set_base_color(mat, color)
            return mat
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    set_base_color(mat, color)
    obj.data.materials.append(mat)
    return mat


def head_bounds(obj):
    """Z du sommet et Z de base de la zone tete (haut ~18 % du corps)."""
    from mathutils import Vector
    zs = [(obj.matrix_world @ Vector(c)).z for c in obj.bound_box]
    lo, hi = min(zs), max(zs)
    return lo + (hi - lo) * 0.82, hi


# ------------------------------------------------------------------- outils
# Le master contient un VRAI personnage : corps, tete, yeux (sclere/iris/
# pupilles), sourcils, bouche, cheveux, chemise, surchemise, pantalon,
# ceinture, chaussures, mains, 55 os et 36 shape keys (visemes inclus).
# Les etapes ci-dessous MODIFIENT ces objets. Elles ne construisent jamais un
# humain a partir de primitives.
REQUIRED_PARTS = ("JARVIS_Head", "JARVIS_Skin", "JARVIS_Hands", "JARVIS_Hair",
                  "JARVIS_Eyes", "JARVIS_Iris", "JARVIS_Shirt", "JARVIS_Pants")

GARMENTS = (
    ("shirt", "JARVIS_Shirt"),
    ("overshirt", "JARVIS_Jacket"),
    ("pants", "JARVIS_Pants"),
    ("belt", "JARVIS_Belt"),
    ("shoes", "JARVIS_Shoes"),
)

SKIN_MATERIALS = ("Skin", "SkinFace", "SkinBody", "SkinHands")


def obj(name):
    return bpy.data.objects.get(name)


def bbox_centre(objects):
    """Centre de la boite englobante monde d'un ou plusieurs objets."""
    from mathutils import Vector
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    found = False
    for o in objects:
        if o is None or o.type != 'MESH':
            continue
        for corner in o.bound_box:
            world = o.matrix_world @ Vector(corner)
            found = True
            for i in range(3):
                lo[i] = min(lo[i], world[i])
                hi[i] = max(hi[i], world[i])
    if not found:
        return Vector((0.0, 0.0, 0.0))
    return (lo + hi) / 2.0


def scale_mesh(o, factors, pivot=None):
    """Met a l'echelle les SOMMETS autour d'un pivot.

    Les objets du master sont a l'origine avec des coordonnees deja en place :
    utiliser `obj.scale` mettrait a l'echelle depuis les pieds. On agit donc
    sur les sommets, ce qui preserve poids de skinning et armature.
    """
    if o is None or o.type != 'MESH':
        return 0
    if pivot is None:
        pivot = bbox_centre([o])
    fx, fy, fz = factors
    for vert in o.data.vertices:
        vert.co.x = pivot.x + (vert.co.x - pivot.x) * fx
        vert.co.y = pivot.y + (vert.co.y - pivot.y) * fy
        vert.co.z = pivot.z + (vert.co.z - pivot.z) * fz
    o.data.update()
    return len(o.data.vertices)


def material_named(name):
    return bpy.data.materials.get(name)


def feature(features, path, default=None):
    """Lecture tolerante d'un chemin pointe : feature(f, 'hair.color')."""
    node = features
    for key in path.split("."):
        if not isinstance(node, dict):
            return default
        node = node.get(key)
    return default if node in (None, "") else node


def set_shape_key(o, name, value):
    if o is None or o.type != 'MESH' or not o.data.shape_keys:
        return False
    block = o.data.shape_keys.key_blocks.get(name)
    if block is None:
        return False
    block.value = max(0.0, min(1.0, float(value)))
    return True


# ------------------------------------------------------------------- etapes
def check_base_mesh(reporter):
    """Exigence 51 : sans vraie base humanoide, on REFUSE de generer.

    Aucun avatar final ne doit etre fabrique a partir de primitives.
    """
    missing = [name for name in REQUIRED_PARTS if obj(name) is None]
    if missing:
        raise BaseMeshMissing(
            "Base humanoïde manquante — pièces absentes du .blend : "
            + ", ".join(missing))
    total = sum(len(o.data.vertices) for o in meshes())
    if total < 4000:
        raise BaseMeshMissing(
            "Base humanoïde trop pauvre (%d sommets) : refus de générer un "
            "avatar final à partir de primitives." % total)
    rig = next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)
    reporter.operation("Base humanoïde validée : %d sommets, %s"
                       % (total, ("%d os" % len(rig.data.bones)) if rig else "sans rig"))
    return total


def measure_reference(reporter, features, reference_image):
    """Complement PIXEL de l'analyse vision (couleurs dominantes, silhouette).

    N'ecrase jamais une valeur venue de la vision : ne remplit que les trous.
    """
    if not reference_image or not os.path.isfile(reference_image):
        return features
    reporter.operation("Lecture des couleurs de la référence")
    measured = reference_pixels.analyze(reference_image)
    if not measured:
        return features
    filled = reference_pixels.merge(features, measured)
    reporter._emit("avatar.reference.measured", measured=measured, filled=filled)
    return features


def stage_load(reporter, blend_in):
    reporter.stage("load", 0.05, "Ouverture du fichier Blender de l'avatar")
    if blend_in and os.path.isfile(blend_in):
        bpy.ops.wm.open_mainfile(filepath=blend_in)
    reporter.stage("load", 0.4, "Vérification de la base humanoïde")
    try:
        check_base_mesh(reporter)
    except BaseMeshMissing as exc:
        reporter.operation("Base primitive/absente — génération loft AvatarEngine")
        from avatar_engine.engine import generate_base
        out_dir = reporter.job_dir if hasattr(reporter, "job_dir") else os.path.dirname(blend_in or ".") or "."
        blend_out = os.path.join(str(out_dir), "generated_base.blend")
        generate_base(blend_out)
        if os.path.isfile(blend_out):
            bpy.ops.wm.open_mainfile(filepath=blend_out)
        check_base_mesh(reporter)
    reporter.stage("load", 0.7, "Inventaire de la scène")
    body = obj("JARVIS_Skin") or body_object()
    rig = next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)
    reporter.operation("Avatar chargé : %d mesh(es)%s"
                       % (len(meshes()), ", rig présent" if rig else ", sans rig"))
    reporter.stage("load", 1.0, "Avatar chargé")
    reporter.snapshot("load", force=True, label="Avatar de départ")
    reporter.stage_complete("load")
    return body, rig


def stage_body(reporter, features, options):
    """Morphologie : carrure et rapport tête/corps, d'après la vision."""
    reporter.stage("body", 0.1, "Analyse de la morphologie")
    if not options.get("modify_proportions", False):
        reporter.stage_skipped("body", "Proportions non demandées")
        return False

    build = str(feature(features, "body.build", "") or "").lower()
    widths = {"slim": 0.94, "athletic": 1.03, "average": 1.0, "heavy": 1.10}
    width = next((v for k, v in widths.items() if k in build), None)

    head_ratio = feature(features, "head.head_to_body_ratio", None)
    head_scale = None
    if isinstance(head_ratio, (int, float)) and 0.10 <= float(head_ratio) <= 0.30:
        # Reference stylisee : tete un peu plus grande qu'un humain realiste
        # (~0.13). On borne pour ne jamais casser la silhouette.
        head_scale = max(0.92, min(1.18, float(head_ratio) / 0.145))

    if width is None and head_scale is None:
        reporter.stage_skipped("body", "Aucune morphologie exploitable dans l'analyse")
        return False

    if width is not None:
        reporter.operation("Ajustement de la carrure (%s)" % (build or "—"))
        torso = [obj(n) for n in ("JARVIS_Skin", "JARVIS_Shirt", "JARVIS_Jacket",
                                  "JARVIS_Pants", "JARVIS_Belt")]
        pivot = bbox_centre([o for o in torso if o])
        for o in torso:
            scale_mesh(o, (width, width, 1.0), pivot)

    if head_scale is not None:
        reporter.operation("Rapport tête/corps stylisé (x%.2f)" % head_scale)
        head_parts = [obj(n) for n in ("JARVIS_Head", "JARVIS_FaceParts", "JARVIS_Hair",
                                       "JARVIS_Brows", "JARVIS_Eyes", "JARVIS_Iris",
                                       "JARVIS_Pupils", "JARVIS_Mouth")]
        pivot = bbox_centre([o for o in head_parts if o])
        # Pivot sous le crane : la tete grandit vers le haut, pas dans le cou.
        pivot.z = bbox_centre([obj("JARVIS_Head")]).z - 0.075
        for o in head_parts:
            scale_mesh(o, (head_scale, head_scale, head_scale), pivot)

    bpy.context.view_layer.update()
    reporter.stage("body", 1.0, "Morphologie ajustée")
    reporter.snapshot("body", force=True, label="Morphologie")
    reporter.stage_complete("body")
    return True


def stage_face(reporter, features, options):
    """Visage : yeux, sourcils, expression — via les vrais objets et shape keys.

    Aucune deformation brutale des sommets du crane : le master possede 36
    shape keys (sourires, sourcils, clignements, visemes), on les pilote.
    """
    reporter.stage("face", 0.1, "Lecture des traits du visage")
    if not options.get("modify_face", True):
        reporter.stage_skipped("face", "Visage non demandé")
        return False

    head = obj("JARVIS_Head")
    if head is None:
        reporter.stage_skipped("face", "Tête absente de la base")
        return False
    changed = []

    # --- yeux : taille reelle des objets sclere / iris / pupille ---------
    eye_size = str(feature(features, "eyes.size", "") or "").lower()
    factors = {"large": 1.22, "average": 1.0, "small": 0.88}
    factor = factors.get(eye_size)
    if factor and abs(factor - 1.0) > 0.01:
        reporter.operation("Agrandissement des yeux (%s)" % eye_size)
        eye_parts = [obj(n) for n in ("JARVIS_Eyes", "JARVIS_Iris", "JARVIS_Pupils")]
        pivot = bbox_centre([o for o in eye_parts if o])
        for o in eye_parts:
            scale_mesh(o, (factor, 1.0, factor), pivot)
        changed.append("yeux %s" % eye_size)
    reporter.stage("face", 0.4, "Yeux ajustés")

    # --- couleur de l'iris ----------------------------------------------
    iris_colour = parse_color(feature(features, "eyes.color", ""))
    if iris_colour:
        reporter.operation("Couleur des yeux")
        set_base_color(material_named("Iris"), iris_colour)
        changed.append("iris")

    # --- sourcils --------------------------------------------------------
    thickness = str(feature(features, "eyebrows.thickness", "") or "").lower()
    brow_factor = {"thick": 1.35, "medium": 1.0, "thin": 0.75}.get(thickness)
    brows = obj("JARVIS_Brows")
    if brows is not None and brow_factor and abs(brow_factor - 1.0) > 0.01:
        reporter.operation("Épaisseur des sourcils (%s)" % thickness)
        scale_mesh(brows, (1.0, 1.0, brow_factor))
        changed.append("sourcils %s" % thickness)
    brow_colour = (parse_color(feature(features, "eyebrows.color", ""))
                   or parse_color(feature(features, "hair.color", "")))
    if brow_colour:
        set_base_color(material_named("Brows"), brow_colour)

    # --- expression par defaut, via shape keys --------------------------
    expression = str(feature(features, "mouth.default_expression", "") or "").lower()
    reporter.stage("face", 0.75, "Expression par défaut")
    if "open" in expression:
        set_shape_key(head, "jawOpen", 0.28)
        set_shape_key(head, "mouthSmile", 0.55)
        changed.append("sourire ouvert")
    elif "smi" in expression or "sourire" in expression:
        set_shape_key(head, "smile", 0.45)
        set_shape_key(head, "mouthSmile", 0.40)
        changed.append("sourire")
    elif "smirk" in expression:
        set_shape_key(head, "smileLeft", 0.55)
        changed.append("sourire en coin")

    if not changed:
        reporter.stage_skipped("face", "Aucun trait exploitable dans l'analyse")
        return False
    reporter.stage("face", 1.0, "Visage ajusté : " + ", ".join(changed[:4]))
    reporter.snapshot("face", force=True, label="Visage")
    reporter.stage_complete("face")
    return True


def stage_hair(reporter, features, options):
    """Cheveux : volume et couleur du VRAI mesh de coiffure (2 496 sommets)."""
    reporter.stage("hair", 0.1, "Analyse de la coiffure")
    if not options.get("modify_hair", True):
        reporter.stage_skipped("hair", "Coiffure non demandée")
        return False
    hair = obj("JARVIS_Hair")
    if hair is None:
        # Exigence 51/52 : pas de casque en UV-sphere pour remplacer une
        # coiffure absente. On le dit, on ne le fabrique pas.
        reporter.stage_skipped("hair", "Aucun mesh de coiffure dans la base "
                                       "humanoïde (aucune primitive ne sera créée)")
        return False

    changed = []
    volume = str(feature(features, "hair.volume", "") or "").lower()
    factors = {"voluminous": 1.20, "medium": 1.0, "flat": 0.86}
    factor = factors.get(volume)
    if factor and abs(factor - 1.0) > 0.01:
        reporter.operation("Volume de la coiffure (%s)" % volume)
        # Le volume se gagne surtout SUR le crâne : pivot bas, croissance haute.
        pivot = bbox_centre([hair])
        from mathutils import Vector
        pivot = Vector((pivot.x, pivot.y, pivot.z - 0.045))
        scale_mesh(hair, (factor * 0.92 + 0.08, factor * 0.92 + 0.08, factor), pivot)
        changed.append("volume %s" % volume)

    length = str(feature(features, "hair.length", "") or "").lower()
    if "long" in length:
        reporter.operation("Allongement de la coiffure")
        scale_mesh(hair, (1.0, 1.0, 1.12))
        changed.append("longueur longue")
    elif "short" in length or "court" in length:
        reporter.operation("Coiffure raccourcie")
        scale_mesh(hair, (1.0, 1.0, 0.92))
        changed.append("longueur courte")

    colour = parse_color(feature(features, "hair.color", ""))
    if colour:
        reporter.operation("Couleur des cheveux")
        set_base_color(material_named("Hair"), colour)
        changed.append("couleur")

    if not changed:
        reporter.stage_skipped("hair", "Aucune donnée de coiffure exploitable")
        return False
    bpy.context.view_layer.update()
    reporter.stage("hair", 1.0, "Coiffure : " + ", ".join(changed[:3]))
    reporter.snapshot("hair", force=True, label="Cheveux")
    reporter.stage_complete("hair")
    return True


def stage_outfit(reporter, features, options):
    """Tenue : CHAQUE vêtement est traité séparément.

    C'etait la cause principale du rendu « bloc » : l'ancienne version peignait
    chemise, surchemise et pantalon avec UNE seule couleur.
    """
    reporter.stage("outfit", 0.1, "Analyse de la tenue")
    if not options.get("modify_outfit", True):
        reporter.stage_skipped("outfit", "Tenue non demandée")
        return False

    applied = []
    for index, (key, object_name) in enumerate(GARMENTS):
        garment = obj(object_name)
        if garment is None:
            continue
        spec = feature(features, "outfit." + key, None)
        if not isinstance(spec, dict):
            continue
        present = spec.get("present")
        if present is False:
            garment.hide_render = True
            garment.hide_viewport = True
            applied.append("%s masqué" % key)
            continue
        garment.hide_render = False
        garment.hide_viewport = False
        colour = parse_color(spec.get("color"))
        if not colour:
            continue
        label = {"shirt": "chemise", "overshirt": "surchemise", "pants": "pantalon",
                 "belt": "ceinture", "shoes": "chaussures"}[key]
        reporter.operation("Couleur de la %s" % label)
        for mat in (garment.data.materials or []):
            set_base_color(mat, colour)
        applied.append(label)
        reporter.stage("outfit", 0.15 + 0.7 * (index + 1) / len(GARMENTS),
                       "Tenue : %s" % label)

    if not applied:
        reporter.stage_skipped("outfit", "Aucune pièce de tenue décrite par l'analyse")
        return False
    reporter.stage("outfit", 1.0, "Tenue : " + ", ".join(applied))
    reporter.snapshot("outfit", force=True, label="Tenue")
    reporter.stage_complete("outfit")
    return True


def stage_materials(reporter, features, options):
    """Peau et finition, pilotees par l'analyse (teinte, rugosite, style)."""
    reporter.stage("materials", 0.1, "Harmonisation des matériaux")
    if not options.get("modify_materials", True):
        reporter.stage_skipped("materials", "Matériaux non demandés")
        return False

    applied = []
    skin_colour = parse_color(feature(features, "skin.tone", ""))
    if skin_colour:
        reporter.operation("Application du matériau peau")
        for name in SKIN_MATERIALS:
            if set_base_color(material_named(name), skin_colour):
                applied.append(name)

    # Rugosite : la reference est stylisee « film d'animation » -> surfaces
    # douces. On se base sur la mesure quand elle existe.
    roughness = feature(features, "skin.roughness", None)
    stylization = feature(features, "style.stylization", None)
    if not isinstance(roughness, (int, float)):
        roughness = 0.55
    skin_roughness = max(0.25, min(0.9, 0.35 + float(roughness) * 0.4))
    cloth_roughness = 0.8 if (isinstance(stylization, (int, float))
                              and float(stylization) > 0.5) else 0.65

    touched = 0
    for mat in bpy.data.materials:
        if not mat or not mat.use_nodes or not mat.node_tree:
            continue
        target = skin_roughness if mat.name in SKIN_MATERIALS else cloth_roughness
        for node in mat.node_tree.nodes:
            if node.type != 'BSDF_PRINCIPLED':
                continue
            if 'Roughness' in node.inputs:
                node.inputs['Roughness'].default_value = target
            if 'Metallic' in node.inputs and mat.name != "Belt":
                node.inputs['Metallic'].default_value = 0.0
            touched += 1

    if not applied and not touched:
        reporter.stage_skipped("materials", "Aucun matériau à harmoniser")
        return False
    reporter.stage("materials", 1.0,
                   "%d matériau(x) harmonisé(s)%s"
                   % (touched, ", peau appliquée" if applied else ""))
    reporter.snapshot("materials", force=True, label="Matériaux")
    reporter.stage_complete("materials")
    return True


def stage_rig(reporter, rig, options):
    reporter.stage("rig", 0.2, "Vérification du rig")
    if not rig:
        reporter.stage_skipped("rig", "Aucun rig présent dans la base")
        return False
    bones = len(rig.data.bones)
    actions = len(bpy.data.actions)
    reporter.operation("Rig conservé : %s (%d os, %d animations)"
                       % (rig.name, bones, actions))
    reporter.stage("rig", 1.0, "Rig et animations préservés")
    reporter.stage_complete("rig")
    return True


def stage_final(reporter, settings):
    """Sauvegarde + export qualite complete + rendu final."""
    job_dir = settings["job_dir"]
    final_dir = os.path.join(job_dir, "final")
    os.makedirs(final_dir, exist_ok=True)

    reporter.stage("finalize", 0.1, "Sauvegarde du fichier Blender")
    reporter.save_blend(os.path.join(final_dir, "avatar.blend"))

    reporter.stage("finalize", 0.4, "Export GLB qualité complète")
    reporter.operation("Export du modèle final")
    final_glb = os.path.join(final_dir, "avatar.glb")
    # Blender ajoute l'extension si le filepath ne finit pas par .glb : le tmp
    # doit donc deja se terminer par .glb, sinon le rename ne trouve rien.
    tmp = os.path.join(final_dir, ".avatar.tmp.glb")
    has_rig = any(o.type == 'ARMATURE' for o in bpy.data.objects)
    exported = False
    for kwargs in ({"filepath": tmp, "export_format": 'GLB', "export_apply": True,
                    "export_animations": has_rig, "export_materials": 'EXPORT',
                    "export_yup": True},
                   {"filepath": tmp, "export_format": 'GLB'}):
        try:
            bpy.ops.export_scene.gltf(**kwargs)
            if os.path.isfile(tmp) and os.path.getsize(tmp) > 64:
                os.replace(tmp, final_glb)
                exported = True
                break
        except Exception:
            continue
    if not exported:
        reporter.warn("Export GLB final impossible")

    reporter.stage("finalize", 0.7, "Rendu final haute qualité")
    engine = str(settings.get("final_engine") or "eevee").lower()
    previous_quality = reporter.quality
    reporter.quality = "high"
    try:
        reporter._ensure_render_scene()
        scene = bpy.context.scene
        if engine == "cycles" and settings.get("gpu_available"):
            scene.render.engine = 'CYCLES'
            scene.cycles.samples = 96
            scene.cycles.use_denoising = True
        scene.render.resolution_x = scene.render.resolution_y = 1024
        for name in reporter._cameras:
            cam = reporter._cameras[name][0]
            scene.camera = cam
            out = os.path.join(final_dir, "%s.png" % name)
            tmp_png = os.path.join(final_dir, ".%s.tmp.png" % name)
            scene.render.filepath = tmp_png
            reporter.operation("Rendu final %s" % name.replace("_", " "))
            bpy.ops.render.render(write_still=True)
            if os.path.isfile(tmp_png):
                os.replace(tmp_png, out)
    except Exception as exc:
        reporter.warn("Rendu final partiel : %s" % exc)
    finally:
        reporter.quality = previous_quality

    polycount = 0
    for obj in meshes():
        polycount += len(obj.data.polygons)
    reporter.stage("finalize", 1.0, "Avatar final prêt")
    reporter.stage_complete("finalize")
    return {"glb": final_glb if exported else "", "polycount": polycount,
            "final_dir": final_dir, "rigged": has_rig}


# ---------------------------------------------------------------------- main
def main():
    settings = _settings()
    job_dir = settings["job_dir"]
    reporter = AvatarJobReporter(
        job_dir,
        job_id=settings.get("job_id", ""),
        quality=settings.get("quality", "balanced"),
        stages=settings.get("stages") or [],
        reference_id=settings.get("reference_id", ""))

    features = settings.get("features") or {}
    options = settings.get("options") or {}
    blend_in = settings.get("blend_in", "")

    reporter._emit("avatar.blender.started", message="Blender démarré")
    if not blend_in or not os.path.isfile(blend_in):
        reporter._emit("avatar.blender.failed",
                       message="Fichier .blend introuvable : %s" % blend_in)
        return 1

    try:
        body, rig = stage_load(reporter, blend_in)
        features = measure_reference(reporter, features,
                                     settings.get("reference_image", ""))
        reporter.check_control()

        stage_body(reporter, features, options)
        reporter.check_control()

        stage_face(reporter, features, options)
        for adj in reporter.check_control():
            reporter.operation("Consigne utilisateur prise en compte : %s"
                               % str(adj.get("text", ""))[:120])

        stage_hair(reporter, features, options)
        reporter.check_control()

        stage_outfit(reporter, features, options)
        reporter.check_control()

        stage_materials(reporter, features, options)
        reporter.check_control()

        stage_rig(reporter, rig, options)
        reporter.check_control(allow_pause=False)

        result = stage_final(reporter, settings)
        reporter._emit("avatar.blender.completed", **result)
        return 0

    except BaseMeshMissing as exc:
        reporter._emit("avatar.blender.failed", message=str(exc)[:400],
                       base_mesh_missing=True)
        return 1
    except JobCancelled:
        reporter._emit("avatar.blender.cancelled", message="Job annulé")
        return 0
    except Exception as exc:
        reporter._emit("avatar.blender.failed",
                       message=str(exc)[:400],
                       traceback=traceback.format_exc()[-1500:])
        return 1


if __name__ == "__main__":
    sys.exit(main())
