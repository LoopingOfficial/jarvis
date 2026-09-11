"""System Monitor — métriques RÉELLES de la machine (jamais de valeurs inventées).

Une métrique indisponible vaut None ; l'interface affiche alors « Unavailable »
plutôt qu'un chiffre fabriqué.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import subprocess
import threading
import time
from typing import Any

try:  # pragma: no cover
    import psutil

    HAVE_PSUTIL = True
except Exception:  # pragma: no cover
    psutil = None  # type: ignore
    HAVE_PSUTIL = False


class SystemMonitor:
    def __init__(self, events=None, started_at: float | None = None) -> None:
        self._events = events
        self._started_at = started_at or time.time()
        self._lock = threading.RLock()
        self._cache: dict[str, Any] = {}
        self._cache_at = 0.0
        self._last_cpu_times: tuple[float, float] | None = None
        self._net_state = ("unknown", 0.0)
        self._net_probing = False

    def _probe_network(self) -> None:
        status = "offline"
        for host, label in (("1.1.1.1", "excellent"), ("8.8.8.8", "correct")):
            try:
                with socket.create_connection((host, 53), timeout=2.0):
                    status = label
                    break
            except Exception:
                continue
        with self._lock:
            self._net_state = (status, time.time())
            self._net_probing = False

    # -- CPU ----------------------------------------------------------------
    def _cpu(self) -> dict[str, Any]:
        cores = os.cpu_count() or 0
        try:
            load1 = round(os.getloadavg()[0], 2)
        except (OSError, AttributeError):
            load1 = None
        percent: float | None = None
        if HAVE_PSUTIL:
            try:
                percent = round(psutil.cpu_percent(interval=0.15), 1)
            except Exception:
                percent = None
        if percent is None and platform.system() == "Darwin":
            percent = self._cpu_darwin()
        if percent is None and platform.system() == "Linux":
            percent = self._cpu_linux()
        if percent is None and load1 is not None and cores:
            percent = round(min(100.0, load1 / cores * 100), 1)
        return {"percent": percent, "cores": cores, "load1": load1}

    @staticmethod
    def _cpu_darwin() -> float | None:
        try:
            out = subprocess.check_output(["top", "-l", "1", "-n", "0"], text=True, timeout=6)
            m = re.search(r"CPU usage:\s*([\d.]+)%\s*user,\s*([\d.]+)%\s*sys", out)
            if m:
                return round(float(m.group(1)) + float(m.group(2)), 1)
        except Exception:
            pass
        return None

    def _cpu_linux(self) -> float | None:
        try:
            with open("/proc/stat", "r", encoding="utf-8") as fh:
                fields = [float(x) for x in fh.readline().split()[1:]]
            idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
            total = sum(fields)
            with self._lock:
                prev = self._last_cpu_times
                self._last_cpu_times = (idle, total)
            if not prev:
                return None
            didle, dtotal = idle - prev[0], total - prev[1]
            if dtotal <= 0:
                return None
            return round(max(0.0, min(100.0, (1 - didle / dtotal) * 100)), 1)
        except Exception:
            return None

    # -- Mémoire ------------------------------------------------------------
    def _memory(self) -> dict[str, Any]:
        if HAVE_PSUTIL:
            try:
                vm = psutil.virtual_memory()
                return {"total_gb": round(vm.total / 1024**3, 1), "used_gb": round(vm.used / 1024**3, 1),
                        "percent": round(vm.percent, 1)}
            except Exception:
                pass
        if platform.system() == "Darwin":
            try:
                total = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True, timeout=5).strip())
                vm_stat = subprocess.check_output(["vm_stat"], text=True, timeout=5)
                page = int((re.search(r"page size of (\d+) bytes", vm_stat) or [0, 4096])[1]) \
                    if re.search(r"page size of (\d+) bytes", vm_stat) else 4096
                vals: dict[str, int] = {}
                for line in vm_stat.splitlines():
                    m = re.match(r"([^:]+):\s+(\d+)\.", line)
                    if m:
                        vals[m.group(1).strip()] = int(m.group(2))
                free = (vals.get("Pages free", 0) + vals.get("Pages inactive", 0)
                        + vals.get("Pages speculative", 0)) * page
                used = max(0, total - free)
                return {"total_gb": round(total / 1024**3, 1), "used_gb": round(used / 1024**3, 1),
                        "percent": round(used / total * 100, 1) if total else None}
            except Exception:
                pass
        if platform.system() == "Linux":
            try:
                info: dict[str, int] = {}
                with open("/proc/meminfo", "r", encoding="utf-8") as fh:
                    for line in fh:
                        k, _, v = line.partition(":")
                        info[k.strip()] = int(v.strip().split()[0]) * 1024
                total = info.get("MemTotal", 0)
                available = info.get("MemAvailable", info.get("MemFree", 0))
                used = max(0, total - available)
                return {"total_gb": round(total / 1024**3, 1), "used_gb": round(used / 1024**3, 1),
                        "percent": round(used / total * 100, 1) if total else None}
            except Exception:
                pass
        return {"total_gb": None, "used_gb": None, "percent": None}

    # -- Disque -------------------------------------------------------------
    @staticmethod
    def _disk() -> dict[str, Any]:
        try:
            usage = shutil.disk_usage(os.path.expanduser("~"))
            return {"total_gb": round(usage.total / 1024**3, 1), "used_gb": round(usage.used / 1024**3, 1),
                    "percent": round(usage.used / usage.total * 100, 1) if usage.total else None}
        except Exception:
            return {"total_gb": None, "used_gb": None, "percent": None}

    # -- Réseau -------------------------------------------------------------
    def _network(self) -> dict[str, Any]:
        """Jamais bloquant : la sonde réseau tourne en arrière-plan.

        Hors ligne, un `connect()` peut coûter plusieurs secondes ; le faire
        dans le fil de la requête figerait tout le tableau de bord.
        """
        now = time.time()
        status, checked = self._net_state
        if now - checked > 20 and not self._net_probing:
            self._net_probing = True
            threading.Thread(target=self._probe_network, daemon=True, name="jarvis-net-probe").start()
        out: dict[str, Any] = {"status": status, "hostname": socket.gethostname()}
        if HAVE_PSUTIL:
            try:
                io = psutil.net_io_counters()
                out["sent_mb"] = round(io.bytes_sent / 1024**2, 1)
                out["recv_mb"] = round(io.bytes_recv / 1024**2, 1)
            except Exception:
                pass
        return out

    # -- snapshot -----------------------------------------------------------
    def snapshot(self, max_age: float = 2.0) -> dict[str, Any]:
        with self._lock:
            if self._cache and (time.time() - self._cache_at) < max_age:
                return self._cache
        data = {
            "cpu": self._cpu(), "memory": self._memory(), "disk": self._disk(), "network": self._network(),
            "uptime_s": int(time.time() - self._started_at),
            "boot_uptime_s": self._boot_uptime(),
            "platform": platform.platform(), "python": platform.python_version(),
            "psutil": HAVE_PSUTIL, "ts": time.time(),
        }
        with self._lock:
            self._cache = data
            self._cache_at = time.time()
        return data

    @staticmethod
    def _boot_uptime() -> int | None:
        if HAVE_PSUTIL:
            try:
                return int(time.time() - psutil.boot_time())
            except Exception:
                return None
        if platform.system() == "Darwin":
            try:
                out = subprocess.check_output(["sysctl", "-n", "kern.boottime"], text=True, timeout=5)
                m = re.search(r"sec\s*=\s*(\d+)", out)
                if m:
                    return int(time.time() - int(m.group(1)))
            except Exception:
                return None
        try:
            with open("/proc/uptime", "r", encoding="utf-8") as fh:
                return int(float(fh.readline().split()[0]))
        except Exception:
            return None

    def start_broadcast(self, interval: float = 5.0, stop_event: threading.Event | None = None) -> threading.Thread:
        """Diffuse system.metrics + alertes sur seuil."""
        def loop() -> None:
            warned: dict[str, float] = {}
            while not (stop_event and stop_event.is_set()):
                try:
                    data = self.snapshot(max_age=0)
                    if self._events:
                        self._events.emit("system.metrics", data)
                        now = time.time()
                        for key, value, label, limit in (
                            ("cpu", data["cpu"]["percent"], "CPU", 92),
                            ("memory", data["memory"]["percent"], "Mémoire", 92),
                            ("disk", data["disk"]["percent"], "Disque", 92),
                        ):
                            if value is not None and value >= limit and now - warned.get(key, 0) > 900:
                                warned[key] = now
                                self._events.emit("system.warning", {"metric": key, "value": value})
                                self._events.feed(f"{label} à {value}%", level="warn", kind="system",
                                                  detail=f"Seuil d'alerte {limit}% dépassé.", source="monitor")
                except Exception:
                    pass
                time.sleep(interval)

        thread = threading.Thread(target=loop, daemon=True, name="jarvis-monitor")
        thread.start()
        return thread
