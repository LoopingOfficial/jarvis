"""Normalisation et reprise des appels d'outil.

Cause du bug « JSON affiché dans le chat » :
les modèles locaux (Ollama / qwen / jarvis-blender) écrivent souvent

    {"name":"blender.inspect","arguments":{}}

en TEXTE au lieu d'un `tool_calls` structuré. Sans reprise, l'orchestrateur
traite ça comme la réponse finale.

Règle Ollama : `arguments` est un OBJET, jamais une chaîne JSON double-encodée.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .base import ToolCall

_TOOL_TAG = re.compile(r"</?tool_call>", re.IGNORECASE)
_FENCE = re.compile(r"^```(?:json|tool)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)
_NAME_LINE = re.compile(
    r"^(?:tool|name|function|call)\s*[:=]\s*([a-zA-Z][\w.]{1,80})\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def coerce_arguments(value: Any) -> dict[str, Any]:
    """Ramène n'importe quelle forme d'arguments à un dict.

    Ollama attend un objet. Une chaîne `\"{}\"` ou `\"{\\\"x\\\":1}\"` est
    décodée. Toute autre forme devient `{}`.
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, str):
            try:
                nested = json.loads(parsed)
            except Exception:
                return {}
            return nested if isinstance(nested, dict) else {}
        return {}
    return {}


def _extract_objects(raw: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for start in (m.start() for m in re.finditer(r"\{", raw)):
        depth = 0
        for position in range(start, len(raw)):
            char = raw[position]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(raw[start:position + 1])
                    except Exception:
                        break
                    if isinstance(parsed, dict):
                        objects.append(parsed)
                    break
        # Ne pas s'arrêter au premier objet : certains modèles préfixent
        # accidentellement leur tool call d'un objet de statut ou d'une pensée.
        # On doit pouvoir récupérer le premier objet dont le nom est autorisé.
    return objects


def _call_from_dict(parsed: dict[str, Any], allowed: set[str], index: int) -> ToolCall | None:
    function = parsed.get("function") if isinstance(parsed.get("function"), dict) else {}
    name = str(
        parsed.get("name")
        or parsed.get("tool")
        or parsed.get("tool_name")
        or function.get("name")
        or ""
    ).strip()
    if name not in allowed:
        return None
    arguments = parsed.get("arguments")
    if arguments is None:
        arguments = parsed.get("parameters")
    if arguments is None:
        arguments = function.get("arguments") or function.get("parameters")
    return ToolCall(id=f"txt_{index}", name=name, arguments=coerce_arguments(arguments))


def recover_text_tool_calls(text: str, allowed: set[str]) -> list[ToolCall]:
    """Convertit un dump JSON d'outil en vrais `ToolCall`.

    Ne convertit QUE si le nom est un outil réellement autorisé.
    """
    raw = _TOOL_TAG.sub(" ", text or "")
    raw = _FENCE.sub(" ", raw).strip()
    if not raw or not allowed:
        return []

    for index, parsed in enumerate(_extract_objects(raw)):
        inner = parsed.get("tool_call") or parsed.get("tool_calls")
        if isinstance(inner, dict):
            call = _call_from_dict(inner, allowed, index)
            if call:
                return [call]
        if isinstance(inner, list):
            for j, item in enumerate(inner):
                if isinstance(item, dict):
                    call = _call_from_dict(item, allowed, j)
                    if call:
                        return [call]
        call = _call_from_dict(parsed, allowed, index)
        if call:
            return [call]

    match = _NAME_LINE.search(raw)
    if match and match.group(1) in allowed:
        args: dict[str, Any] = {}
        for parsed in _extract_objects(raw):
            if "name" not in parsed and "tool" not in parsed:
                args = coerce_arguments(parsed)
                break
        return [ToolCall(id="txt_named", name=match.group(1), arguments=args)]
    return []


def looks_like_tool_call_dump(text: str, allowed: set[str] | None = None) -> bool:
    """Vrai si le texte EST un appel d'outil (et rien d'utile à afficher)."""
    raw = _TOOL_TAG.sub(" ", text or "")
    raw = _FENCE.sub(" ", raw).strip()
    if not raw:
        return False
    if allowed:
        return bool(recover_text_tool_calls(raw, allowed))
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = _extract_objects(raw)
        parsed = parsed[0] if parsed else None
    if not isinstance(parsed, dict):
        return False
    name = parsed.get("name") or parsed.get("tool") or ""
    return bool(name) and ("arguments" in parsed or "parameters" in parsed)


def strip_tool_call_text(text: str, allowed: set[str] | None = None) -> str:
    """Retire un dump d'outil du texte visible. Vide si le message n'était que ça."""
    if looks_like_tool_call_dump(text, allowed):
        return ""
    return (text or "").strip()


def recover_response_tool_calls(text: str, tools: list[dict[str, Any]] | None) -> list[ToolCall]:
    allowed: set[str] = set()
    for tool in tools or []:
        name = tool.get("name")
        if not name and isinstance(tool.get("function"), dict):
            name = tool["function"].get("name")
        if name:
            allowed.add(str(name))
    return recover_text_tool_calls(text, allowed)
