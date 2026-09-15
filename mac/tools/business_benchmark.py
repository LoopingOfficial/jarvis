"""Benchmark JARVIS on its real use cases, not on generic portraits.

Drives the production V2 path (RenderPlanner → ZImageGraphBuilder →
ImageRenderer) so what is measured is what the user actually gets.  Records
every parameter, then writes a labelled grid and a blind A/B/C/D sheet.

    python tools/business_benchmark.py --out bench/business
    python tools/business_benchmark.py --cases giveaway,machine --modes QUALITY,ULTRA
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.image_edit_graph import plan_edit  # noqa: E402
from jarvis.image_quality import RenderPlanner  # noqa: E402
from jarvis.image_runtime import ComfyClient, ImageRenderer, gpu_free_mb  # noqa: E402

SEED = 913377


@dataclass
class Case:
    id: str
    title: str
    request: str
    modes: tuple[str, ...] = ("QUALITY",)
    image_type: str = ""
    edit_operation: str = ""
    checklist: tuple[str, ...] = ()


CASES: list[Case] = [
    Case("giveaway", "Giveaway gaming vertical",
         "Affiche verticale pour un giveaway dans un univers de jeu créatif cartoon. "
         "Un coffre au trésor lumineux flotte au centre, entouré de personnages "
         "cartoon stylisés et de confettis. Couleurs vives, ambiance festive. "
         "Laisse le tiers supérieur et le bas dégagés pour le texte.",
         modes=("BALANCED", "QUALITY", "ULTRA"), image_type="POSTER",
         checklist=("composition", "profondeur", "lisibilité", "éclairage",
                    "espace texte", "rendu gaming", "matériaux")),
    Case("machine", "Eternal Machine (symétrie)",
         "Une machine futuriste vue strictement de face, parfaitement symétrique et "
         "centrée. Panneaux de métal brossé avec joints et boulons visibles, fines "
         "bandes de néon cyan le long de la structure. Éclairage de studio régulier, "
         "aucune déformation mécanique.",
         modes=("QUALITY", "ULTRA"), image_type="PRODUCT",
         checklist=("symétrie", "matériaux", "néons", "détails fins",
                    "aucune déformation", "centrage")),
    Case("discord", "Bannière Discord",
         "Bannière horizontale large pour un serveur Discord gaming. Un vaisseau "
         "spatial stylisé sur la gauche, nébuleuse colorée à droite. Le sujet est "
         "entier et non coupé, avec de l'espace libre pour un titre.",
         modes=("BALANCED", "QUALITY"), image_type="GAMING",
         checklist=("composition large", "sujet non coupé", "espace titre",
                    "profondeur", "lisibilité en petit")),
    Case("asset", "Asset PNG détouré",
         "Un casque de gaming futuriste isolé, objet entier, vu de trois quarts, "
         "sur un fond uni parfaitement neutre. Contours nets et propres, aucun "
         "élément coupé.",
         modes=("QUALITY",), image_type="PRODUCT",
         checklist=("fond détourable", "contours propres", "aucun halo",
                    "sujet entier", "définition")),
    Case("character", "Personnage gaming complet",
         "Personnage de jeu vidéo en pied, une exploratrice en tenue technique avec "
         "une veste à capuche et des gants, les deux mains visibles et posées le "
         "long du corps, expression calme, vue de face.",
         modes=("QUALITY", "ULTRA"), image_type="GAMING",
         checklist=("anatomie", "mains", "visage", "vêtements", "matériaux",
                    "cohérence")),
    Case("poster_text", "Affiche à zones de texte",
         "Affiche d'événement verticale. Un stade illuminé vu en contre-plongée "
         "occupe la moitié basse, ciel dégagé au-dessus. Composition en trois "
         "bandes : une large zone vide en haut pour un titre, le visuel au centre, "
         "une bande sombre unie en bas pour les informations pratiques.",
         modes=("QUALITY", "ULTRA"), image_type="POSTER",
         checklist=("zones réservées", "hiérarchie", "espace non rempli",
                    "composable en post-traitement")),
]

EDIT_CASES: list[Case] = [
    Case("restyle", "Restyle (fidélité au sujet)",
         "Garde exactement le sujet et sa géométrie, transforme l'ambiance en "
         "peinture à l'huile nocturne avec des coups de pinceau visibles.",
         edit_operation="restyle",
         checklist=("sujet identique", "géométrie identique", "style transféré")),
    Case("background", "Changement de fond",
         "Garde exactement le sujet. Remplace uniquement l'arrière-plan par une "
         "plage tropicale au coucher du soleil.",
         edit_operation="replace_background",
         checklist=("sujet intact", "fond remplacé", "bords propres",
                    "lumière cohérente")),
]


@dataclass
class Row:
    case: str
    title: str
    mode: str
    ok: bool = False
    seconds: float = 0.0
    vram_peak_mb: int = 0
    image: str = ""
    error: str = ""
    checklist: list[str] = field(default_factory=list)
    params: dict = field(default_factory=dict)


def _png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return (0, 0)
    return (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"))


def run_generate(case: Case, mode: str, client: ComfyClient, out_dir: Path) -> Row:
    row = Row(case=case.id, title=case.title, mode=mode, checklist=list(case.checklist))
    try:
        plan = RenderPlanner().plan(case.request, requested_mode=mode,
                                    image_type=case.image_type, seed=SEED,
                                    total_vram_mb=10240, free_vram_mb=gpu_free_mb())
        renderer = ImageRenderer(client.base)
        result = renderer.render(plan, filename_prefix=f"biz_{case.id}")
        slug = f"{case.id}__{mode}.png"
        (out_dir / slug).write_bytes(result.images[-1])
        w, h = _png_size(result.images[-1])
        row.ok, row.seconds, row.image = True, result.seconds, slug
        row.vram_peak_mb = result.vram_peak_mb
        row.params = {
            "user_request": case.request, "final_prompt": plan.prompt,
            "image_type": plan.image_type, "seed": plan.seed, "steps": plan.steps,
            "sampler": plan.sampler, "scheduler": plan.scheduler, "shift": plan.shift,
            "cfg": plan.cfg, "base": f"{plan.width}×{plan.height}",
            "final": f"{w}×{h}", "hires": f"{plan.hires_width}×{plan.hires_height}"
                                            if plan.hires_width else "—",
            "hires_denoise": plan.hires_denoise, "post_upscale": plan.post_upscale,
            "sharpen": plan.sharpen,
            "upscale_model": result.metadata.get("upscale_model") or "—",
            "downgraded_from": result.downgraded_from,
        }
    except Exception as exc:
        row.error = f"{type(exc).__name__}: {exc}"[:400]
    return row


def run_edit(case: Case, source: str, client: ComfyClient, out_dir: Path) -> Row:
    row = Row(case=case.id, title=case.title, mode="EDIT", checklist=list(case.checklist))
    try:
        uploaded = client.upload_image(source)
        plan = plan_edit(case.edit_operation, source_image=uploaded,
                         prompt=case.request, seed=SEED)
        renderer = ImageRenderer(client.base)
        result = renderer.edit(plan, filename_prefix=f"biz_{case.id}")
        slug = f"{case.id}__edit.png"
        (out_dir / slug).write_bytes(result.images[-1])
        w, h = _png_size(result.images[-1])
        row.ok, row.seconds, row.image = True, result.seconds, slug
        row.vram_peak_mb = result.vram_peak_mb
        row.params = {"user_request": case.request, "operation": plan.operation,
                      "denoise": plan.denoise, "steps": plan.steps, "seed": plan.seed,
                      "final": f"{w}×{h}", "confidence": plan.confidence,
                      "source": Path(source).name, "notes": ", ".join(plan.notes) or "—"}
    except Exception as exc:
        row.error = f"{type(exc).__name__}: {exc}"[:400]
    return row


_STYLE = """
 body{font:14px system-ui,sans-serif;margin:24px;background:#12141a;color:#e6e8ee}
 h1{font-size:20px} h2{font-size:16px;margin-top:32px;border-bottom:1px solid #2a2f3a;padding-bottom:6px}
 .sheet{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(320px,1fr))}
 figure{margin:0;background:#171b24;border:1px solid #2a2f3a;border-radius:10px;padding:10px}
 img{width:100%;border-radius:6px;display:block}
 figcaption{margin-top:8px;font-weight:600}
 .meta{color:#9aa3b5;font-size:12px;margin-top:6px;line-height:1.5}
 .chk{color:#7fd1c1;font-size:12px;margin-top:6px}
 .prompt{color:#b9c0cf;font-size:12px;margin-top:6px;line-height:1.45}
 .fail{color:#ff9b9b;font-size:12px}
 .warn{color:#e0b050;font-size:12px}
 details summary{cursor:pointer;color:#9aa3b5;font-size:12px}
 .hidden-params{display:none} .revealed .hidden-params{display:block}
 button.reveal{margin:10px 0;padding:7px 16px;border-radius:8px;border:1px solid #2a2f3a;
   background:transparent;color:#5ad1c3;cursor:pointer}
"""


def write_grid(rows: list[Row], out_dir: Path) -> Path:
    groups: dict[str, list[Row]] = {}
    for row in rows:
        groups.setdefault(row.case, []).append(row)
    parts = []
    for case_id, items in groups.items():
        cards = []
        for row in items:
            if not row.ok:
                cards.append(f"<figure><figcaption>{html.escape(row.mode)}</figcaption>"
                             f"<div class='fail'>{html.escape(row.error)}</div></figure>")
                continue
            p = row.params
            meta = "<br>".join(f"{html.escape(k)} : {html.escape(str(v))}"
                               for k, v in p.items() if k not in {"final_prompt", "user_request"})
            warn = (f"<div class='warn'>rétrogradé depuis {html.escape(p['downgraded_from'])}</div>"
                    if p.get("downgraded_from") else "")
            cards.append(
                f"<figure><a href='{row.image}' target='_blank'>"
                f"<img src='{row.image}' loading='lazy'></a>"
                f"<figcaption>{html.escape(row.mode)} — {row.seconds:.1f}s · "
                f"VRAM {row.vram_peak_mb} Mo</figcaption>{warn}"
                f"<div class='chk'>À juger : {html.escape(', '.join(row.checklist))}</div>"
                f"<details><summary>paramètres &amp; prompt</summary>"
                f"<div class='meta'>{meta}</div>"
                f"<div class='prompt'><b>prompt final</b> : "
                f"{html.escape(str(p.get('final_prompt', '')))}</div></details></figure>")
        parts.append(f"<h2>{html.escape(items[0].title)}</h2>"
                     f"<div class='sheet'>{''.join(cards)}</div>")
    page = (f"<!doctype html><meta charset='utf-8'><title>Benchmark métier JARVIS</title>"
            f"<style>{_STYLE}</style><h1>Benchmark métier — cas d'usage réels</h1>"
            f"<p class='meta'>Seed fixe {SEED}. Pour juger sans biais, ouvre d'abord "
            f"<a href='blind.html'>blind.html</a>.</p>{''.join(parts)}")
    path = out_dir / "index.html"
    path.write_text(page, encoding="utf-8")
    return path


def write_blind(rows: list[Row], out_dir: Path) -> Path:
    groups: dict[str, list[Row]] = {}
    for row in rows:
        if row.ok and len([r for r in rows if r.case == row.case]) > 1:
            groups.setdefault(row.case, []).append(row)
    parts = []
    for i, (case_id, items) in enumerate(groups.items()):
        cards = []
        for n, row in enumerate(items):
            hidden = (f"{row.mode} · {row.seconds:.1f}s · VRAM {row.vram_peak_mb} Mo · "
                      f"{row.params.get('final', '')}")
            cards.append(f"<figure><a href='{row.image}' target='_blank'>"
                         f"<img src='{row.image}' loading='lazy'></a>"
                         f"<figcaption>{chr(65 + n)}</figcaption>"
                         f"<div class='meta hidden-params'>{html.escape(hidden)}</div></figure>")
        parts.append(f"<h2>{html.escape(items[0].title)}</h2>"
                     f"<button class='reveal' data-target='g{i}'>Révéler</button>"
                     f"<div class='sheet' id='g{i}'>{''.join(cards)}</div>")
    page = (f"<!doctype html><meta charset='utf-8'><title>Benchmark métier — aveugle</title>"
            f"<style>{_STYLE}</style><h1>Comparaison aveugle — cas métier</h1>"
            f"<p class='meta'>Choisis avant de révéler. Plus lent n'est pas meilleur.</p>"
            f"{''.join(parts)}"
            "<script>document.querySelectorAll('button.reveal').forEach(b=>"
            "b.addEventListener('click',()=>document.getElementById(b.dataset.target)"
            ".classList.toggle('revealed')));</script>")
    path = out_dir / "blind.html"
    path.write_text(page, encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="bench/business")
    parser.add_argument("--cases", default="")
    parser.add_argument("--modes", default="")
    parser.add_argument("--base-url", default="http://127.0.0.1:8188")
    parser.add_argument("--edit-source", default="")
    args = parser.parse_args()

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    client = ComfyClient(args.base_url)
    client.health()

    wanted = {c.strip() for c in args.cases.split(",") if c.strip()}
    forced = tuple(m.strip().upper() for m in args.modes.split(",") if m.strip())

    rows: list[Row] = []
    edit_source = args.edit_source
    for case in CASES:
        if wanted and case.id not in wanted:
            continue
        for mode in (forced or case.modes):
            print(f"[{case.id}] {mode}", flush=True)
            row = run_generate(case, mode, client, out_dir)
            rows.append(row)
            print(f"    {'ok' if row.ok else 'FAIL'} {row.seconds:.1f}s {row.error}", flush=True)
            if row.ok and not edit_source and case.id == "character":
                edit_source = str(out_dir / row.image)

    if not edit_source:
        existing = sorted(out_dir.glob("character__*.png")) or sorted(out_dir.glob("*.png"))
        edit_source = str(existing[0]) if existing else ""

    for case in EDIT_CASES:
        if wanted and case.id not in wanted:
            continue
        if not edit_source:
            rows.append(Row(case=case.id, title=case.title, mode="EDIT",
                            error="Aucune image source disponible."))
            continue
        print(f"[{case.id}] EDIT", flush=True)
        row = run_edit(case, edit_source, client, out_dir)
        rows.append(row)
        print(f"    {'ok' if row.ok else 'FAIL'} {row.seconds:.1f}s {row.error}", flush=True)

    merged: dict[tuple[str, str], dict] = {}
    results = out_dir / "results.json"
    if results.exists():
        try:
            for item in json.loads(results.read_text(encoding="utf-8")):
                merged[(item["case"], item["mode"])] = item
        except Exception:
            pass
    for row in rows:
        merged[(row.case, row.mode)] = asdict(row)
    ordered = list(merged.values())
    results.write_text(json.dumps(ordered, indent=2, ensure_ascii=False), encoding="utf-8")

    all_rows = [Row(**item) for item in ordered]
    print("\nGrille :", write_grid(all_rows, out_dir))
    print("Aveugle:", write_blind(all_rows, out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
