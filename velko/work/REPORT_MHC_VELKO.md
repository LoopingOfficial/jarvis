# RAPPORT COURT — Préparation de MHC_VELKO (passe Astra)

Date : 2026-09-24 | Auteur : pipeline big-pickle | Projet : `C:\Users\jerom\Desktop\jarvis-mac\velko`

- **META HUMAN** : READY — `/Game/VELKO/MHC_VELKO` (MetaHumanCharacter 1,2 Go), rig corps 334 os sur `metahuman_base_skel`, face `Face_Archetype_Skeleton` (858 morphs). Look intouché.
- **SCENE** : VELKO_AvatarLab créée & sauvegardée (KeyLight, 4 caméras FOV60, 10 markers IK, floor). ⚠ `MetaHumanDefaultEditorPipelineActor` n'est pas sérialisé → placer `MHC_VELKO` dans la map à l'ouverture (ou `scripts\11_scene_avatar.py`).
- **BODY RIG** : PASS — skeleton 334 os, animations test bindées et chargées sur le même skeleton.
- **FACE RIG** : PASS — 858 morph targets, Control Rigs face dispo moteur (MetaHumanAnimator).
- **IK** : PASS — `IK_VELKO` (root=pelvis, root motion=pelvis, 29 chaînes) créé.
- **RETARGETING** : PASS — `RT_VELKO` (source=target=IK_VELKO, preview mesh, 5 ops, chaînes mappées EXACT).
- **TEST ANIMATIONS** :
  - Idle : PASS (`VELKO_Idle_Anim`, 1,97 s, skeleton OK)
  - Walk : PASS (`VELKO_Walk_Anim`, 1,97 s, skeleton OK)
  - Sit : PASS (`VELKO_Sit_Anim`, 2,97 s, skeleton OK)
  - Turn : PASS (`VELKO_Turn_Anim`, 1,97 s) — clips générés Blender 5.0 → FBX → import auto.
- **BLENDER REFERENCE** : À FAIRE en interactif — export du corps `BodyMesh_1` en `MHC_VELKO_BODY_REFERENCE.fbx` depuis l'éditeur (headless crashe l'exporteur FBX). Pipeline détaillé : `velko\work\VELKO_AVATAR_PIPELINE.md`.
- **BLOCKERS** :
  1. Acteur MetaHuman non persisté dans la map (SDK pipeline) — à matérialiser à la première ouverture interactive.
  2. Export FBX de référence corps impossible en commandlet (crash) — nécessite session éditeur UI.
  3. Aucune animation « vraie » avant passe Astra (clips de test procéduraux Blender) — OK pour valider le pipeline, à remplacer par la motion matching/finale.