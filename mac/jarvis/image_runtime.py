"""Execute a render plan against ComfyUI, with progress, timeouts and OOM fallback.

This is the only place that talks to the ComfyUI HTTP API for V2.  It is kept
free of JARVIS core imports so the benchmark harness can drive it directly.

Progress is reported as the stage vocabulary the UI expects:
``PREPARING → PROMPTING → GENERATING → REFINING → UPSCALING → COMPLETE``.
Stages are derived from which node ComfyUI reports executing, so the UI shows
what is genuinely happening rather than a synthetic timer.
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from .image_edit_graph import EditGraphBuilder, EditPlan
from .image_quality import FAST, QUALITY_MODES, RenderPlan
from .zimage_graph import (
    N_CLIP as N_CLIP_LOADER,
    N_DECODE, N_POST_APPLY, N_POST_FIT, N_POST_MODEL, N_RE_DECODE, N_RE_SAMPLER,
    N_SAMPLER, N_SHARPEN,
    N_UNET as N_UNET_LOADER,
    N_UP_APPLY, N_UP_FIT,
    N_VAE as N_VAE_LOADER,
    GraphBuildError, ZImageGraphBuilder,
)

DEFAULT_BASE_URL = "http://127.0.0.1:8188"

# Which stage each node belongs to.  ComfyUI reports the node it is executing
# over the WebSocket, so the stage shown is the stage actually running -- never
# a timer dressed up as progress.  Nodes absent from a given graph simply never
# fire, which is why a FAST render never shows HI-RES.
_NODE_STAGE = {
    N_UNET_LOADER: "LOADING_MODEL",
    N_CLIP_LOADER: "LOADING_MODEL",
    N_VAE_LOADER: "LOADING_MODEL",
    N_SAMPLER: "GENERATING",
    N_DECODE: "GENERATING",
    N_UP_APPLY: "HI_RES",
    N_UP_FIT: "HI_RES",
    N_RE_SAMPLER: "HI_RES",
    N_RE_DECODE: "HI_RES",
    N_POST_MODEL: "UPSCALE",
    N_POST_APPLY: "UPSCALE",
    N_POST_FIT: "UPSCALE",
    N_SHARPEN: "UPSCALE",
    # Image Edit V3.
    "61": "SEGMENTING",
    "62": "SEGMENTING",
    "63": "MASKING",
    "64": "MASKING",
    "66": "MASKING",
    "24": "MASKING",
    "26": "COMPOSITING",
    "71": "COMPOSITING",
}

# Rough share of a render each stage represents, used only to move the bar
# between two real node events.  The bar never advances past the stage that
# ComfyUI says is running.
_STAGE_FLOOR = {
    "PREPARING": 0.02, "PROMPTING": 0.05, "LOADING_MODEL": 0.10,
    "SEGMENTING": 0.15, "MASKING": 0.22, "GENERATING": 0.30,
    "HI_RES": 0.62, "UPSCALE": 0.82, "COMPOSITING": 0.90,
    "TYPOGRAPHY": 0.94, "SAVING": 0.97, "COMPLETE": 1.0,
}

# ComfyUI's dynamic VRAM loader does not always say "out of memory".  A
# 1440x2016 hi-res pass failed with "VRAM Allocation failed (non OOM)" /
# "Fault failed: 2" / "device not ready", which the fallback missed entirely,
# so the render errored instead of degrading one mode.  These are all memory
# pressure by another name.
_OOM_MARKERS = ("out of memory", "outofmemoryerror", "cuda error",
                "allocate", "not enough memory", "vram allocation failed",
                "fault failed", "device not ready", "cuda api failed")


class ImageBackendUnavailable(RuntimeError):
    pass


class ImageGenerationFailed(RuntimeError):
    pass


class ImageGenerationTimeout(ImageGenerationFailed):
    pass


@dataclass
class RenderResult:
    images: list[bytes] = field(default_factory=list)
    filenames: list[str] = field(default_factory=list)
    seconds: float = 0.0
    vram_peak_mb: int = 0
    prompt_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    downgraded_from: str = ""
    stages_seen: list[str] = field(default_factory=list)


def gpu_free_mb() -> int | None:
    """Free VRAM in MiB, or ``None`` when nvidia-smi cannot answer."""
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return int(proc.stdout.strip().splitlines()[0])
    except Exception:
        return None


def _gpu_used_mb() -> int:
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return int(proc.stdout.strip().splitlines()[0])
    except Exception:
        return 0


class ComfyClient:
    """Minimal ComfyUI API client with explicit failure modes."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, *, timeout: int = 30) -> None:
        self.base = str(base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, timeout: int | None = None) -> Any:
        with urllib.request.urlopen(self.base + path, timeout=timeout or self.timeout) as fh:
            return json.loads(fh.read())

    def health(self, *, attempts: int = 3) -> dict[str, Any]:
        """Ping ComfyUI, retrying briefly.

        A single slow reply is not proof the server is gone: right after an
        interrupt it can stall for several seconds while it tears the workflow
        down.  Failing on the first timeout made one timed-out render cascade
        into a run of false "backend unavailable" errors.
        """
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                return self._get("/system_stats", timeout=15)
            except Exception as exc:
                last = exc
                if attempt + 1 < attempts:
                    time.sleep(2.0 * (attempt + 1))
        raise ImageBackendUnavailable(
            f"ComfyUI injoignable sur {self.base} : {type(last).__name__}") from last

    def wait_until_idle(self, timeout: float = 60.0) -> bool:
        """Block until the queue drains, so the next render starts clean."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                queue = self._get("/queue", timeout=10)
                if not queue.get("queue_running") and not queue.get("queue_pending"):
                    return True
            except Exception:
                pass
            time.sleep(1.5)
        return False

    def submit(self, graph: dict[str, Any], client_id: str) -> str:
        body = json.dumps({"prompt": graph, "client_id": client_id}).encode()
        req = urllib.request.Request(self.base + "/prompt", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as fh:
                payload = json.loads(fh.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:600]
            raise ImageGenerationFailed(f"ComfyUI a refusé le workflow : {detail}") from exc
        except Exception as exc:
            raise ImageBackendUnavailable(f"Envoi du workflow impossible : {exc}") from exc
        prompt_id = str(payload.get("prompt_id") or "")
        if not prompt_id:
            raise ImageGenerationFailed("ComfyUI n'a pas renvoyé de prompt_id.")
        return prompt_id

    def history(self, prompt_id: str) -> dict[str, Any]:
        return self._get(f"/history/{prompt_id}")

    def interrupt(self) -> None:
        try:
            urllib.request.urlopen(
                urllib.request.Request(self.base + "/interrupt", data=b"{}",
                                       headers={"Content-Type": "application/json"}),
                timeout=8).read()
        except Exception:
            pass

    def free(self, *, unload_models: bool = True) -> None:
        """Ask ComfyUI to release VRAM.  Used by the OOM fallback path."""
        body = json.dumps({"unload_models": unload_models, "free_memory": True}).encode()
        try:
            urllib.request.urlopen(
                urllib.request.Request(self.base + "/free", data=body,
                                       headers={"Content-Type": "application/json"}),
                timeout=15).read()
        except Exception:
            pass

    def fetch_image(self, item: dict[str, Any]) -> bytes:
        query = urllib.parse.urlencode({
            "filename": item.get("filename", ""),
            "subfolder": item.get("subfolder", ""),
            "type": item.get("type", "output")})
        with urllib.request.urlopen(f"{self.base}/view?{query}", timeout=120) as fh:
            return fh.read()

    def upload_image(self, path: str, *, name: str = "") -> str:
        """Upload a local file into ComfyUI's input folder; returns its name."""
        import mimetypes
        import os
        filename = name or os.path.basename(path)
        with open(path, "rb") as fh:
            payload = fh.read()
        boundary = "----jarvis" + uuid.uuid4().hex
        mime = mimetypes.guess_type(filename)[0] or "image/png"
        body = b"".join([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode(),
            f"Content-Type: {mime}\r\n\r\n".encode(), payload, b"\r\n",
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n',
            f"--{boundary}--\r\n".encode()])
        req = urllib.request.Request(
            self.base + "/upload/image", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=120) as fh:
            result = json.loads(fh.read())
        return str(result.get("name") or filename)


