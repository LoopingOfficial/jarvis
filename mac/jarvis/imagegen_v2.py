"""V2 generation path wired into :class:`jarvis.imagegen.ImageGenManager`.

Added alongside the V1 path rather than replacing it: V1 stays reachable via
``Settings → Image → pipeline_version = "v1"`` until the new pipeline has been
validated in daily use.  Both write the same job records, so the UI, history
and sidecars are unchanged.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .image_edit_graph import EDIT_RECIPES, detect_edit_operation, plan_edit
from .image_quality import (
    IMAGE_QUALITY_BUILD_ID, QUALITY_MODES, RenderPlanner, TYPE_PIPELINES,
)
from .image_runtime import (
    ImageGenerationFailed, ImageGenerationTimeout, ImageRenderer, gpu_free_mb,
)

PIPELINE_VERSION = "v2"


def _total_vram_mb() -> int | None:
    import subprocess
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return int(proc.stdout.strip().splitlines()[0])
    except Exception:
        return None


class ImageGenV2:
    """Run V2 plans using the host manager's job bookkeeping."""

    def __init__(self, manager: Any) -> None:
        self.m = manager
        self.planner = RenderPlanner()

    # -- helpers ------------------------------------------------------------
    def _settings(self) -> dict[str, Any]:
        try:
            return self.m._core.settings.section("image") or {}
        except Exception:
            return {}

    def _base_url(self, backend: dict[str, Any]) -> str:
        return str(backend.get("base_url") or "http://127.0.0.1:8188").rstrip("/")

    def _progress(self, job: dict[str, Any]):
        def emit(stage: str, progress: float, extra: dict[str, Any]) -> None:
            job["stage"] = stage
            job["progress"] = round(progress, 3)
            job["meta"]["stage_detail"] = extra
            self.m._set(job, stage=stage, progress=progress)
            self.m._emit("image.generation.progress", job, stage=stage,
                         progress=progress, **extra)
        return emit

    # -- public -------------------------------------------------------------
    def generate(self, job: dict[str, Any], backend: dict[str, Any], *,
                 request: str, mode: str = "generate", quality_mode: str = "",
                 image_type: str = "", width: int = 0, height: int = 0,
                 seed: int = 0, source_path: str = "",
                 user_negative: str = "") -> dict[str, Any]:
        settings = self._settings()
        renderer = ImageRenderer(self._base_url(backend), on_progress=self._progress(job))

        if mode in {"edit", "improve", "inpaint", "outpaint", "upscale"} and source_path:
            return self._run_edit(job, renderer, request=request, mode=mode,
                                  source_path=source_path, seed=seed)

        default_mode = str(settings.get("default_quality") or "BALANCED").upper()
        if default_mode not in QUALITY_MODES:
            default_mode = "BALANCED"
        plan = self.planner.plan(
            request, requested_mode=quality_mode, image_type=image_type,
            seed=seed, width=width, height=height,
            total_vram_mb=_total_vram_mb(), free_vram_mb=gpu_free_mb(),
            user_negative=user_negative, default_mode=default_mode)

        job["prompt"] = plan.prompt[:2000]
        job["width"], job["height"] = plan.final_width, plan.final_height
        job["steps"], job["seed"] = plan.steps, plan.seed
        job["meta"].update({
            "pipeline_version": PIPELINE_VERSION,
            "build_id": IMAGE_QUALITY_BUILD_ID,
            "original_prompt": request,
            "final_prompt": plan.prompt,
            "quality_mode": plan.mode,
            "quality_mode_reason": plan.mode_reason,
            "image_type": plan.image_type,
            "image_type_label": TYPE_PIPELINES[plan.image_type].label,
            "plan": plan.as_dict(),
            "stages": plan.stages,
        })
        self.m._save(job)

        result = renderer.render(plan, filename_prefix=f"jarvis_{job['id']}")
        job["meta"].update({
            "model": result.metadata, "workflow": "zimage_v2",
            "duration_s": result.seconds, "vram_peak_mb": result.vram_peak_mb,
            "downgraded_from": result.downgraded_from,
        })
        if result.downgraded_from:
            job["meta"]["warning"] = (
                f"VRAM insuffisante en mode {result.downgraded_from} : "
                f"rendu en {plan.mode}.")
        return self._finalise(job, result.images[-1], result)

    def _run_edit(self, job: dict[str, Any], renderer: ImageRenderer, *,
                  request: str, mode: str, source_path: str, seed: int) -> dict[str, Any]:
        operation = "upscale" if mode == "upscale" else detect_edit_operation(request)
        uploaded = renderer.client.upload_image(source_path)
        recipe = EDIT_RECIPES[operation]
        mask = ""
        if recipe.needs_mask:
            mask_path = str(job.get("meta", {}).get("mask_path") or "")
            if not mask_path or not Path(mask_path).exists():
                raise ImageGenerationFailed(
                    f"L'opération « {recipe.label} » a besoin que tu sélectionnes "
                    f"la zone à modifier sur l'image.")
            mask = renderer.client.upload_image(mask_path)

        plan = plan_edit(operation, source_image=uploaded, prompt=request,
                         mask_image=mask, seed=seed)
        job["meta"].update({
            "pipeline_version": PIPELINE_VERSION, "build_id": IMAGE_QUALITY_BUILD_ID,
            "image_type": "IMAGE_EDIT", "edit_operation": operation,
            "edit_plan": plan.as_dict(), "stages": ["PREPARING", "GENERATING", "COMPLETE"],
        })
        self.m._save(job)
        result = renderer.edit(plan, filename_prefix=f"jarvis_edit_{job['id']}")
        job["meta"].update({"model": result.metadata, "workflow": "zimage_v2_edit",
                            "duration_s": result.seconds,
                            "vram_peak_mb": result.vram_peak_mb})
        return self._finalise(job, result.images[-1], result)

    def _finalise(self, job: dict[str, Any], data: bytes, result: Any) -> dict[str, Any]:
        from .imagegen import IMAGE_DIR
        output_dir = IMAGE_DIR / job["id"]
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "image.png"
        path.write_bytes(data)
        job["file_path"] = str(path)
        job["meta"]["bytes"] = len(data)
        self.m._set(job, stage="finalizing", progress=0.99)
        (output_dir / "image.json").write_text(
            json.dumps({**job, "url": ""}, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        self.m._set(job, status="completed", stage="completed", progress=1.0, emit=False)
        (output_dir / "image.json").write_text(
            json.dumps({**job, "url": ""}, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        try:
            self.m._save_history(job)
        except Exception:
            pass
        self.m._emit("image.generation.completed", job, bytes=len(data))
        return job
