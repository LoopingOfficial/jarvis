"""Reproducible visual benchmark: V1 pipeline vs V2 pipeline.

Runs the same eight cases through both pipelines at a fixed seed, records time,
resolution, VRAM peak and errors, writes every image to disk and emits an HTML
comparison grid for human inspection.

    python tools/image_benchmark.py --out bench/run1
    python tools/image_benchmark.py --cases portrait,poster --only v2

Nothing here scores images automatically: the point is a grid a human can
judge.  Only the measurable columns are measured.
"""
from __future__ import annotations

import argparse
import html
import json
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.image_edit_graph import plan_edit  # noqa: E402
from jarvis.image_quality import RenderPlanner  # noqa: E402
from jarvis.image_runtime import (  # noqa: E402
    ComfyClient, ImageRenderer, gpu_free_mb,
)
from jarvis.zimage_adapter import ZImageTurboAdapter  # noqa: E402

SEED = 776611


@dataclass
class BenchCase:
    id: str
    request: str
    mode: str = "QUALITY"
    edit_operation: str = ""
    edit_source: str = ""


CASES: list[BenchCase] = [
    BenchCase("portrait", "portrait réaliste d'un vieux pêcheur breton, visage buriné, "
                          "regard direct, fin d'après-midi sur le port", "QUALITY"),
    BenchCase("poster_gaming", "affiche gaming pour un tournoi d'esport, chevalier cyber "
                               "en armure néon devant une arène", "ULTRA"),
    BenchCase("product", "visuel produit d'un flacon de parfum en verre ambré sur fond crème",
              "QUALITY"),
    BenchCase("landscape", "paysage cinématique de falaises islandaises sous un ciel d'orage, "
                           "lumière rasante", "QUALITY"),
    BenchCase("ui_concept", "concept UI d'une application de finance personnelle, thème sombre, "
                            "graphiques et cartes", "QUALITY"),
    BenchCase("character", "personnage stylisé, jeune alchimiste avec une cape verte et une "
                           "lanterne, illustration", "QUALITY"),
    BenchCase("multi_subject", "trois musiciens de rue jouant ensemble sur une place pavée, "
                               "plan large", "QUALITY"),
    # Maskless by design: masked operations (remove / replace background /
    # recolor) require a selection the harness cannot invent.
    BenchCase("edit_restyle", "refais cette image dans un style peinture à l'huile, "
                              "coups de pinceau visibles",
              "QUALITY", edit_operation="restyle"),
]


@dataclass
class BenchRow:
    case: str
    pipeline: str
    ok: bool = False
    seconds: float = 0.0
    width: int = 0
    height: int = 0
    vram_peak_mb: int = 0
    image: str = ""
    error: str = ""
    detail: dict = field(default_factory=dict)


