"""Host-side MPFB dependency contract.

This module does not vendor MPFB. The Blender-side adapter loads the installed
official extension and calls ``HumanService``. Missing MPFB is a hard failure.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MIN_BLENDER = (4, 2, 0)
MPFB_MODULE = "bl_ext.user_default.mpfb"


class MpfbUnavailable(RuntimeError):
    pass


def _version_tuple(value: str) -> tuple[int, int, int]:
    parts = [int(p) if p.isdigit() else 0 for p in re.findall(r"\d+", value)[:3]]
    return tuple((parts + [0, 0, 0])[:3])


@dataclass(frozen=True)
class MpfbAdapter:
    blender_path: str
    min_blender: tuple[int, int, int] = MIN_BLENDER

    def blender_version(self) -> str:
        if not self.blender_path or not Path(self.blender_path).is_file():
            raise MpfbUnavailable("Blender executable not found")
        proc = subprocess.run([self.blender_path, "--version"], capture_output=True,
                              text=True, timeout=90)
        text = (proc.stdout or "") + "\n" + (proc.stderr or "")
        match = re.search(r"Blender\s+(\d+(?:\.\d+){1,2})", text)
        version = match.group(1) if match else ""
        if _version_tuple(version) < self.min_blender:
            raise MpfbUnavailable(
                f"MPFB2 requires Blender >= {'.'.join(map(str, self.min_blender))}; "
                f"detected {version or 'unknown'}")
        return version

    def preflight(self) -> dict[str, Any]:
        version = self.blender_version()
        return {"ok": True, "blender_version": version, "mpfb_module": MPFB_MODULE,
                "requires_mpfb": True, "fallback": False}

    @staticmethod
    def ensure_fail_hard(result: dict[str, Any]) -> dict[str, Any]:
        if not result.get("ok"):
            raise MpfbUnavailable(result.get("error") or "MPFB runtime failed")
        return result
