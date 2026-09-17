"""Arbitrage de la VRAM entre Ollama et Blender.

Sur une carte 8 Go (RTX 3070), un modèle Ollama chargé et un rendu Blender GPU
se disputent la même mémoire. Ce module :

  * lit la VRAM réellement disponible quel que soit le vendeur : `nvidia-smi`
    (NVIDIA), `rocm-smi` (AMD), puis les compteurs de performance Windows
    (`GPU Adapter Memory`, valables AMD comme Intel) — sans dépendance Python ;
  * dit quels modèles Ollama sont actuellement CHARGÉS (`/api/ps`) ;
  * permet de décharger un modèle à la demande (`keep_alive: 0`), une fois que
    le spécialiste a produit son plan et avant un rendu lourd.

Rien n'est deviné : si aucune de ces sources ne répond, `vram()` renvoie None
et l'appelant ne prend aucune décision fondée sur une valeur inventée.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
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
    def _run(args: list[str], timeout: int = 10) -> str:
        """Exécute une sonde de mesure. Chaîne vide si absente ou en échec."""
        if not shutil.which(args[0]):
            return ""
        try:
            proc = subprocess.run(
                args, capture_output=True, text=True, timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            return ""
        return proc.stdout if proc.returncode == 0 else ""

    @classmethod
    def _vram_nvidia(cls) -> dict[str, Any] | None:
        out = cls._run(["nvidia-smi",
                        "--query-gpu=memory.total,memory.used,memory.free",
                        "--format=csv,noheader,nounits"])
        if not out.strip():
            return None
        first = out.strip().splitlines()[0]
        try:
            total, used, free = (int(p.strip()) for p in first.split(",")[:3])
        except ValueError:
            return None
        return {"total_mb": total, "used_mb": used, "free_mb": free,
                "vendor": "nvidia", "source": "nvidia-smi"}

    @classmethod
    def _vram_amd(cls) -> dict[str, Any] | None:
        """AMD via rocm-smi (Linux, et Windows quand ROCm est installé)."""
        out = cls._run(["rocm-smi", "--showmeminfo", "vram", "--json"])
        if not out.strip():
            return None
        try:
            payload = json.loads(out)
        except Exception:
            return None
        for card in payload.values():
            if not isinstance(card, dict):
                continue
            total = used = None
            for key, value in card.items():
                low = key.lower()
                if "vram" not in low:
                    continue
                try:
                    number = int(str(value).strip())
                except (TypeError, ValueError):
                    continue
                # L'ordre compte : la clé « VRAM Total Used Memory (B) »
                # contient aussi « total ». « used » doit donc primer.
                if "used" in low:
                    used = number
                elif "total" in low:
                    total = number
            if total is None or used is None or total <= 0:
                continue
            # rocm-smi rapporte des octets.
            total_mb, used_mb = total // (1024 * 1024), used // (1024 * 1024)
            return {"total_mb": total_mb, "used_mb": used_mb,
                    "free_mb": max(0, total_mb - used_mb),
                    "vendor": "amd", "source": "rocm-smi"}
        return None

    # AdapterRAM (WMI) plafonne à 4 Go : le total exact vient de qwMemorySize
    # côté registre, l'occupation des compteurs "GPU Adapter Memory".
    _PS_VRAM = "$best=$null;Get-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}\\*' -ErrorAction SilentlyContinue | ForEach-Object { $v=$_.'HardwareInformation.qwMemorySize'; if($v -ne $null -and (-not $best -or [int64]$v -gt [int64]$best.mem)) { $best=[pscustomobject]@{mem=[int64]$v;name=$_.DriverDesc} } };if(-not $best){ $c=Get-CimInstance Win32_VideoController | Where-Object {$_.AdapterRAM -gt 0} | Sort-Object AdapterRAM -Descending | Select-Object -First 1; if(-not $c){exit 1}; $best=[pscustomobject]@{mem=[int64]$c.AdapterRAM;name=$c.Name} };$u=(Get-Counter ('\\GPU Adapter Memory(*)\\Dedicated Usage') -ErrorAction SilentlyContinue).CounterSamples | Measure-Object -Property CookedValue -Maximum;[pscustomobject]@{name=$best.name;total=$best.mem;used=[int64]$u.Maximum} | ConvertTo-Json -Compress"

    @classmethod
    def _vram_windows(cls) -> dict[str, Any] | None:
        """Repli Windows tous vendeurs (AMD, Intel) : WMI + compteurs GPU."""
        if sys.platform != "win32":
            return None
        out = cls._run(["powershell", "-NoProfile", "-NonInteractive",
                        "-Command", cls._PS_VRAM], timeout=30)
        if not out.strip():
            return None
        try:
            payload = json.loads(out)
            total_mb = int(payload["total"]) // (1024 * 1024)
            used_mb = int(payload.get("used") or 0) // (1024 * 1024)
        except Exception:
            return None
        if total_mb <= 0:
            return None
        name = str(payload.get("name") or "")
        low = name.lower()
        vendor = ("amd" if any(k in low for k in ("amd", "radeon"))
                  else "intel" if ("intel" in low or "arc" in low)
                  else "nvidia" if ("nvidia" in low or "geforce" in low)
                  else "unknown")
        used_mb = min(used_mb, total_mb)
        return {"total_mb": total_mb, "used_mb": used_mb,
                "free_mb": total_mb - used_mb, "vendor": vendor,
                "source": "windows-perf-counters", "adapter": name}

    @classmethod
    def vram(cls) -> dict[str, Any] | None:
        """{'total_mb', 'used_mb', 'free_mb', 'vendor', 'source'} — None si illisible.

        Sondes essayées dans l'ordre : nvidia-smi, rocm-smi, compteurs Windows.
        """
        for probe in (cls._vram_nvidia, cls._vram_amd, cls._vram_windows):
            try:
                measure = probe()
            except Exception:
                measure = None
            if measure:
                return measure
        return None

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
                    "reason": "Aucune sonde VRAM disponible (nvidia-smi, rocm-smi, "
                              "compteurs Windows) : aucune décision VRAM."}
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
        return {"vram": vram, "vendor": (vram or {}).get("vendor", ""),
                "source": (vram or {}).get("source", ""),
                "loaded_models": self.loaded_models(),
                "min_free_mb_for_render": MIN_FREE_MB_FOR_RENDER,
                "render_ready": bool(vram and vram["free_mb"] >= MIN_FREE_MB_FOR_RENDER)}