def _png_size(data: bytes) -> tuple[int, int]:
    """Read dimensions straight from the IHDR chunk; avoids a Pillow dependency."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return (0, 0)
    return (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"))


def run_v1(case: BenchCase, client: ComfyClient, out_dir: Path) -> BenchRow:
    """The legacy path: the frozen golden workflow, 8 steps, 1024x1024."""
    row = BenchRow(case=case.id, pipeline="v1")
    if case.edit_operation:
        row.error = "V1 : l'édition exige un checkpoint SDXL, absent de cette installation."
        return row
    renderer = ImageRenderer(client.base)
    try:
        graph = ZImageTurboAdapter().compile(case.request, width=1024, height=1024, seed=SEED)
        result = renderer._execute(graph, budget_s=300, expected_stages=["GENERATING"])
        path = out_dir / f"{case.id}__v1.png"
        path.write_bytes(result.images[0])
        width, height = _png_size(result.images[0])
        row.ok, row.seconds, row.image = True, result.seconds, path.name
        row.width, row.height, row.vram_peak_mb = width, height, result.vram_peak_mb
        row.detail = {"steps": 8, "passes": 1, "prompt": case.request}
    except Exception as exc:
        row.error = f"{type(exc).__name__}: {exc}"[:400]
    return row


def run_v2(case: BenchCase, client: ComfyClient, out_dir: Path,
           edit_source: str = "") -> BenchRow:
    row = BenchRow(case=case.id, pipeline="v2")
    renderer = ImageRenderer(client.base)
    try:
        if case.edit_operation:
            if not edit_source:
                row.error = "Pas d'image source disponible pour le cas d'édition."
                return row
            uploaded = client.upload_image(edit_source)
            # Masked operations need a real selection.  No segmentation model is
            # installed, and feeding the source image in as its own mask would
            # produce a meaningless edit that looks like a success.  The
            # benchmark therefore exercises the maskless operation honestly.
            plan = plan_edit(case.edit_operation, source_image=uploaded,
                             prompt=case.request, seed=SEED)
            result = renderer.edit(plan, filename_prefix=f"bench_{case.id}")
            row.detail = {"operation": plan.operation, "denoise": plan.denoise,
                          "confidence": plan.confidence}
        else:
            plan = RenderPlanner().plan(case.request, requested_mode=case.mode,
                                        seed=SEED, total_vram_mb=10240,
                                        free_vram_mb=gpu_free_mb())
            result = renderer.render(plan, filename_prefix=f"bench_{case.id}")
            row.detail = {"mode": plan.mode, "image_type": plan.image_type,
                          "steps": plan.steps, "passes": 2 if plan.hires_width else 1,
                          "prompt": plan.prompt,
                          "downgraded_from": result.downgraded_from}
        path = out_dir / f"{case.id}__v2.png"
        path.write_bytes(result.images[-1])
        width, height = _png_size(result.images[-1])
        row.ok, row.seconds, row.image = True, result.seconds, path.name
        row.width, row.height, row.vram_peak_mb = width, height, result.vram_peak_mb
    except Exception as exc:
        row.error = f"{type(exc).__name__}: {exc}"[:400]
    return row


def write_grid(rows: list[BenchRow], out_dir: Path) -> Path:
    by_case: dict[str, dict[str, BenchRow]] = {}
    for row in rows:
        by_case.setdefault(row.case, {})[row.pipeline] = row

    def cell(row: BenchRow | None) -> str:
        if row is None:
            return "<td class='miss'>non exécuté</td>"
        if not row.ok:
            return f"<td class='fail'><div class='err'>{html.escape(row.error)}</div></td>"
        meta = (f"{row.width}×{row.height} · {row.seconds:.1f}s · "
                f"VRAM {row.vram_peak_mb} Mo")
        extra = ""
        if row.detail.get("downgraded_from"):
            extra = (f"<div class='warn'>rétrogradé depuis "
                     f"{html.escape(row.detail['downgraded_from'])}</div>")
        if row.detail.get("mode"):
            extra += (f"<div class='tag'>{html.escape(str(row.detail['mode']))} · "
                      f"{html.escape(str(row.detail.get('image_type', '')))} · "
                      f"{row.detail.get('passes', 1)} passe(s)</div>")
        return (f"<td><a href='{row.image}'><img src='{row.image}' loading='lazy'></a>"
                f"<div class='meta'>{meta}</div>{extra}</td>")

    body = []
    for case_id, entries in by_case.items():
        body.append(
            f"<tr><th>{html.escape(case_id)}</th>{cell(entries.get('v1'))}{cell(entries.get('v2'))}</tr>")

    ok_v1 = [r for r in rows if r.pipeline == "v1" and r.ok]
    ok_v2 = [r for r in rows if r.pipeline == "v2" and r.ok]

    def avg(values: list[float]) -> str:
        return f"{sum(values) / len(values):.1f}" if values else "—"

    summary = (
        f"<p class='sum'>V1 : {len(ok_v1)}/{len([r for r in rows if r.pipeline=='v1'])} réussis, "
        f"{avg([r.seconds for r in ok_v1])} s en moyenne, "
        f"{avg([float(r.width * r.height) / 1e6 for r in ok_v1])} MP. &nbsp;·&nbsp; "
        f"V2 : {len(ok_v2)}/{len([r for r in rows if r.pipeline=='v2'])} réussis, "
        f"{avg([r.seconds for r in ok_v2])} s en moyenne, "
        f"{avg([float(r.width * r.height) / 1e6 for r in ok_v2])} MP.</p>")

    page = f"""<!doctype html><meta charset="utf-8">
