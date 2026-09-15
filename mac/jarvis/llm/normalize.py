"""Normalisation UNIQUE des messages et des reponses de modele.

Cause racine du bug `'dict' object has no attribute 'role'` :
`LLMManager.chat()` etait type `list[ChatMessage]`, mais plusieurs appelants
(analyse et evaluation d'avatar) passaient des dictionnaires bruts. Les
providers font `m.role` / `m.content` -> AttributeError, l'exception etait
avalee par un `except Exception` et le pipeline repartait sur des valeurs par
defaut. Le bug etait donc invisible et produisait un avatar generique.

Regle : plus personne ne suppose qu'un message est un objet. Tout entre par
`normalize_messages()`, applique une seule fois a la frontiere du manager.

Formats acceptes :
  * `ChatMessage` (dataclass interne)
  * `dict` — {"role": ..., "content": ..., "images": [...]}
  * dataclass quelconque exposant .role / .content
  * objet Pydantic (v1 `.dict()` / v2 `.model_dump()`)
  * objet provider quelconque exposant .role / .content
  * `str` — traite comme un message utilisateur
"""
from __future__ import annotations

from typing import Any

from .base import ChatMessage, LLMResponse, ToolCall
from .tool_calls import coerce_arguments

VALID_ROLES = {"system", "user", "assistant", "tool"}


def _as_mapping(value: Any) -> dict[str, Any] | None:
    """Renvoie une vue dict d'un objet Pydantic / dataclass, sinon None."""
    for attr in ("model_dump", "dict"):
        method = getattr(value, attr, None)
        if callable(method):
            try:
                data = method()
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
    data = getattr(value, "__dict__", None)
    return dict(data) if isinstance(data, dict) and data else None


def _coerce_content(value: Any) -> str:
    """Aplati un contenu en texte, y compris le format 'blocs' des providers."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        parts: list[str] = []
        for block in value:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                # {"type": "text", "text": ...} (OpenAI/Anthropic) ou {"text": ...}
                text = block.get("text") or block.get("content") or ""
                if isinstance(text, str):
                    parts.append(text)
            else:
                text = getattr(block, "text", None)
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(value)


def _coerce_tool_calls(value: Any) -> list[ToolCall]:
    out: list[ToolCall] = []
    for item in value or []:
        if isinstance(item, ToolCall):
            out.append(item)
            continue
        if isinstance(item, dict):
            function = item.get("function") if isinstance(item.get("function"), dict) else {}
            arguments = item.get("arguments", function.get("arguments"))
            # Un seul contrat traverse l'application : Ollama et les gateways
            # compatibles peuvent renvoyer soit un objet natif, soit une chaîne
            # JSON (parfois doublement encodée). Ne laissez pas cette variation
            # atteindre ToolRunner, sinon les arguments deviennent silencieusement
            # {} et le modèle peut afficher son appel au lieu de l'exécuter.
            arguments = coerce_arguments(arguments)
            out.append(ToolCall(
                id=str(item.get("id") or function.get("name") or ""),
                name=str(item.get("name") or function.get("name") or ""),
                arguments=arguments))
            continue
        name = getattr(item, "name", "")
        if name:
            arguments = coerce_arguments(getattr(item, "arguments", {}))
            out.append(ToolCall(id=str(getattr(item, "id", "") or name), name=str(name),
                                arguments=arguments))
    return out


def _coerce_images(value: Any) -> list[str]:
    """Images d'un message : base64 nu (sans prefixe data:), une par entree."""
    out: list[str] = []
    for item in value or []:
        if not isinstance(item, str) or not item:
            continue
        if item.startswith("data:"):
            _, _, payload = item.partition(",")
            item = payload
        out.append(item)
    return out


def normalize_message(message: Any) -> ChatMessage:
    """Transforme n'importe quelle representation de message en `ChatMessage`."""
    if isinstance(message, ChatMessage):
        return message
    if isinstance(message, str):
        return ChatMessage(role="user", content=message)

    source: dict[str, Any]
    if isinstance(message, dict):
        source = message
    else:
        mapped = _as_mapping(message)
        source = mapped if mapped is not None else {
            "role": getattr(message, "role", "user"),
            "content": getattr(message, "content", ""),
            "tool_calls": getattr(message, "tool_calls", None),
            "tool_call_id": getattr(message, "tool_call_id", ""),
            "name": getattr(message, "name", ""),
            "images": getattr(message, "images", None),
        }

    role = str(source.get("role") or "user").strip().lower()
    if role not in VALID_ROLES:
        # 'model' (Gemini) et 'ai' (LangChain) designent l'assistant.
        role = "assistant" if role in {"model", "ai"} else "user"
    return ChatMessage(
        role=role,
        content=_coerce_content(source.get("content")),
        tool_calls=_coerce_tool_calls(source.get("tool_calls")),
        tool_call_id=str(source.get("tool_call_id") or source.get("tool_use_id") or ""),
        name=str(source.get("name") or ""),
        images=_coerce_images(source.get("images")),
    )


def normalize_messages(messages: Any) -> list[ChatMessage]:
    """Normalise une liste (ou un message seul) en `list[ChatMessage]`."""
    if messages is None:
        return []
    if isinstance(messages, (str, dict)) or not isinstance(messages, (list, tuple)):
        return [normalize_message(messages)]
    return [normalize_message(m) for m in messages]


def normalize_llm_response(response: Any) -> LLMResponse:
    """Ramene toute reponse de provider a `LLMResponse` (text/tool_calls/...).

    Permet au code metier de ne JAMAIS dependre de `response.message.role`
    ni d'une forme specifique a un fournisseur.
    """
    if isinstance(response, LLMResponse):
        return response
    if response is None:
        return LLMResponse(error="Réponse vide du fournisseur.")
    if isinstance(response, str):
        return LLMResponse(text=response)

    source = response if isinstance(response, dict) else (_as_mapping(response) or {})

    # Forme {"message": {...}} (Ollama) ou {"choices": [{"message": {...}}]} (OpenAI).
    message: Any = source.get("message")
    if message is None:
        choices = source.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            message = first.get("message") if isinstance(first, dict) else None
    if message is None and not isinstance(response, dict):
        message = getattr(response, "message", None)

    text = ""
    tool_calls: list[ToolCall] = []
    if message is not None:
        normalized = normalize_message(message)
        text = normalized.content
        tool_calls = normalized.tool_calls
    if not text:
        text = _coerce_content(source.get("text") or source.get("content"))
    if not tool_calls:
        tool_calls = _coerce_tool_calls(source.get("tool_calls"))

    return LLMResponse(
        text=(text or "").strip(),
        tool_calls=tool_calls,
        model=str(source.get("model") or ""),
        provider=str(source.get("provider") or ""),
        usage=source.get("usage") if isinstance(source.get("usage"), dict) else {},
        raw=response,
        error=str(source.get("error") or ""),
    )
