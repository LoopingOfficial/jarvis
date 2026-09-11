"""Script d'opération : créer un modèle 3D (primitive, blueprint paramétrique ou pièces)."""
from __future__ import annotations

import sys
import traceback

sys.path.insert(0, __file__.rsplit("\\", 1)[0] + "\\lib")
sys.path.insert(0, __file__.rsplit("/", 1)[0] + "/lib")

import bpy  # noqa: E402

try:
    from common import boot, settings, out, progress, finish, fail  # noqa
    from geometry import create_primitive, add_text, join_objects, scene_objects_summary, clear_scene  # noqa
    from materials import resolve_merge, apply_to_object, object_materials  # noqa
    from blueprints import blueprint, build_parts  # noqa
    from camera import add_camera  # noqa
    from lights import default_lights  # noqa
    from render import render_still  # noqa
    from export import save_blend, export_scene  # noqa
    from scene import add_floor  # noqa
    from anim import animate  # noqa
    from modifiers import decimate  # noqa
    from rigify import build_human_rig, bind_mesh  # noqa
    from mathutils import Vector

    st = boot()
    progress(0.05, "Configuration chargée", "initialisation")

    warnings: list[str] = []
    prompt = str(st.get("prompt") or "")
    kind = str(st.get("kind") or "").strip() or "cube"
    _humanoid = kind.lower() in {"character", "personnage", "avatar", "human", "humain", "person"}
    _prompt_l = prompt.lower()
    if _humanoid or "humanoide" in _prompt_l or "humanoïde" in _prompt_l or "personnage" in _prompt_l:
        fail("Un humanoïde final ne se construit pas en primitives. "
             "Utilise AvatarEngine (avatar.engine.build / load_base loft).")
        raise RuntimeError("humanoid primitives forbidden")
    name = str(st.get("name") or "").strip() or f"{kind.title()}_JARVIS"
    color = st.get("color") or None
    material = st.get("material") or ({"preset": kind} if kind == "stylized" else None)
    parts = st.get("parts") or []
    export_formats = st.get("export_formats") or [st.get("export_format") or "glb"]

    clear_scene()
    progress(0.2, f"Construction ({kind})…", "geometry")

    if parts:
        objects = build_parts(parts)
    else:
        bp = blueprint(kind)
        if bp:
            objects = build_parts(bp)
        else:
            progress(0.22, "Primitive de base…", "geometry")
            obj = create_primitive(kind, scale=None, size=float(st.get("size", 1.0)),
                                   location=(0.0, 0.0, 0.0), name=name)
            objects = [obj]

    progress(0.42, "Matériau…", "materials")
    base_mat = ({"preset": material} if isinstance(material, str) else
                material if isinstance(material, dict) else {})
    merged = resolve_merge({**base_mat, "color": color})
    for obj in objects:
        if not obj.data.materials:
            apply_to_object(obj, color=merged.get("color", "6e5f7d"),
                            metallic=float(merged.get("metallic", 0.0)),
                            roughness=float(merged.get("roughness", 0.5)),
                            emission_strength=float(merged.get("emission_strength", 0.0)),
                            emission_color=merged.get("emission_color", merged.get("color")),
                            name="Mat_JARVIS")

    if st.get("join") is True and len(objects) > 1:
        joined = join_objects([o.name for o in objects])
        if joined:
            objects = [joined]

    base_obj = objects[0]

    if st.get("rig"):
        humanoid = kind in {"character", "robot"} or "personn" in prompt.lower()
        if humanoid:
            progress(0.5, "Rig humanoïde…", "rigging")
            joined = base_obj
            if len(objects) > 1:
                joined = join_objects([o.name for o in objects if o.type == "MESH"])
            arm = build_human_rig(None, scale=float(st.get("rig_scale") or 1.0),
                                  name="AR_JARVIS")
            bind_mesh(joined, arm)
            base_obj = joined
        else:
            warnings.append("rig ignoré : modèle non humanoïde")

    _clips: list[str] = []
    if st.get("animate") and st.get("animations"):
        _clips = [str(c).strip().lower() for c in st["animations"]
                  if isinstance(c, str) and c.strip()]
    elif st.get("animate") and st.get("clip"):
        _clips = [str(st["clip"]).strip().lower()]
    supported_anim = {"spin", "rotate", "turn", "twirl", "float", "pulse",
                      "bounce", "orbit"}
    if _clips:
        clip = _clips[0]
        if clip in supported_anim:
            progress(0.56, f"Animation {clip}…", "animation")
            animate(base_obj, kind={"spin": "rotate", "turn": "rotate",
                                    "twirl": "rotate", "float": "pulse"}.get(clip, clip),
                    axis=str(st.get("axis") or "Z").upper(),
                    frames=(1, max(2, int(float(st.get("duration") or 5) * int(st.get("fps") or 30)))),
                    amplitude=float(st.get("amplitude") or 0.4))
        else:
            warnings.append(f"animation « {clip} » ignorée : non implémentée")

    if st.get("optimize"):
        progress(0.6, "Optimisation…", "optimisation")
        try:
            decimate(base_obj, 0.8)
        except Exception as exc:
            warnings.append(f"optimisation ignorée : {exc}")

    geometry_name = base_obj.name
    if st.get("floor") is not False and kind not in {"plane", "grid"}:
        progress(0.5, "Plan de sol…", "scene")
        add_floor(color="#20212b")

    progress(0.58, "Caméra…", "camera")
    add_camera(st.get("angle", "three_quarter"))
    progress(0.62, "Lumières…", "lights")
    default_lights()

    if st.get("preview") is not False:
        progress(0.72, "Rendu preview…", "rendering")
        render_still(out("preview.png"),
                     engine=str(st.get("render_engine") or st.get("engine") or "cycles"),
                     samples=int(st.get("samples", 64)),
                     use_gpu=bool(st.get("use_gpu", True)))

    progress(0.86, "Sauvegarde .blend…", "exporting")
    save_blend(out("model.blend"))
    exports = {}
    for fmt in export_formats:
        progress(0.9, f"Export {fmt.upper()}…", "exporting")
        try:
            exports[fmt] = export_scene(out(f"model.{fmt}"), fmt)
        except Exception as exc:
            exports[fmt] = f"export_failed:{exc}"

    summary = scene_objects_summary()
    materials = sorted({m for o in objects for m in object_materials(o)})
    finish({
        "kind": "blueprint" if (parts or blueprint(kind)) else kind,
        "shape": kind,
        "prompt": prompt[:500],
        "name": geometry_name,
        "color": merged.get("color", ""),
        "objects": summary,
        "materials": materials,
        "exports": exports,
        "warnings": warnings,
        "dimensions": [0.0, 0.0, 0.0],
    }, message=f"Modèle {geometry_name} créé")
except Exception:
    try:
        fail(f"create_model : {traceback.format_exc()}")
    except Exception:
        pass
    raise