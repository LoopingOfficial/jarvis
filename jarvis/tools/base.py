"""Contrat des outils : schéma, risque, dépendance connecteur, politique."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from ..permissions import READ_ONLY, RISK_LABELS


@dataclass
class ToolContext:
    """Tout ce qu'un outil peut utiliser. Les secrets ne sont accessibles que
    via `secret(field)`, qui passe par le vault et trace l'accès."""
    core: Any
    connector: dict[str, Any] | None = None
    task_id: str = ""
    agent: str = "jarvis"
    conversation_id: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    execution_policy: dict[str, Any] = field(default_factory=dict)

    def secret(self, field_name: str, default: str = "") -> str:
        if not self.connector:
            return default
        return self.core.vault.get(self.connector["id"], field_name, default)

    def secrets(self, *fields: str) -> dict[str, str]:
        return {f: self.secret(f) for f in fields}

    @property
    def config(self) -> dict[str, Any]:
        return (self.connector or {}).get("config", {}) or {}

    def log(self, message: str, level: str = "info", data: Any = None) -> None:
        if self.task_id:
            self.core.tasks.log(self.task_id, message, level=level, data=data)


@dataclass
class ToolResult:
    ok: bool
    output: str = ""
    data: Any = None
    risk: str = READ_ONLY
    needs_confirmation: bool = False
    confirmation_reason: str = ""
    artifacts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "output": self.output, "data": self.data,
            "risk": self.risk, "risk_label": RISK_LABELS.get(self.risk, self.risk),
            "artifacts": self.artifacts,
        }


@dataclass
class Tool:
    id: str
    name: str
    description: str
    category: str
    handler: Callable[[ToolContext], ToolResult]
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    connector_type: str = ""                  # type de connecteur requis ("" = aucun)
    connector_optional: bool = False
    risk: str = READ_ONLY                     # risque de base
    risk_resolver: Callable[[dict[str, Any]], str] | None = None
    confirmation_policy: str = "auto"         # auto | always | never
    permissions: tuple[str, ...] = ("read",)
    enabled: bool = True
    agents: tuple[str, ...] = ()              # agents autorisés ("" = tous)
    dangerous_hint: str = ""

    def resolve_risk(self, arguments: dict[str, Any]) -> str:
        if self.risk_resolver:
            try:
                return self.risk_resolver(arguments)
            except Exception:
                return self.risk
        return self.risk

    def public(self, core=None) -> dict[str, Any]:
        status = "ready"
        detail = ""
        if not self.enabled:
            status, detail = "disabled", "Désactivé dans les réglages"
        elif self.connector_type and core is not None:
            available = core.connectors.active(self.connector_type)
            if not available and not self.connector_optional:
                status = "not_configured"
                detail = f"Aucun connecteur « {self.connector_type} » configuré"
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "category": self.category, "connector_type": self.connector_type,
            "connector_optional": self.connector_optional,
            "risk": self.risk, "risk_label": RISK_LABELS.get(self.risk, self.risk),
            "confirmation_policy": self.confirmation_policy,
            "permissions": list(self.permissions), "enabled": self.enabled,
            "status": status, "status_detail": detail,
            "input_schema": self.input_schema, "output_schema": self.output_schema,
            "agents": list(self.agents),
        }

    def llm_schema(self) -> dict[str, Any]:
        """Description au format « function calling »."""
        desc = self.description
        if self.connector_type:
            desc += f" (nécessite un connecteur {self.connector_type} ; passer connector_id)"
        return {"name": self.id, "description": desc[:1000], "input_schema": self.input_schema or {
            "type": "object", "properties": {}, "required": []
        }}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._lock = threading.RLock()

    def register(self, tool: Tool) -> Tool:
        with self._lock:
            self._tools[tool.id] = tool
        return tool

    def add(self, **kwargs) -> Tool:
        return self.register(Tool(**kwargs))

    def get(self, tool_id: str) -> Tool | None:
        return self._tools.get(tool_id)

    def all(self) -> list[Tool]:
        return sorted(self._tools.values(), key=lambda t: (t.category, t.name))

    def for_agent(self, agent: str) -> list[Tool]:
        return [t for t in self.all() if t.enabled and (not t.agents or agent in t.agents)]

    def categories(self) -> list[str]:
        return sorted({t.category for t in self._tools.values()})

    def count(self) -> int:
        return len(self._tools)

    def set_enabled(self, tool_id: str, enabled: bool) -> bool:
        tool = self._tools.get(tool_id)
        if not tool:
            return False
        tool.enabled = enabled
        return True


registry = ToolRegistry()
