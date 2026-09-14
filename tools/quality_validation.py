"""Quality Validation V2 — prove which settings actually improve the image.

Runs controlled sweeps where exactly one variable changes at a time, at a fixed
seed, and records every parameter plus time and VRAM.  Produces:

  * ``results.json``        — one row per variant, all parameters flattened
  * ``index.html``          — labelled comparison sheets
  * ``blind.html``          — the same variants as A/B/C/D, parameters hidden
                              until you pick one

The blind sheet exists because it is very easy to prefer the slowest variant
simply because it is labelled ULTRA.  Judge first, reveal after.

    python tools/quality_validation.py --test steps
    python tools/quality_validation.py --test all --out bench/qv2
"""
from __future__ import annotations

import argparse
import html
import json
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.image_runtime import ComfyClient, ImageRenderer  # noqa: E402
from jarvis.zimage_graph import (  # noqa: E402
    MODEL, TEXT_ENCODER, UPSCALE_MODEL, VAE,
)

SEED = 20260914

# Two subjects, chosen because they fail differently.  The portrait exposes
# skin/hair artefacts; the machine exposes geometry drift, which is what the
# hi-res pass is most likely to break.
SUBJECTS = {
    "portrait": (
        "Close-up portrait photograph of a 35 year old woman with freckles. "
        "Soft window light from the left, shallow depth of field, 85mm lens, "
        "calm expression, neutral grey background."),
    "machine": (
        "A futuristic machine seen straight on, perfectly symmetrical, standing "
        "in a dark workshop. Brushed metal panels with visible seams and bolts, "
        "thin cyan neon strips running along its frame. Even studio lighting."),
}


@dataclass
class Variant:
    """One point in a sweep.  Every field lands in results.json."""

    test: str
    label: str
    subject: str
    steps: int = 12
    width: int = 1024
    height: int = 1024
    shift: float = 3.0
    cfg: float = 1.0
    sampler: str = "res_multistep"
    scheduler: str = "simple"
    hires_scale: float = 0.0
    hires_denoise: float = 0.0
    hires_steps: int = 0
    upscaler: str = ""          # "" | "esrgan" | "lanczos"
    post_upscale: float = 0.0
    sharpen: float = 0.0


@dataclass
class Row:
    test: str
    label: str
    subject: str
    ok: bool = False
    seconds: float = 0.0
    vram_peak_mb: int = 0
    final_width: int = 0
    final_height: int = 0
    image: str = ""
    error: str = ""
    params: dict = field(default_factory=dict)


