"""Conversion FBX -> GLB pour le viewer avatar, via Blender en mode headless.

Usage :
    blender --background --python tools/fbx_to_glb.py -- <entree.fbx> <sortie.glb> [--draco] [--no-anim]

Pourquoi Blender plutôt qu'un convertisseur JS
----------------------------------------------
Le FBX est un format binaire propriétaire ; `FBXLoader` de three.js le lit dans
le navigateur, mais charger 45 Mo bloque le thread principal plusieurs secondes
et conserve des matériaux FBX que three interprète approximativement. Blender
importe, nettoie et réexporte en glTF binaire — format natif de three, chargé en
une fraction du temps.

Ce que fait le script
---------------------
  * importe le FBX avec ses animations,
  * met le modèle À L'ÉCHELLE MÈTRE et le pose sur l'origine, pieds au sol,
    centré sur X/Z : c'est ce qui permet au viewer de le cadrer sans corriger
    un décalage à la main,
  * limite les textures à 2048 px (les scans Renderpeople embarquent du 8K,
    inutile pour un avatar affiché à 400 px de haut et coûteux en VRAM),
  * exporte en GLB, compression Draco optionnelle.

LIMITE CONNUE
-------------
Testé sur rp_manuel_animated_001_dancing.fbx (Renderpeople animé), le maillage
et le squelette ressortent corrects mais le haut du corps se déforme dès que
l'animation joue, dans les cinq configurations d'import essayées
(automatic_bone_orientation on/off, primary_bone_axis Y, leaf bones on/off,
courbes de translation remises à l'échelle). Le fichier lui-même est à vérifier
dans l'interface Blender avant d'aller plus loin : si l'animation y est déjà
déformée, aucun réglage d'export ne la réparera.
"""
import sys
from pathlib import Path

import bpy


def log(message: str) -> None:
    print(f"[fbx2glb] {message}", flush=True)


def parse_args() -> tuple[Path, Path, bool, bool]:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(argv) < 2:
        raise SystemExit("Usage : ... -- <entree.fbx> <sortie.glb> [--draco] [--no-anim]")
    return Path(argv[0]), Path(argv[1]), "--draco" in argv, "--no-anim" not in argv


def reset_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_fbx(path: Path) -> None:
    log(f"import {path.name} ({path.stat().st_size / 1e6:.1f} Mo)")
    bpy.ops.import_scene.fbx(
        filepath=str(path),
        use_anim=True,
        automatic_bone_orientation=True,   # évite les os tordus des rigs Maya/3ds
        ignore_leaf_bones=True,
    )


def describe() -> dict:
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    bones = sum(len(a.data.bones) for a in armatures)
    tris = sum(len(m.data.loop_triangles) for m in meshes if m.data)
    return {
        "meshes": len(meshes), "armatures": len(armatures), "bones": bones,
        "actions": [a.name for a in bpy.data.actions],
        "materials": [m.name for m in bpy.data.materials],
        "images": [(i.name, tuple(i.size)) for i in bpy.data.images if i.size[0]],
        "triangles": tris,
    }


def normalize_transform() -> None:
    """Échelle mètre, pieds au sol, centré sur X/Z.

    Les FBX Renderpeople sont en centimètres : importés tels quels, le
    personnage fait 180 unités de haut et sort du cadre de toute caméra réglée
    pour une scène en mètres. On mesure la boîte englobante réelle plutôt que
    de supposer un facteur : un fichier déjà en mètres ne doit pas être réduit.
    """
    # On N'APPLIQUE PAS l'échelle. L'importeur FBX pose une échelle 0,01 sur
    # l'armature pour compenser un rig en centimètres ; l'appliquer la retire
    # de l'objet mais laisse les courbes de translation des os en centimètres,
    # et le personnage part alors à plus de 100 m dès la première frame.
    # (Constaté : amplitude 157 sur l'os « hip ».) On applique la rotation
    # seule, et toute correction d'échelle se fait sur l'objet racine — dont le
    # facteur multiplie aussi les translations animées, donc sans rien casser.
    for obj in bpy.data.objects:
        obj.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    if not meshes:
        log("aucun maillage : pas de normalisation")
        return

    from mathutils import Vector
    xs, ys, zs = [], [], []
    for obj in meshes:
        for corner in obj.bound_box:
            world = obj.matrix_world @ Vector(corner)
            xs.append(world.x); ys.append(world.y); zs.append(world.z)
    height = max(zs) - min(zs)
    log(f"hauteur mesurée : {height:.2f} unités")

    scale = 1.0
    if height > 10:                      # centimètres (ou millimètres)
        scale = 1.7 / height             # ramené à une stature humaine plausible
        log(f"mise à l'échelle x{scale:.5f} (unités non métriques détectées)")

    centre_x = (max(xs) + min(xs)) / 2
    centre_y = (max(ys) + min(ys)) / 2
    floor_z = min(zs)

    roots = [o for o in bpy.data.objects if o.parent is None]
    for obj in roots:
        obj.scale = tuple(s * scale for s in obj.scale)
        obj.location.x -= centre_x * scale
        obj.location.y -= centre_y * scale
        obj.location.z -= floor_z * scale
    bpy.context.view_layer.update()
    log("modèle centré sur X/Y, pieds posés sur Z=0")


