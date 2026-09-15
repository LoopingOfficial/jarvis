"""Smoke test the integrated pipeline through JARVIS's own HTTP API.

Deliberately not the benchmark harness: this drives the same routes the UI
calls, so it proves the integration, not the pipeline (already validated in
Quality Validation V2).

    python tools/smoke_integration.py --base http://127.0.0.1:8766
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def post(base: str, path: str, body: dict, timeout: int = 900) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as fh:
            return json.loads(fh.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            return {**json.loads(detail), "http_status": exc.code}
        except Exception:
            return {"ok": False, "error": detail[:300], "http_status": exc.code}


def get_bytes(base: str, path: str, timeout: int = 120) -> bytes:
    with urllib.request.urlopen(base + path, timeout=timeout) as fh:
        return fh.read()


def png_size(data: bytes) -> tuple[int, int]:
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"))
    return (0, 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8766")
    parser.add_argument("--out", default="bench/smoke")
    args = parser.parse_args()

    base = args.base.rstrip("/")
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    source_job = ""

    def record(name: str, ok: bool, detail: dict) -> None:
        results.append({"test": name, "ok": ok, **detail})
        flag = "ok  " if ok else "FAIL"
        print(f"  {flag} {name}: {detail.get('summary', detail.get('error', ''))}",
              flush=True)

    def generate(name: str, prompt: str, mode: str, save_as: str) -> str:
        print(f"[{name}]", flush=True)
        started = time.time()
        reply = post(base, "/api/images/generate",
                     {"prompt": prompt, "context": {"quality_mode": mode}})
        if not reply.get("ok"):
            record(name, False, {"error": str(reply.get("error"))[:200]})
            return ""
        job = reply["job"]
        meta = job.get("meta", {})
        data = get_bytes(base, f"/api/images/{job['id']}/file")
        (out / save_as).write_bytes(data)
        width, height = png_size(data)
        record(name, True, {
            "summary": (f"{meta.get('quality_mode')} · {width}×{height} · "
                        f"{float(meta.get('duration_s') or 0):.1f}s · seed {job.get('seed')}"),
            "job_id": job["id"], "pipeline_version": meta.get("pipeline_version"),
            "quality_mode": meta.get("quality_mode"),
            "image_type": meta.get("image_type"), "seed": job.get("seed"),
            "width": width, "height": height,
            "duration_s": meta.get("duration_s"),
            "downgraded_from": meta.get("downgraded_from") or "",
            "elapsed_s": round(time.time() - started, 1), "file": save_as})
        return job["id"]

    # A / B / C / D -- generation through the normal route
    poster_job = generate("A. Affiche giveaway (ULTRA)",
                          "Affiche verticale pour un giveaway gaming cartoon, coffre "
                          "lumineux au centre, confettis, tiers haut et bas dégagés",
                          "ULTRA", "A_poster.png")
    generate("B. Bannière Discord (BALANCED)",
             "Bannière horizontale large pour un serveur Discord gaming, vaisseau "
             "spatial stylisé à gauche, nébuleuse à droite, espace pour un titre",
             "BALANCED", "B_discord.png")
    generate("C. Image QUALITY",
             "Un vieux phare breton dans la tempête, vagues, lumière rasante",
             "QUALITY", "C_quality.png")
    source_job = generate("D. Image ULTRA",
                          "Portrait photoréaliste d'une exploratrice en tenue "
                          "technique, lumière douce de fin de journée",
                          "ULTRA", "D_ultra.png")

    # E / F / G / H -- Image Edit V3 on an existing job
    if source_job:
        for name, operation, prompt, save_as in (
            ("E. Changement de fond", "replace_background",
             "une plage tropicale au coucher du soleil", "E_background.png"),
            ("F. Détourage PNG", "cutout", "", "F_cutout.png"),
            ("G. Upscale", "upscale", "", "G_upscale.png"),
            ("H. Restyle", "restyle", "peinture à l'huile, coups de pinceau visibles",
             "H_restyle.png"),
        ):
            print(f"[{name}]", flush=True)
            started = time.time()
            reply = post(base, f"/api/images/{source_job}/edit",
                         {"operation": operation, "prompt": prompt})
            if not reply.get("ok"):
                record(name, False, {"error": str(reply.get("error"))[:200]})
                continue
            job = reply["job"]
            meta = job.get("meta", {})
            data = get_bytes(base, f"/api/images/{job['id']}/file")
            (out / save_as).write_bytes(data)
            width, height = png_size(data)
            record(name, True, {
                "summary": (f"{operation} · {width}×{height} · "
                            f"{float(meta.get('duration_s') or 0):.1f}s · "
                            f"seg={meta.get('model', {}).get('segmentation') or '—'}"),
                "job_id": job["id"], "pipeline_version": meta.get("pipeline_version"),
                "edit_operation": meta.get("edit_operation"),
                "segmentation": meta.get("model", {}).get("segmentation") or "",
                "width": width, "height": height,
                "duration_s": meta.get("duration_s"),
                "elapsed_s": round(time.time() - started, 1), "file": save_as})

    # I -- exact typography composited onto the poster
    if poster_job:
        name = "I. Affiche avec texte exact"
        print(f"[{name}]", flush=True)
        wanted = ["Giveaway Fortnite", "3 gagnants - 20 decembre",
                  "Abonne-toi + like + partage"]
        reply = post(base, f"/api/images/{poster_job}/poster-text", {"lines": wanted})
        if not reply.get("ok"):
            record(name, False, {"error": str(reply.get("error"))[:200]})
        else:
            data = get_bytes(base, f"/api/images/{poster_job}/poster/file")
            (out / "I_poster_text.png").write_bytes(data)
            width, height = png_size(data)
            record(name, True, {"summary": f"{width}×{height} · texte composé en PIL",
                                "lines": wanted, "width": width, "height": height,
                                "file": "I_poster_text.png"})

    # Refusal path: a named region must be declined, not approximated.
    if source_job:
        name = "J. Refus édition ciblée non supportée"
        print(f"[{name}]", flush=True)
        reply = post(base, f"/api/images/{source_job}/edit",
                     {"operation": "remove", "prompt": "enlève l'objet à gauche"})
        refused = (not reply.get("ok")) and reply.get("http_status") == 422
        record(name, refused, {
            "summary": "refusé proprement (422)" if refused else "",
            "error": "" if refused else f"attendu 422, reçu {reply.get('http_status')}",
            "message": str(reply.get("error"))[:160]})

    (out / "results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    passed = sum(1 for r in results if r["ok"])
    print(f"\n{passed}/{len(results)} smoke tests OK — {out}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
