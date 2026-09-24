# VELKO — Pipeline Avatar MetaHuman (MHC_VELKO)

> Statut : **PREPARED** pour la passe Astra. Source d'avatar = `MHC_VELKO` (MetaHuman).
> Ancien avatar MakeHuman = `LEGACY_AVATAR` (voir `velko/work/LEGACY_AVATAR.md`) — conservé, non utilisé comme source.

Date : 2026-09-24 — Machine Windows (chemins réels ci-dessous).

---

## 1. Assets clés (chemins EXACTS)

| Quoi | Chemin | Classe |
|---|---|---|
| Projet Unreal | `C:\Users\jerom\Desktop\jarvis-mac\velko\unreal\VELKO_MetaHuman` (`.uproject` = `VELKO_MetaHuman.uproject`, UE 5.8) | — |
| Moteur | `D:\UE_5.8` | — |
| Runner commandlet | `D:\UE_5.8\Engine\Binaries\Win64\UnrealEditor-Cmd.exe` | — |
| **LE vrai MetaHuman** | `/Game/VELKO/MHC_VELKO` → `Content\VELKO\MHC_VELKO.uasset` (**1,2 Go**, classe `MetaHumanCharacter`) | character |
| Outfit VELKO | `/Game/VELKO/OA_T-Shirt_VELKO` → `Content\VELKO\OA_T-Shirt_VELKO.uasset` (classe `ChaosOutfitAsset`) | outfit |
| Squelette corps | `/Game/Fab/MetaHuman/MHC_Hero/MetaHumans/Common/Female/Medium/NormalWeight/Body/metahuman_base_skel` | Skeleton |
| Squelette visage (runtime assemble) | `/MetaHumanCharacter/Face/Face_Archetype_Skeleton` (plugin MetaHumanCharacter) | Skeleton |
| Scène | `/Game/VELKO/Maps/VELKO_AvatarLab` → `Content\VELKO\Maps\VELKO_AvatarLab.umap` | level |
| Animations de test | `/Game/VELKO/Anim/VELKO_Idle_Anim`, `VELKO_Walk_Anim`, `VELKO_Sit_Anim`, `VELKO_Turn_Anim` | AnimSequence |
| IK Rig | `/Game/VELKO/Rig/IK_VELKO` | IKRigDefinition |
| IK Retargeter | `/Game/VELKO/Rig/RT_VELKO` | IKRetargeter |
| Scripts UE Python | `velko\unreal\scripts\01_…12b_.py` | — |
| Source FBX anims | `velko\unreal\anim_source\VELKO_{Idle,Walk,Sit,Turn}.fbx` (générés Blender) | FBX |
| Blender | `C:\Program Files\Blender Foundation\Blender 5.0\blender.exe` (et 3.6) | — |

## 2. Scène VELKO_AvatarLab — contenu persistant
- Lumières/Caméras : `VELKO_KeyLight` (DirectionalLight), `VELKO_Cam_Front`, `VELKO_Cam_ThreeQuarter`, `VELKO_Cam_Profile`, `VELKO_Cam_FullBody` (FOV 60).
- **Markers IK** (StaticSphere 0.16u) : `IKTarget_LeftFoot`, `IKTarget_RightFoot`, `IKTarget_LeftHand`, `IKTarget_RightHand`, `IKTarget_Keyboard`, `IKTarget_Mouse`, `IKTarget_ChairSit`, `IKTarget_ScreenLookLeft`, `IKTarget_ScreenLookCenter`, `IKTarget_ScreenLookRight`.
- Sol `VELKO_Floor` (`/Engine/BasicShapes/Plane`, scale 60).