def build_graph(v: Variant, prompt: str) -> dict:
    """Assemble the graph for one variant.

    Written here rather than reusing ZImageGraphBuilder because the sweep must
    vary things the production builder deliberately fixes (sampler, shift,
    upscaler choice).  The conditioning core is identical.
    """
    g: dict = {
        "28": {"class_type": "UNETLoader",
               "inputs": {"unet_name": MODEL, "weight_dtype": "default"}},
        "30": {"class_type": "CLIPLoader",
               "inputs": {"clip_name": TEXT_ENCODER, "type": "lumina2", "device": "default"}},
        "29": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "27": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["30", 0]}},
        "33": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["27", 0]}},
        "11": {"class_type": "ModelSamplingAuraFlow",
               "inputs": {"model": ["28", 0], "shift": float(v.shift)}},
        "13": {"class_type": "EmptySD3LatentImage",
               "inputs": {"width": v.width, "height": v.height, "batch_size": 1}},
        "3": {"class_type": "KSampler", "inputs": {
            "seed": SEED, "steps": v.steps, "cfg": v.cfg,
            "sampler_name": v.sampler, "scheduler": v.scheduler, "denoise": 1.0,
            "model": ["11", 0], "positive": ["27", 0], "negative": ["33", 0],
            "latent_image": ["13", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["29", 0]}},
    }
    tail = ["8", 0]

    if v.hires_scale:
        tw, th = int(v.width * v.hires_scale), int(v.height * v.hires_scale)
        tw, th = tw - tw % 8, th - th % 8
        if v.upscaler == "lanczos":
            g["42"] = {"class_type": "ImageScale", "inputs": {
                "upscale_method": "lanczos", "width": tw, "height": th,
                "crop": "disabled", "image": tail}}
            tail = ["42", 0]
        else:
            g["40"] = {"class_type": "UpscaleModelLoader",
                       "inputs": {"model_name": UPSCALE_MODEL}}
            g["41"] = {"class_type": "ImageUpscaleWithModel",
                       "inputs": {"upscale_model": ["40", 0], "image": tail}}
            g["42"] = {"class_type": "ImageScale", "inputs": {
                "upscale_method": "lanczos", "width": tw, "height": th,
                "crop": "disabled", "image": ["41", 0]}}
            tail = ["42", 0]
        g["43"] = {"class_type": "VAEEncode", "inputs": {"pixels": tail, "vae": ["29", 0]}}
        g["44"] = {"class_type": "KSampler", "inputs": {
            "seed": SEED + 1, "steps": v.hires_steps or 10, "cfg": v.cfg,
            "sampler_name": v.sampler, "scheduler": v.scheduler,
            "denoise": v.hires_denoise, "model": ["11", 0],
            "positive": ["27", 0], "negative": ["33", 0], "latent_image": ["43", 0]}}
        g["45"] = {"class_type": "VAEDecode", "inputs": {"samples": ["44", 0], "vae": ["29", 0]}}
        tail = ["45", 0]

    if v.post_upscale:
        base_w = int(v.width * (v.hires_scale or 1))
        base_h = int(v.height * (v.hires_scale or 1))
        tw, th = int(base_w * v.post_upscale), int(base_h * v.post_upscale)
        if v.upscaler == "lanczos":
            g["52"] = {"class_type": "ImageScale", "inputs": {
                "upscale_method": "lanczos", "width": tw, "height": th,
                "crop": "disabled", "image": tail}}
        else:
            g["50"] = {"class_type": "UpscaleModelLoader",
                       "inputs": {"model_name": UPSCALE_MODEL}}
            g["51"] = {"class_type": "ImageUpscaleWithModel",
                       "inputs": {"upscale_model": ["50", 0], "image": tail}}
            g["52"] = {"class_type": "ImageScale", "inputs": {
                "upscale_method": "lanczos", "width": tw, "height": th,
                "crop": "disabled", "image": ["51", 0]}}
        tail = ["52", 0]

    if v.sharpen:
        g["53"] = {"class_type": "ImageSharpen", "inputs": {
            "image": tail, "sharpen_radius": 1, "sigma": 1.0, "alpha": float(v.sharpen)}}
        tail = ["53", 0]

    g["9"] = {"class_type": "SaveImage",
              "inputs": {"filename_prefix": f"qv2_{v.test}", "images": tail}}
    return g


# ---------------------------------------------------------------------------
# Sweeps — exactly one variable moves per test
# ---------------------------------------------------------------------------
def sweep_steps(subject: str) -> list[Variant]:
    """Test A — where does adding steps stop paying?"""
    return [Variant("steps", f"{n} steps", subject, steps=n) for n in (8, 10, 12, 16, 20)]


def sweep_hires(subject: str) -> list[Variant]:
    """Test B — the hi-res denoise curve, including the no-hi-res control."""
    out = [Variant("hires", "sans hi-res", subject, steps=12)]
    for dn in (0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50):
        out.append(Variant("hires", f"denoise {dn:.2f}", subject, steps=12,
                           hires_scale=1.5, hires_denoise=dn, hires_steps=10,
                           upscaler="esrgan"))
    return out


def sweep_base_resolution(subject: str) -> list[Variant]:
    """Test C — first-pass resolution; bigger is not assumed to be better."""
    out = []
    for size in (768, 896, 1024, 1152):
        out.append(Variant("resolution", f"base {size}", subject, steps=12,
                           width=size, height=size, hires_scale=1.5,
                           hires_denoise=0.30, hires_steps=10, upscaler="esrgan"))
    return out


def sweep_upscale(subject: str) -> list[Variant]:
    """Test D — does ESRGAN beat Lanczos, and does sharpening help at all?"""
    return [
        Variant("upscale", "aucun upscale", subject, steps=12),
        Variant("upscale", "lanczos seul", subject, steps=12,
                post_upscale=1.5, upscaler="lanczos"),
        Variant("upscale", "ESRGAN", subject, steps=12,
                post_upscale=1.5, upscaler="esrgan"),
        Variant("upscale", "ESRGAN + sharpen 0.10", subject, steps=12,
                post_upscale=1.5, upscaler="esrgan", sharpen=0.10),
        Variant("upscale", "ESRGAN + sharpen 0.25", subject, steps=12,
                post_upscale=1.5, upscaler="esrgan", sharpen=0.25),
    ]


