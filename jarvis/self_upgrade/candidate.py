"""Démarrage/arrêt de la candidate et vérification de santé HTTP."""
from __future__ import annotations

import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any


def http_get(port: int, path: str = "/api/health", timeout: float = 8.0) -> dict[str, Any]:
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")[:2000]
            return {"ok": resp.status == 200, "status": resp.status, "body": body, "url": url}
    except Exception as exc:
        return {"ok": False, "status": 0, "body": "", "url": url, "error": str(exc)}


class CandidateRunner:
    def __init__(self, workspace: Path, python: str = "python", port: int = 8791,
                 health_endpoint: str = "/api/health") -> None:
        self._ws = Path(workspace)
        self._python = python
        self._port = port
        self._endpoint = health_endpoint
        self._proc: subprocess.Popen | None = None

    @property
    def port(self) -> int:
        return self._port

    def start(self, boot_wait_s: float = 18.0, attempts: int = 40) -> dict[str, Any]:
        data_dir = self._ws / "data_candidate"
        log_file = data_dir / "candidate.log"
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            log_handle = open(log_file, "a", encoding="utf-8", errors="replace")
        except Exception:
            log_handle = None
        env = {
            "JARVIS_PORT": str(self._port),
            "JARVIS_HOST": "127.0.0.1",
            "JARVIS_LAUNCH_UI": "0",
            "JARVIS_DATA_DIR": str(data_dir),
            "JARVIS_CLAP_ENABLED": "0",
        }
        try:
            # stdout/stderr vers un fichier : un PIPE jamais lu se bloque quand
            # le buffer se remplit et la candidate n'atteint jamais le serveur HTTP.
            self._proc = subprocess.Popen(
                [self._python, "jarvis.py"],
                cwd=str(self._ws), env=env,
                stdout=log_handle, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            if log_handle:
                log_handle.close()
            return {"ok": False, "error": f"lancement impossible: {exc}"}
        time.sleep(boot_wait_s)
        health = {"ok": False}
        for _ in range(attempts):
            health = http_get(self._port, self._endpoint)
            if health["ok"]:
                break
            time.sleep(2.0)
            if self._proc.poll() is not None:
                break
        if not health["ok"] and log_handle:
            try:
                tail = Path(log_file).read_text(encoding="utf-8", errors="replace")[-3000:]
                health["candidate_log_tail"] = tail
            except Exception:
                pass
        if log_handle:
            log_handle.close()
        return {
            "ok": health["ok"],
            "port": self._port,
            "pid": self._proc.pid,
            "health": health,
            "error": "" if health["ok"] else f"candidate injoignable: {health.get('error', '')}",
        }

    def check(self, endpoint: str = "", timeout: float = 8.0) -> dict[str, Any]:
        return http_get(self._port, endpoint or self._endpoint, timeout=timeout)

    def stop(self) -> None:
        if self._proc is None:
            return
        try:
            subprocess.run(["taskkill", "/PID", str(self._proc.pid), "/T", "/F"],
                           capture_output=True, timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            try:
                self._proc.terminate()
            except Exception:
                pass
        self._proc = None