<title>Benchmark image JARVIS — V1 vs V2</title>
<style>
 body{{font:14px system-ui,sans-serif;margin:24px;background:#12141a;color:#e6e8ee}}
 h1{{font-size:20px}} table{{border-collapse:collapse;width:100%}}
 th,td{{border:1px solid #2a2f3a;padding:10px;vertical-align:top;text-align:center}}
 th{{background:#1b1f29;text-align:left;white-space:nowrap}}
 img{{max-width:420px;width:100%;border-radius:6px;display:block}}
 .meta{{margin-top:6px;color:#9aa3b5;font-size:12px}}
 .tag{{margin-top:4px;color:#7fd1c1;font-size:12px}}
 .warn{{margin-top:4px;color:#e0b050;font-size:12px}}
 .fail{{background:#2a1a1a}} .err{{color:#ff9b9b;font-size:12px;text-align:left}}
 .miss{{color:#666}} .sum{{color:#9aa3b5}}
 thead th{{text-align:center}}
</style>
<h1>Benchmark image JARVIS — ancien pipeline (V1) vs nouveau (V2)</h1>
{summary}
<table><thead><tr><th>Cas</th><th>V1 — Z-Image golden, 8 steps, 1024²</th>
<th>V2 — profils qualité, hi-res fix, ESRGAN</th></tr></thead>
<tbody>{''.join(body)}</tbody></table>
"""
    path = out_dir / "index.html"
    path.write_text(page, encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="bench/latest")
    parser.add_argument("--base-url", default="http://127.0.0.1:8188")
    parser.add_argument("--cases", default="", help="comma-separated case ids")
    parser.add_argument("--only", default="both", choices=["both", "v1", "v2"])
    args = parser.parse_args()

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    client = ComfyClient(args.base_url)
    client.health()

    wanted = {c.strip() for c in args.cases.split(",") if c.strip()}
    cases = [c for c in CASES if not wanted or c.id in wanted]

    # A partial re-run (--cases edit_restyle) has no freshly rendered image to
    # edit, so reuse one already sitting in the output directory.
    rows: list[BenchRow] = []
    existing = sorted(out_dir.glob("*__v2.png")) or sorted(out_dir.glob("*__v1.png"))
    edit_source = str(existing[0]) if existing else ""
    for case in cases:
        print(f"[{case.id}]", flush=True)
        if args.only in {"both", "v1"}:
            row = run_v1(case, client, out_dir)
            rows.append(row)
            print(f"  v1 {'ok' if row.ok else 'FAIL'} {row.seconds}s {row.error}", flush=True)
            if row.ok and not edit_source:
                edit_source = str(out_dir / row.image)
        if args.only in {"both", "v2"}:
            row = run_v2(case, client, out_dir, edit_source=edit_source)
            rows.append(row)
            print(f"  v2 {'ok' if row.ok else 'FAIL'} {row.seconds}s {row.error}", flush=True)
            if row.ok and not edit_source:
                edit_source = str(out_dir / row.image)

    # Merge with any previous run so re-running a single case updates the grid
    # instead of reducing it to that one row.
    merged: dict[tuple[str, str], dict] = {}
    previous = out_dir / "results.json"
    if previous.exists():
        try:
            for item in json.loads(previous.read_text(encoding="utf-8")):
                merged[(item["case"], item["pipeline"])] = item
        except Exception:
            pass
    for row in rows:
        merged[(row.case, row.pipeline)] = asdict(row)
    ordered = [merged[key] for key in sorted(
        merged, key=lambda k: ([c.id for c in CASES].index(k[0])
                               if k[0] in [c.id for c in CASES] else 99, k[1]))]
    previous.write_text(json.dumps(ordered, indent=2, ensure_ascii=False), encoding="utf-8")
    grid = write_grid([BenchRow(**item) for item in ordered], out_dir)
    print(f"\nGrille : {grid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