> **NOTE IMPORTANTE** : l'acteur MetaHuman (`MetaHumanDefaultEditorPipelineActor`) est généré par le pipeline du SDK (visualisation/édition) et n'est **pas sérialisé** dans le `.umap`. Pour matérialiser VELKO dans la map :
> 1. Ouvrir `VELKO_AvatarLab` avec le moteur éditorial,
> 2. Charger `/Game/VELKO/MHC_VELKO` (Objets) et le glisser-déposer dans la vue 3D — l'acteur `MetaHuman` persistant est créé,
> 3. Ou exécuter `velko\unreal\scripts\11_scene_avatar.py` (spawn pipeline pour préview).
> Le pipeline Astra doit débuter par « ouvrir VELKO_AvatarLab puis placer MHC_VELKO ».

## 3. Rig — validation
- **BODY RIG** : `BodyMesh_1` bindé sur `metahuman_base_skel` — 334 os extraits du skeleton (pelvis, spine_01–05, neck_01/02, head, clavicles, upper/lowerarm, mains/doigts complets `index/middle/ring/pinky/thumb_0X` + twist/correctifs, jambes thigh/calf/twists, foot/ball/toes). Body morph = 0 (blendshapes corps gérés via RigLogic/DNA, normal MetaHuman).
- **FACE RIG** : `FaceMesh_0` bindé sur `Face_Archetype_Skeleton`, **858 morph targets** (bases de blendshape faciales présentes). Control Rigs face dispo moteur : `D:\UE_5.8\Engine\Plugins\MetaHuman\MetaHumanAnimator\Content\IdentityTemplate\Controls\` (`Face_ControlBoard_CtrlRig`, `Neck_CtrlRig`, `HeadMovementIK_Proc_CtrlRig`).
- **Animabilité** : 4 AnimSequences de test créées sur `metahuman_base_skel` (générées Blender → FBX → import automatique). Idle 1,97 s, Walk 1,97 s (cycle 2 pas), Sit 2,97 s (descente assise), Turn 1,97 s (rotation 90°).
- **IK** : `IK_VELKO` root = `pelvis`, root motion = `pelvis`, **29 chaînes retarget auto-générées** (Spine, Neck, Head, LeftLeg/LeftFoot(LeftFootIK), LeftArm, mains/doigts, symétrique droit). `RT_VELKO` : source=target=`IK_VELKO`, preview mesh posé des deux côtés, chaînes mappées (EXACT auto-map), 5 ops par défaut (FB/IPP).

## 4. Cheveux — candidats taper fade (Fab, non appliqués)
- `Hair_TEKATI_Broccoli_Fade_no2` (cut fade, bord technique)
- `NomadWaveUndercut`
- `Frey/Middle_part` = NON adapté (féminin, raie au milieu).
> Cheveux actuels VELKO dans la collection interne : `S_SweptUp` (actif), variantes `S_Casual`, `S_360Waves`, `S_Pixie`. Ne pas retoucher au look sans validation.

## 5. Pipeline Blender (RÉFÉRENCE ONLY — ne rien créer d'animé dedans)
- Ancien master : `velko\work\VELKO_MASTER_v2.blend` = **LEGACY**, ne pas modifier/ne pas servir de base.
- Référence corps pour fits vêtements : à exporter depuis UE l'"Export selected" du corps `BodyMesh_1` (interactif, dans l'éditeur) en `velko\unreal\anim_source\MHC_VELKO_BODY_REFERENCE.fbx`. ⚠ N'essayer PAS cet export en commandlet headless (crash de l'exporteur FBX sur meshes transitoires MetaHuman) — le faire en mode interactif.
- Convention : tout asset Blender de travail dans `velko\assets\` ou `velko\work\`, jamais dans le racine.

## 6. Point d'entrée pour la passe Astra
1. `cd velko\unreal\VELKO_MetaHuman`
2. Ouvrir le projet (UE 5.8), ouvrir `VELKO_AvatarLab`, placer `/Game/VELKO/MHC_VELKO`.
3. Anim/test : monter `VELKO_Idle_Anim/Walk/Sit/Turn` sur le corps (Animation Blueprint ou PlayAnimation).
4. Retarget/IK : `RT_VELKO` + `IK_VELKO` prêts ; markers `IKTarget_*` en place.