SWEEPS = {
    "steps": sweep_steps,
    "hires": sweep_hires,
    "resolution": sweep_base_resolution,
    "upscale": sweep_upscale,
}


def _png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return (0, 0)
    return (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"))


def run_variant(v: Variant, client: ComfyClient, out_dir: Path) -> Row:
    row = Row(test=v.test, label=v.label, subject=v.subject, params=asdict(v))
    row.params["seed"] = SEED
    row.params["upscale_model"] = UPSCALE_MODEL if v.upscaler == "esrgan" else v.upscaler
    renderer = ImageRenderer(client.base)
    try:
        graph = build_graph(v, SUBJECTS[v.subject])
        result = renderer._execute(graph, budget_s=900, expected_stages=["GENERATING"])
        slug = f"{v.test}__{v.subject}__{v.label.replace(' ', '_').replace('.', '')}.png"
        (out_dir / slug).write_bytes(result.images[-1])
        row.final_width, row.final_height = _png_size(result.images[-1])
        row.ok, row.seconds, row.vram_peak_mb = True, result.seconds, result.vram_peak_mb
        row.image = slug
    except Exception as exc:
        row.error = f"{type(exc).__name__}: {exc}"[:400]
    return row


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
_STYLE = """
 body{font:14px system-ui,sans-serif;margin:24px;background:#12141a;color:#e6e8ee}
 h1{font-size:20px} h2{font-size:16px;margin-top:34px;border-bottom:1px solid #2a2f3a;padding-bottom:6px}
 .sheet{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}
 figure{margin:0;background:#171b24;border:1px solid #2a2f3a;border-radius:10px;padding:10px}
 img{width:100%;border-radius:6px;display:block;cursor:zoom-in}
 figcaption{margin-top:8px;font-size:13px}
 .meta{color:#9aa3b5;font-size:12px;margin-top:4px}
 .fail{color:#ff9b9b;font-size:12px}
 .hidden-params{display:none}
 .revealed .hidden-params{display:block}
 button.reveal{margin:10px 0;padding:7px 16px;border-radius:8px;border:1px solid #2a2f3a;
   background:transparent;color:#5ad1c3;cursor:pointer}
 .note{color:#9aa3b5;max-width:70ch;line-height:1.55}
"""


def write_labelled(rows: list[Row], out_dir: Path) -> Path:
    groups: dict[tuple[str, str], list[Row]] = {}
    for row in rows:
        groups.setdefault((row.test, row.subject), []).append(row)

    parts = []
    for (test, subject), items in groups.items():
        cards = []
        for row in items:
            if not row.ok:
                cards.append(f"<figure><figcaption>{html.escape(row.label)}</figcaption>"
                             f"<div class='fail'>{html.escape(row.error)}</div></figure>")
                continue
            p = row.params
            detail = (f"{row.final_width}×{row.final_height} · {row.seconds:.1f}s · "
                      f"VRAM {row.vram_peak_mb} Mo<br>"
                      f"steps {p['steps']} · base {p['width']}×{p['height']} · "
                      f"shift {p['shift']} · cfg {p['cfg']}<br>"
                      f"hi-res ×{p['hires_scale']} dn {p['hires_denoise']} "
                      f"({p['hires_steps']} st) · post ×{p['post_upscale']} · "
                      f"sharpen {p['sharpen']} · {html.escape(str(p['upscale_model'] or '—'))}")
            cards.append(f"<figure><a href='{row.image}' target='_blank'>"
                         f"<img src='{row.image}' loading='lazy'></a>"
                         f"<figcaption>{html.escape(row.label)}</figcaption>"
                         f"<div class='meta'>{detail}</div></figure>")
        parts.append(f"<h2>{html.escape(test)} — {html.escape(subject)}</h2>"
                     f"<div class='sheet'>{''.join(cards)}</div>")

    page = (f"<!doctype html><meta charset='utf-8'><title>Quality Validation V2</title>"
            f"<style>{_STYLE}</style><h1>Quality Validation V2 — planches annotées</h1>"
            f"<p class='note'>Une seule variable change par planche. Seed fixe "
            f"{SEED}. Pour un jugement non biaisé, ouvre d'abord "
            f"<a href='blind.html'>blind.html</a>.</p>{''.join(parts)}")
    path = out_dir / "index.html"
    path.write_text(page, encoding="utf-8")
    return path


def write_blind(rows: list[Row], out_dir: Path) -> Path:
    """Same images, labelled A/B/C/D, parameters revealed only on demand."""
    groups: dict[tuple[str, str], list[Row]] = {}
    for row in rows:
        if row.ok:
            groups.setdefault((row.test, row.subject), []).append(row)

    parts = []
    for index, ((test, subject), items) in enumerate(groups.items()):
        cards = []
        for n, row in enumerate(items):
            p = row.params
            hidden = (f"{html.escape(row.label)} — steps {p['steps']}, "
                      f"base {p['width']}, hi-res ×{p['hires_scale']} dn {p['hires_denoise']}, "
                      f"post ×{p['post_upscale']}, sharpen {p['sharpen']}, "
                      f"{row.seconds:.1f}s, VRAM {row.vram_peak_mb} Mo")
            cards.append(f"<figure><a href='{row.image}' target='_blank'>"
                         f"<img src='{row.image}' loading='lazy'></a>"
                         f"<figcaption>{chr(65 + n)}</figcaption>"
                         f"<div class='meta hidden-params'>{hidden}</div></figure>")
        parts.append(
            f"<h2>{html.escape(test)} — {html.escape(subject)}</h2>"
            f"<button class='reveal' data-target='g{index}'>Révéler les paramètres</button>"
            f"<div class='sheet' id='g{index}'>{''.join(cards)}</div>")

    page = (f"<!doctype html><meta charset='utf-8'><title>Comparaison aveugle</title>"
            f"<style>{_STYLE}</style><h1>Comparaison aveugle</h1>"
            f"<p class='note'>Choisis la meilleure image de chaque planche <em>avant</em> "
            f"de révéler ses réglages. Plus lent n'est pas meilleur.</p>{''.join(parts)}"
            "<script>document.querySelectorAll('button.reveal').forEach(b=>"
            "b.addEventListener('click',()=>document.getElementById(b.dataset.target)"
            ".classList.toggle('revealed')));</script>")
    path = out_dir / "blind.html"
    path.write_text(page, encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", default="all",
                        choices=[*SWEEPS, "all"])
    parser.add_argument("--subjects", default="portrait,machine")
    parser.add_argument("--out", default="bench/qv2")
    parser.add_argument("--base-url", default="http://127.0.0.1:8188")
    args = parser.parse_args()

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    client = ComfyClient(args.base_url)
    client.health()

    subjects = [s.strip() for s in args.subjects.split(",") if s.strip() in SUBJECTS]
    tests = list(SWEEPS) if args.test == "all" else [args.test]

    variants: list[Variant] = []
    for test in tests:
        for subject in subjects:
            variants.extend(SWEEPS[test](subject))

    rows: list[Row] = []
    for i, variant in enumerate(variants, 1):
        print(f"[{i}/{len(variants)}] {variant.test} · {variant.subject} · {variant.label}",
              flush=True)
        row = run_variant(variant, client, out_dir)
        rows.append(row)
        print(f"    {'ok' if row.ok else 'FAIL'} {row.seconds:.1f}s "
              f"{row.final_width}x{row.final_height} {row.error}", flush=True)

    # Merge with previous runs so a single sweep can be re-run on its own.
    merged: dict[tuple[str, str, str], dict] = {}
    results = out_dir / "results.json"
    if results.exists():
        try:
            for item in json.loads(results.read_text(encoding="utf-8")):
                merged[(item["test"], item["subject"], item["label"])] = item
        except Exception:
            pass
    for row in rows:
        merged[(row.test, row.subject, row.label)] = asdict(row)
    ordered = list(merged.values())
    results.write_text(json.dumps(ordered, indent=2, ensure_ascii=False), encoding="utf-8")

    all_rows = [Row(**item) for item in ordered]
    print("\nPlanches :", write_labelled(all_rows, out_dir))
    print("Aveugle  :", write_blind(all_rows, out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