def clamp_textures(max_size: int = 2048) -> None:
    for image in bpy.data.images:
        width, height = image.size
        if not width or max(width, height) <= max_size:
            continue
        ratio = max_size / max(width, height)
        image.scale(int(width * ratio), int(height * ratio))
        log(f"texture {image.name} réduite : {width}x{height} -> {image.size[0]}x{image.size[1]}")


def bake_animation() -> None:
    """Réécrit les courbes d'animation depuis la pose RÉELLEMENT évaluée.

    Indispensable ici. Le FBX Renderpeople porte un rig en centimètres,
    compensé à l'import par une échelle 0,01 sur l'armature. Appliquer cette
    échelle (pour obtenir un modèle en mètres) la retire de l'objet mais PAS
    des courbes de translation des os : la hanche continue de se déplacer de
    117 unités, et le personnage part à 117 m du décor dès que l'animation
    démarre. Le bake en « visual keying » recalcule chaque clé à partir de la
    pose visible, donc dans la même unité que le maillage.

    Effet secondaire utile : les 19 actions fragmentées par l'importeur FBX
    (une par chaîne d'os) fusionnent en une seule action propre.
    """
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if not armatures:
        log("aucune armature : rien à recuire")
        return
    armature = armatures[0]
    frame_start, frame_end = (int(v) for v in bpy.context.scene.frame_range) \
        if hasattr(bpy.context.scene, "frame_range") else (bpy.context.scene.frame_start,
                                                           bpy.context.scene.frame_end)
    bpy.ops.object.select_all(action="DESELECT")
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode="POSE")
    log(f"bake de l'animation, frames {frame_start} → {frame_end}")
    bpy.ops.nla.bake(
        frame_start=frame_start, frame_end=frame_end, step=1,
        only_selected=False, visual_keying=True, clear_constraints=False,
        clear_parents=False, use_current_action=True, bake_types={"POSE"},
    )
    bpy.ops.object.mode_set(mode="OBJECT")
    log(f"actions après bake : {[a.name for a in bpy.data.actions]}")


def export_glb(path: Path, draco: bool, animations: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    kwargs = dict(
        filepath=str(path), export_format="GLB",
        export_animations=animations, export_skins=True, export_morph=True,
        export_apply=False, export_yup=True,        # three.js attend Y vers le haut
    )
    if draco:
        kwargs.update(export_draco_mesh_compression_enable=True,
                      export_draco_mesh_compression_level=6)
    bpy.ops.export_scene.gltf(**kwargs)
    log(f"export {path.name} : {path.stat().st_size / 1e6:.2f} Mo")


def main() -> None:
    source, target, draco, animations = parse_args()
    if not source.is_file():
        raise SystemExit(f"Introuvable : {source}")
    reset_scene()
    import_fbx(source)
    info = describe()
    log(f"contenu : {info['meshes']} maillage(s), {info['armatures']} armature(s), "
        f"{info['bones']} os, {info['triangles']} triangles")
    log(f"animations : {info['actions'] or 'aucune'}")
    log(f"matériaux : {info['materials']}")
    log(f"textures : {info['images'] or 'aucune'}")
    normalize_transform()
    clamp_textures()
    export_glb(target, draco, animations)
    log("terminé")


if __name__ == "__main__":
    main()
