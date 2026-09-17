"""Script d'opération : exporter un modèle .blend sauvegardé vers des formats."""
from __future__ import annotations

import glob
import os
import sys
import traceback

sys.path.insert(0, __file__.rsplit("\\", 1)[0] + "\\lib")
sys.path.insert(0, __file__.rsplit("/", 1)[0] + "/lib")

try:
    from common import boot, settings, out, progress, finish, fail  # noqa
    from export import open_blend, export_scene, save_blend  # noqa

    st = boot()
    progress(0.05, "Configuration chargée", "initialisation")

    src = st.get("source") or st.get("blend_in")
    if not src or not os.path.exists(src):
        candidates = glob.glob(os.path.join(str(settings().get("output_dir") or "."), "*.blend"))
        candidates += glob.glob(os.path.join(str(settings().get("work_dir") or "."), "**", "*.blend"), recursive=True)
        candidates = sorted(set(candidates))
        if not candidates:
            raise RuntimeError("Aucun .blend à exporter.")
        src = candidates[-1]

    progress(0.3, f"Ouverture {os.path.basename(src)}…", "import")
    open_blend(src)

    formats = [f for f in (st.get("formats") or st.get("export_formats") or ["glb"]) if f]
    if not formats:
        formats = ["glb"]

    base = os.path.splitext(os.path.basename(src))[0].replace(" ", "_")
    exports = {}
    for i, fmt in enumerate(formats):
        progress(0.4 + 0.5 * i / max(1, len(formats)), f"Export {fmt.upper()}…", "export")
        try:
            exports[fmt] = export_scene(out(f"{base}.{fmt}"), fmt)
        except Exception as exc:
            exports[fmt] = f"export_failed:{exc}"

    progress(0.95, "Re-sauvegarde .blend…", "export")
    try:
        save_blend(src)
    except Exception:
        pass

    files = [p for p in exports.values() if isinstance(p, str) and not p.startswith("export_failed:")]
    finish({
        "source": src,
        "exports": exports,
        "formats": formats,
        "files": files,
    }, message=f"Export réussi : {', '.join(formats)}")
except Exception:
    try:
        fail(f"export_model : {traceback.format_exc()}")
    except Exception:
        pass
    raise