ProgressCallback = Callable[[str, float, dict[str, Any]], None]


class ImageRenderer:
    """Run render and edit plans, reporting stages and surviving OOM."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, *,
                 on_progress: ProgressCallback | None = None) -> None:
        self.client = ComfyClient(base_url)
        self.on_progress = on_progress
        self.builder = ZImageGraphBuilder()
        self.edit_builder = EditGraphBuilder()

    def _emit(self, stage: str, progress: float, **extra: Any) -> None:
        if self.on_progress:
            try:
                self.on_progress(stage, max(0.0, min(1.0, progress)), extra)
            except Exception:
                pass  # a UI listener must never break a render

    # -- execution ----------------------------------------------------------
    def _execute(self, graph: dict[str, Any], *, budget_s: int,
                 expected_stages: list[str]) -> RenderResult:
        self._emit("PROMPTING", _STAGE_FLOOR["PROMPTING"], nodes=len(graph))
        client_id = uuid.uuid4().hex
        started = time.time()

        # Listen before submitting, so no early node event is missed.
        ws = self._open_ws(client_id)
        prompt_id = self.client.submit(graph, client_id)

        peak = _gpu_used_mb()
        seen: list[str] = []
        stage = "LOADING_MODEL"
        self._emit(stage, _STAGE_FLOOR[stage], prompt_id=prompt_id)

        try:
            while True:
                elapsed = time.time() - started
                if elapsed > budget_s:
                    self.client.interrupt()
                    self.client.wait_until_idle(timeout=45)
                    raise ImageGenerationTimeout(
                        f"Génération interrompue après {int(elapsed)} s "
                        f"(budget {budget_s} s). Le format demandé est plus lourd "
                        f"que ce que ce mode prévoit.")

                event = self._next_event(ws)
                if event is not None:
                    node_id, node_progress = event
                    mapped = _NODE_STAGE.get(str(node_id))
                    if mapped and mapped != stage:
                        stage = mapped
                        if stage not in seen:
                            seen.append(stage)
                    floor = _STAGE_FLOOR.get(stage, 0.3)
                    ceiling = min(0.96, floor + 0.28)
                    self._emit(stage, floor + (ceiling - floor) * node_progress,
                               elapsed_s=round(elapsed, 1))
                    peak = max(peak, _gpu_used_mb())

                history = self.client.history(prompt_id)
                entry = history.get(prompt_id)
                if entry:
                    break
                if event is None:
                    peak = max(peak, _gpu_used_mb())
                    time.sleep(0.4)
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass

        seconds = time.time() - started
        status = entry.get("status") or {}
        if status.get("status_str") != "success":
            raise ImageGenerationFailed(self._explain(status))

        self._emit("SAVING", _STAGE_FLOOR["SAVING"])
        images: list[bytes] = []
        names: list[str] = []
        for node_output in entry.get("outputs", {}).values():
            for item in node_output.get("images", []):
                if item.get("type") != "output":
                    continue
                images.append(self.client.fetch_image(item))
                names.append(str(item.get("filename") or ""))
        if not images:
            raise ImageGenerationFailed("ComfyUI n'a produit aucune image.")

        self._emit("COMPLETE", 1.0, seconds=round(seconds, 1))
        return RenderResult(images=images, filenames=names, seconds=round(seconds, 2),
                            vram_peak_mb=peak, prompt_id=prompt_id, stages_seen=seen)

    def _open_ws(self, client_id: str):
        """Subscribe to ComfyUI's progress channel; ``None`` if unavailable.

        Without it the render still completes -- the stage display just falls
        back to whatever the last real event said, rather than inventing one.
        """
        try:
            from .ws_client import WebSocketClient
            scheme = "wss" if self.client.base.startswith("https") else "ws"
            host = self.client.base.split("://", 1)[-1]
            ws = WebSocketClient(f"{scheme}://{host}/ws?clientId={client_id}", timeout=15)
            ws.connect()
            ws.settimeout(1.0)
            return ws
        except Exception:
            return None

    @staticmethod
    def _next_event(ws) -> tuple[str, float] | None:
        """Return ``(node_id, 0..1)`` for the node ComfyUI is running, if any."""
        if ws is None:
            return None
        try:
            kind, payload = ws.recv()
        except Exception:
            return None
        if kind != "text":
            return None
        try:
            message = json.loads(payload.decode("utf-8", "replace"))
        except Exception:
            return None
        data = message.get("data") or {}
        if message.get("type") == "executing" and data.get("node"):
            return str(data["node"]), 0.0
        if message.get("type") == "progress":
            node = data.get("node")
            total = float(data.get("max") or 0)
            value = float(data.get("value") or 0)
            if node and total > 0:
                return str(node), max(0.0, min(1.0, value / total))
        return None

    @staticmethod
    def _explain(status: dict[str, Any]) -> str:
        """Turn a ComfyUI failure into something a user can act on."""
        blob = json.dumps(status, ensure_ascii=False).casefold()
        for message in status.get("messages") or []:
            if isinstance(message, list) and len(message) > 1 and isinstance(message[1], dict):
                detail = message[1]
                if detail.get("exception_message"):
                    text = str(detail["exception_message"])
                    if any(marker in text.casefold() for marker in _OOM_MARKERS):
                        return f"VRAM insuffisante : {text[:200]}"
                    return f"Échec ComfyUI ({detail.get('node_type', '?')}) : {text[:300]}"
        if any(marker in blob for marker in _OOM_MARKERS):
            return "VRAM insuffisante pour cette résolution."
        return f"Échec ComfyUI : {json.dumps(status, ensure_ascii=False)[:300]}"

    @staticmethod
    def _is_oom(exc: Exception) -> bool:
        return any(marker in str(exc).casefold() for marker in _OOM_MARKERS) or \
            "vram insuffisante" in str(exc).casefold()

    # -- public API ---------------------------------------------------------
    def render(self, plan: RenderPlan, *, source_image: str = "",
               filename_prefix: str = "jarvis_v2", allow_downgrade: bool = True) -> RenderResult:
        """Render ``plan``, degrading one quality step on an out-of-memory error.

        The degradation is reported in :attr:`RenderResult.downgraded_from` so
        the UI can say the image is not the mode that was asked for, rather
        than quietly delivering something smaller.
        """
        self.client.health()
        self._emit("PREPARING", 0.02, mode=plan.mode, image_type=plan.image_type)
        graph = self.builder.build(plan, source_image=source_image,
                                   filename_prefix=filename_prefix)
        try:
            result = self._execute(graph, budget_s=plan.budget_s,
                                   expected_stages=plan.stages)
        except ImageGenerationFailed as exc:
            if not (allow_downgrade and self._is_oom(exc)):
                raise
            fallback = self._downgrade(plan)
            if fallback is None:
                raise
            self.client.free()
            self._emit("PREPARING", 0.02, downgraded_to=fallback.mode)
            graph = self.builder.build(fallback, source_image=source_image,
                                       filename_prefix=filename_prefix)
            result = self._execute(graph, budget_s=fallback.budget_s,
                                   expected_stages=fallback.stages)
            result.downgraded_from = plan.mode
            plan = fallback
        result.metadata = self.builder.metadata(plan)
        result.metadata.update({
            "width": plan.final_width, "height": plan.final_height,
            "steps": plan.steps, "seed": graph[N_SAMPLER]["inputs"]["seed"],
            "prompt": plan.prompt, "negative_active": plan.negative_active,
            "mode_reason": plan.mode_reason, "notes": plan.notes})
        return result

    @staticmethod
    def _downgrade(plan: RenderPlan) -> RenderPlan | None:
        """One step down the quality ladder, rebuilt rather than patched."""
        from .image_quality import QUALITY_MODE_SPECS, RenderPlanner
        index = QUALITY_MODES.index(plan.mode)
        if index == 0:
            return None
        lower = QUALITY_MODES[index - 1]
        spec = QUALITY_MODE_SPECS[lower]
        downgraded = RenderPlanner().plan(
            plan.subject or plan.prompt, requested_mode=lower,
            image_type=plan.image_type, seed=plan.seed)
        downgraded.prompt = plan.prompt
        downgraded.negative = plan.negative
        downgraded.notes = [*plan.notes, f"downgraded_from_{plan.mode}_after_oom"]
        downgraded.budget_s = spec.budget_s
        return downgraded

    def edit(self, plan: EditPlan, *, filename_prefix: str = "jarvis_edit_v2",
             budget_s: int = 300) -> RenderResult:
        self.client.health()
        self._emit("PREPARING", 0.02, operation=plan.operation)
        graph = self.edit_builder.build_edit(plan, filename_prefix=filename_prefix)
        result = self._execute(graph, budget_s=budget_s, expected_stages=["GENERATING"])
        result.metadata = {
            "engine": "zimage_v2_edit", "operation": plan.operation,
            "denoise": plan.denoise, "steps": plan.steps, "seed": plan.seed,
            "confidence": plan.confidence, "notes": plan.notes,
            "upscale_model": self.edit_builder.upscale_model,
        }
        return result
