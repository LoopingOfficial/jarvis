"""Arbitrage de la VRAM entre Ollama et Blender.

Sur une carte 8 Go (RTX 3070), un modèle Ollama chargé et un rendu Blender GPU
se disputent la même mémoire. Ce module :

  * lit la VRAM réellement disponible (`nvidia-smi`, sans dépendance Python) ;
  * dit quels modèles Ollama sont actuellement CHARGÉS (`/api/ps`) ;
  * permet de décharger un modèle à la demande (`keep_alive: 0`), une fois que
    le spécialiste a produit son plan et avant un rendu lourd.

Rien n'est deviné : sans `nvidia-smi`, `vram()` renvoie None et l'appelant ne
prend aucune décision fondée sur une valeur inventée.
"""
from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from typing import Any

# En dessous, un rendu GPU Blender risque de tomber en mémoire insuffisante.
MIN_FREE_MB_FOR_RENDER = 3000


def _log(message: str) -> None:
    line = f"[gpu] {message}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)
    except Exception:
        pass


class GpuResourceManager:
    """Décide si Blender peut rendre sur GPU, et libère la VRAM si besoin."""

    def __init__(self, core) -> None:
        self._core = core

    # ------------------------------------------------------------ mesures
    @staticmethod
    def vram() -> dict[str, int] | None:
        """{'total_mb', 'used_mb', 'free_mb'} — None si nvidia-smi absent."""
        try:
            proc = subprocess.run(
                ["nvidia-smi",
                 "--query-gpu=memory.total,memory.used,memory.free",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            return None
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        first = proc.stdout.strip().splitlines()[0]
        try:
            total, used, free = (int(p.strip()) for p in first.split(",")[:3])
        except ValueError:
            return None
        return {"total_mb": total, "used_mb": used, "free_mb": free}

    def _ollama_base(self) -> str:
        for status in self._core.llm.status():
            if status.get("type") == "ollama" and status.get("id"):
                provider = self._core.llm.provider_by_id(status["id"])
                if provider is not None:
                    return provider.base_url
        return "http://127.0.0.1:11434"

    def loaded_models(self) -> list[dict[str, Any]]:
        """Modèles Ollama actuellement résidents en mémoire."""
        try:
            with urllib.request.urlopen(self._ollama_base() + "/api/ps", timeout=6) as response:
                payload = json.loads(response.read())
        except Exception:
            return []
        out = []
        for entry in (payload.get("models") or []):
            out.append({"name": entry.get("name", ""),
                        "size_mb": int(entry.get("size_vram") or entry.get("size") or 0) // (1024 * 1024)})
        return out

    # ------------------------------------------------------------ actions
    def unload(self, model: str) -> bool:
        """Décharge un modèle Ollama (`keep_alive: 0`). True si la requête passe."""
        if not model:
            return False
        body = json.dumps({"model": model, "keep_alive": 0}).encode()
        request = urllib.request.Request(
            self._ollama_base() + "/api/generate", body,
            {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            _log(f"déchargement {model} refusé : HTTP {exc.code}")
            return False
        except Exception as exc:
            _log(f"déchargement {model} impossible : {exc}")
            return False
        _log(f"modèle déchargé : {model}")
        return True

    def free_vram_for_blender(self, *, keep: str = "") -> dict[str, Any]:
        """Libère de la VRAM avant un travail Blender lourd.

        Ne décharge que si la mémoire libre est réellement insuffisante, et
        jamais le modèle `keep` (celui encore nécessaire).
        """
        before = self.vram()
        if before is None:
            return {"measured": False, "unloaded": [],
                    "reason": "nvidia-smi indisponible : aucune décision VRAM."}
        if before["free_mb"] >= MIN_FREE_MB_FOR_RENDER:
            return {"measured": True, "unloaded": [], "before": before,
                    "reason": f"{before['free_mb']} Mo libres : suffisant."}

        unloaded = []
        for entry in self.loaded_models():
            name = entry["name"]
            if not name or name == keep:
                continue
            if self.unload(name):
                unloaded.append(name)
        after = self.vram() or before
        _log(f"VRAM {before['free_mb']} -> {after['free_mb']} Mo libres "
             f"(déchargés : {unloaded or 'aucun'})")
        return {"measured": True, "unloaded": unloaded, "before": before,
                "after": after,
                "reason": f"{after['free_mb']} Mo libres après déchargement."}

    def snapshot(self) -> dict[str, Any]:
        """État pour l'UI / les diagnostics."""
        vram = self.vram()
        return {"vram": vram, "loaded_models": self.loaded_models(),
                "min_free_mb_for_render": MIN_FREE_MB_FOR_RENDER,
                "render_ready": bool(vram and vram["free_mb"] >= MIN_FREE_MB_FOR_RENDER)}
