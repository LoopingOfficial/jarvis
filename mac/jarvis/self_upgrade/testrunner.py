"""Exécution des tests unittest dans un workspace."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any


class TestRunner:
    def __init__(self, workspace: Path, python: str = "python") -> None:
        self._ws = Path(workspace)
        self._python = python

    def _cmd(self, target: str) -> list[str]:
        if target and not target.startswith("discover"):
            return [self._python, "-m", "unittest", target]
        return [self._python, "-m", "unittest", "discover", "-s", "tests"]

    def run(self, target: str = "", timeout: float = 300.0) -> dict[str, Any]:
        try:
            res = subprocess.run(
                self._cmd(target), cwd=str(self._ws), capture_output=True, text=True,
                timeout=timeout, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return {"ok": False, "summary": f"timeout après {timeout}s", "tests_run": 0,
                    "tests_failed": 999, "output": "timeout", "duration_s": timeout}
        except Exception as exc:
            return {"ok": False, "summary": str(exc), "tests_run": 0,
                    "tests_failed": 999, "output": str(exc), "duration_s": 0}
        output = res.stdout + res.stderr
        return self._parse(output, res.returncode == 0)

    def _parse(self, output: str, ok: bool) -> dict[str, Any]:
        tail = output[-12000:]
        summary = ""
        m = re.search(r"Ran (\d+) tests* in ([\d.]+)s", output)
        tests_run = int(m.group(1)) if m else 0
        duration = float(m.group(2)) if m else 0.0
        m = re.search(r"(OK|FAILED)", output.splitlines()[-1] if output.splitlines() else "")
        failed = 0
        for line in output.splitlines():
            if re.match(r"^(FAIL|ERROR):", line):
                failed += 1
        summary = "PASS" if (ok and failed == 0) else "FAIL"
        return {"ok": ok and failed == 0, "summary": summary, "tests_run": tests_run,
                "tests_failed": failed, "duration_s": duration, "output": tail}