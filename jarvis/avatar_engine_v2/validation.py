"""Static milestone validation; no artificial quality score."""
from __future__ import annotations

from typing import Any


REQUIRED_VIEWS = ("front", "three_quarter", "side", "back", "face_closeup")


def validate_static_report(report: dict[str, Any]) -> dict[str, Any]:
    failures = []
    if not report.get("mpfb_runtime"):
        failures.append("mpfb_runtime_missing")
    if not report.get("basemesh_vertices", 0) >= 10000:
        failures.append("basemesh_missing_or_low_density")
    if not all(report.get("renders", {}).get(view) for view in REQUIRED_VIEWS):
        failures.append("required_render_missing")
    return {"ok": not failures, "failures": failures, "quality_gate": "USER_APPROVAL",
            "score": None}
