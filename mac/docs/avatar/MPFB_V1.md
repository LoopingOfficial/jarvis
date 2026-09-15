# JARVIS MPFB_V1

`JARVIS_MPFBA_V1_20260911_A` is the new avatar architecture. MPFB2 is an
official Blender extension dependency, installed in Blender's extension
repository; its source is not copied into this repository.

The first milestone is static appearance only. The runtime must create the
human with MPFB `HumanService.create_human`, apply MPFB assets, and render
`front`, `three_quarter`, `side`, `back`, and `face_closeup` at 1024 px or
larger. No score is computed: `USER_APPROVAL` is the quality gate.

The previous primitive/loft generator is archived as `legacy_prototype` and
cannot be used as a fallback. If Blender is below 4.2, MPFB is unavailable,
the basemesh is missing, or a required stage fails, the job stops.
