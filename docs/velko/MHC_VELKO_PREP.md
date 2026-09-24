# MHC_VELKO preparation receipt

## Canonical source

- Unreal project: `C:/Users/jerom/Desktop/jarvis-mac/velko/unreal/VELKO_MetaHuman`
- Unreal Engine: `D:/UE_5.8/Engine/Binaries/Win64/UnrealEditor.exe`
- MetaHuman plugin: `D:/UE_5.8/Engine/Plugins/MetaHuman`
- Canonical asset: `/Game/VELKO/MHC_VELKO`
- File: `velko/unreal/VELKO_MetaHuman/Content/VELKO/MHC_VELKO.uasset`
- Asset size: 1,284,902,625 bytes

## Discovered dependency families

- MetaHuman character sources: `/Game/Fab/MetaHuman/MHC_Frey`, `/Game/Fab/MetaHuman/MHC_Hero`
- MetaHuman base skeleton evidence: `/Game/Fab/MetaHuman/MHC_Hero/MetaHumans/Common/Female/Medium/NormalWeight/Body/metahuman_base_skel`
- Head skeleton / physics evidence: `/Game/Fab/MetaHuman/NomadWaveUndercut/Common/Meshes/SK_FemaleHead_Skeleton`, `PHYS_FemaleHead_BA`
- Clothing: `/Game/VELKO/OA_T-Shirt_VELKO`, `/Game/Fab/MetaHuman/WI_T-Shirt`, `/Game/Fab/MetaHuman/WI_OA_Jeans_slm`, `/Game/Fab/MetaHuman/WI_OA_Jeansmcf`, `/Game/Fab/MetaHuman/WI_OA_Jeansakn`
- Grooms: `/Game/Fab/MetaHuman/NomadWaveUndercut/NomadWaveUndercut/Gr_NomadWaveUndercut`, `/Game/Fab/MetaHuman/GB_Hair_TEKATI_Broccoli_Fade_no2`
- Groom mesh: `/Game/Fab/MetaHuman/Hair_TEKATI_Broccoli_Fade_no2/GroomMesh/SKM_MH_Groom_Head`
- Materials/textures/physics: present under the corresponding `Fab/MetaHuman` clothing, hair and head folders.

## Required isolated scene

- Target package: `/Game/VELKO/VELKO_AvatarLab`
- Required contents: MHC_VELKO, simple floor, clean light, front / three-quarter / profile / full-body cameras.
- Current status: NOT CREATED. Unreal Editor 5.8 is now installed and the project is open; editor-side creation and verification are still pending.

## Validation status

| Area | Status | Evidence / blocker |
|---|---|---|
| Body rig | NOT VERIFIED | Requires opening MHC_VELKO in Unreal |
| Face rig | NOT VERIFIED | Requires MetaHuman preview/control rig |
| Morph targets | NOT VERIFIED | Requires editor inspection |
| IK Rig | NOT PRESENT | No IK Rig asset found |
| IK Retargeter | NOT PRESENT | No IK Retargeter asset found |
| AnimBP | NOT PRESENT | No animation blueprint found |
| Idle | BLOCKED | No scene/animation test harness |
| Walk | BLOCKED | No scene/animation test harness |
| Sit | BLOCKED | No scene/animation test harness |
| Body reference | NOT CREATED | Must be exported from the real MetaHuman in Unreal |

## Hair decision

The closest available groom candidates are `GB_Hair_TEKATI_Broccoli_Fade_no2`
and `Gr_NomadWaveUndercut`. Neither is auto-applied; visual fit must be decided
in Unreal after opening MHC_VELKO.

## Isolation rule

Do not connect this avatar lab to the Velko engine until the scene and all tests
are green. The legacy MakeHuman file remains preserved and is explicitly marked
`LEGACY_AVATAR` in `velko/work/LEGACY_AVATAR.md`.
