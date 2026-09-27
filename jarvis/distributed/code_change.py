"""Validated structured code changes and workspace-only patch execution."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class CodeChange:
    operation: str
    path: str
    reason: str
    after: str
    before: Optional[str] = None


class CodeChangeSet:
    def __init__(self, changes: List[CodeChange]):
        self.changes = changes

    @classmethod
    def from_json(cls, text: str) -> "CodeChangeSet":
        try:
            payload = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise ValueError("code_change_response_must_be_json") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("changes"), list):
            raise ValueError("code_change_schema_requires_changes_array")
        changes = []
        for item in payload["changes"]:
            if not isinstance(item, dict):
                raise ValueError("code_change_item_must_be_object")
            operation = item.get("operation")
            if operation not in {"replace", "create"}:
                raise ValueError("unsupported_code_change_operation")
            if not isinstance(item.get("path"), str) or not item["path"]:
                raise ValueError("code_change_path_required")
            if not isinstance(item.get("reason"), str):
                raise ValueError("code_change_reason_required")
            if not isinstance(item.get("after"), str):
                raise ValueError("code_change_after_required")
            changes.append(CodeChange(operation, item["path"], item["reason"], item["after"], item.get("before")))
        if not changes:
            raise ValueError("code_change_must_contain_changes")
        return cls(changes)

    def to_dict(self) -> Dict[str, Any]:
        return {"changes": [asdict(change) for change in self.changes]}


class PatchExecutor:
    def __init__(self, workspace: str):
        self.root = Path(workspace).resolve()
        self._snapshot: Dict[Path, Optional[bytes]] = {}

    def _path(self, relative: str) -> Path:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("code_change_path_outside_workspace")
        candidate = (self.root / path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("code_change_path_outside_workspace")
        return candidate

    def validate(self, change_set: CodeChangeSet) -> None:
        paths = set()
        for change in change_set.changes:
            path = self._path(change.path)
            if change.path in paths:
                raise ValueError("duplicate_code_change_path:" + change.path)
            paths.add(change.path)
            if change.operation == "replace":
                if not path.is_file():
                    raise ValueError("replace_target_missing:" + change.path)
                if not isinstance(change.before, str):
                    raise ValueError("replace_requires_before_content:" + change.path)
                current = path.read_text(encoding="utf-8")
                if current.count(change.before) != 1:
                    raise ValueError("replace_before_content_mismatch:" + change.path)

    def apply(self, change_set: CodeChangeSet) -> List[str]:
        self.validate(change_set)
        self._snapshot = {}
        try:
            for change in change_set.changes:
                path = self._path(change.path)
                self._snapshot[path] = path.read_bytes() if path.exists() else None
                path.parent.mkdir(parents=True, exist_ok=True)
                current = path.read_text(encoding="utf-8") if path.exists() else ""
                if change.operation == "replace":
                    current = current.replace(change.before or "", change.after, 1)
                else:
                    current = change.after
                path.write_text(current, encoding="utf-8")
        except Exception:
            self.rollback()
            raise
        return [change.path for change in change_set.changes]

    def rollback(self) -> None:
        for path, content in self._snapshot.items():
            if content is None:
                if path.exists():
                    path.unlink()
            else:
                path.write_bytes(content)
