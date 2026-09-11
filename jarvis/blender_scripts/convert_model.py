"""Script d'opération : convertir un fichier 3D vers d'autres formats (sans rendu)."""
from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, __file__.rsplit("\\", 1)[0] + "\\lib")
sys.path.insert(0, __file__.rsplit("/", 1)[0] + "/lib")

try:
    from common import boot, settings, out, progress, finish, fail  # noqa
    from export import import_file, export_scene  # noqa
    from geometry import mesh_stats  # noqa

    st = boot()
    progress(0.05, "Configuration chargée", "initialisation")
    src = st.get("source")
    if not src or not os.path.exists(src):
        raise RuntimeError("Aucun fichier source à convertir.")

    progress(0.3, f"Import {os.path.basename(src)}…", "import")
    imported = import_file(src)
    if not imported:
        raise RuntimeError("Aucun objet importé.")

    target_fmts = [f for f in (st.get("formats") or ["glb"]) if f]
    base = os.path.splitext(os.path.basename(src))[0].replace(" ", "_")
    exports = {}
    for i, fmt in enumerate(target_fmts):
        progress(0.4 + 0.5 * i / max(1, len(target_fmts)), f"Export {fmt.upper()}…", "export")
        try:
            exports[fmt] = export_scene(out(f"{base}.{fmt}"), fmt)
        except Exception as exc:
            exports[fmt] = f"export_failed:{exc}"

    files = [p for p in exports.values() if isinstance(p, str) and not p.startswith("export_failed:")]
    finish({
        "source": src,
        "formats": target_fmts,
        "exports": exports,
        "files": files,
    }, message=f"Conversion {', '.join(target_fmts)} terminée")
except Exception:
    try:
        fail(f"convert_model : {traceback.format_exc()}")
    except Exception:
        pass
    raise