"""Matérialisation et validation des fichiers remis à l'utilisateur."""
from __future__ import annotations

import hashlib
import mimetypes
import shutil
import uuid
from pathlib import Path
from typing import Any

from .config import DATA_DIR


class ArtifactManager:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or DATA_DIR) / "artifacts"
        self.root.mkdir(parents=True, exist_ok=True)

    def materialize(self, source: str | Path, *, filename: str = "") -> dict[str, Any]:
        src = Path(source)
        if not src.is_file() or src.stat().st_size <= 0:
            raise ValueError("artifact absent ou vide")
        if src.suffix.lower() == ".pdf":
            raw = src.read_bytes()
            if not raw.startswith(b"%PDF-") or b"%%EOF" not in raw[-2048:]:
                raise ValueError("PDF invalide")
            mime = "application/pdf"
        else:
            raw = src.read_bytes()
            mime = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
        artifact_id = uuid.uuid4().hex
        safe_name = Path(filename or src.name).name or f"artifact{src.suffix}"
        dest = self.root / artifact_id / safe_name
        dest.parent.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(src, dest)
        size = dest.stat().st_size
        if size <= 0:
            raise ValueError("artifact copié mais vide")
        return {"id": artifact_id, "path": str(dest), "filename": safe_name,
                "mime_type": mime, "size": size,
                "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
                "url": f"/api/artifacts/{artifact_id}/download",
                "artifact_verified": True}


artifact_manager = ArtifactManager()
