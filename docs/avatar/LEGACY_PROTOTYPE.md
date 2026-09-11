# `legacy_prototype`

The previous primitive/loft avatar pipeline is retained only for historical
reference. It is not an input, fallback, or quality path for `MPFB_V1`.

Official avatar jobs route through `jarvis/blender_scripts/avatar_engine_job.py`
to `avatar_engine_v2_job.py`. That runtime requires Blender 4.2+ and the MPFB2
extension, creates the human through MPFB `HumanService`, and stops hard when a
required dependency or asset is missing.

The static candidate remains separate from the live avatar. It can become live
only after explicit user acceptance.
