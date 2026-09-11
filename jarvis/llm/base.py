"""Interface commune à tous les fournisseurs de modèles.

Chaque provider implémente `chat()` et, si possible, `models()` et `embed()`.
Le reste de JARVIS ne connaît que cette interface : changer de fournisseur
n'impacte aucun agent ni aucun outil.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatMessage:
    role: str                      # system | user | assistant | tool
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""
    name: str = ""
    # Images attachees au message, en base64 NU (sans prefixe `data:`).
    # Portees telles quelles jusqu'au provider, qui les encode a son format.
    images: list[str] = field(default_factory=list)


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    provider: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    raw: Any = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


class LLMProvider:
    """Classe de base. `connector` fournit la config ; `secret()` la clé API."""

    type = "base"
    supports_tools = False
    supports_embeddings = False
    supports_vision = False

    def __init__(self, connector: dict[str, Any], secret_getter) -> None:
        self.connector = connector
        self.config = connector.get("config", {}) or {}
        self._secret = secret_getter
        self.id = connector["id"]
        self.name = connector.get("name", self.type)

    # -- helpers -----------------------------------------------------------
    @property
    def base_url(self) -> str:
        return str(self.config.get("base_url") or "").rstrip("/")

    @property
    def api_key(self) -> str:
        return self._secret(self.id, "api_key", "")

    @property
    def default_model(self) -> str:
        return str(self.config.get("default_model") or "")

    # -- API ---------------------------------------------------------------
    def models(self) -> list[str]:
        return []

    def chat(
        self, messages: list[ChatMessage], *, model: str = "", tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.3, max_tokens: int = 4096, timeout: float = 180.0,
    ) -> LLMResponse:
        raise NotImplementedError

    def embed(self, text: str, model: str = "") -> list[float]:
        return []

    def health(self) -> tuple[bool, str]:
        try:
            models = self.models()
            if models:
                return True, f"{len(models)} modèle(s)"
            return False, "Aucun modèle disponible"
        except Exception as exc:
            return False, str(exc)[:200]


def messages_to_openai(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
            continue
        entry: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.images:
            entry["content"] = [{"type": "text", "text": m.content or ""}] + [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{img}"}}
                for img in m.images
            ]
        if m.tool_calls:
            import json

            entry["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}
                for tc in m.tool_calls
            ]
            entry["content"] = m.content or None
        out.append(entry)
    return out


def tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"type": "function", "function": {
        "name": t["name"], "description": t.get("description", ""),
        "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
    }} for t in tools]


def messages_to_ollama(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """Native Ollama uses object arguments and tool_name on tool results."""
    out: list[dict[str, Any]] = []
    for m in messages:
        entry: dict[str, Any] = {"role": m.role, "content": m.content or ""}
        if m.images:
            entry["images"] = list(m.images)
        if m.role == "tool":
            entry["tool_name"] = m.name
        if m.tool_calls:
            from .tool_calls import coerce_arguments

            entry["tool_calls"] = [
                {"function": {"name": tc.name, "arguments": coerce_arguments(tc.arguments)}}
                for tc in m.tool_calls
            ]
        out.append(entry)
    return out
