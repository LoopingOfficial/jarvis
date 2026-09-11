"""Implémentations concrètes des fournisseurs de modèles."""
from __future__ import annotations

import json
from typing import Any

from ..connectors import http_json
from .base import ChatMessage, LLMProvider, LLMResponse, ToolCall, messages_to_openai, messages_to_ollama, tools_to_openai


def _llm_debug(message: str) -> None:
    import os

    if os.getenv("JARVIS_DEBUG", "0").lower() in {"1", "true", "yes", "on"}:
        print(f"[llm] {message}", flush=True)


def _safe_tool_args(arguments: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (arguments or {}).items():
        if k in {"password", "token", "api_key", "secret", "private_key", "passphrase"}:
            out[k] = "[masqué]"
        elif isinstance(v, str) and len(v) > 200:
            out[k] = v[:200] + "…"
        else:
            out[k] = v
    return out


class OpenAICompatProvider(LLMProvider):
    """OpenAI, Groq, OpenRouter — même schéma /chat/completions."""

    type = "openai"
    supports_tools = True
    supports_embeddings = True
    supports_vision = True

    def _headers(self) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if self.type == "openrouter":
            h["HTTP-Referer"] = "http://127.0.0.1"
            h["X-Title"] = "JARVIS"
        return h

    def models(self) -> list[str]:
        if not self.api_key:
            return []
        ok, payload = http_json(f"{self.base_url}/models", headers=self._headers(), timeout=15)
        if not ok or not isinstance(payload, dict):
            return []
        return sorted(m.get("id", "") for m in payload.get("data", []) if m.get("id"))

    def chat(self, messages, *, model="", tools=None, temperature=0.3, max_tokens=4096, timeout=180.0):
        model = model or self.default_model
        if not model:
            return LLMResponse(error="Aucun modèle sélectionné pour ce fournisseur.")
        body: dict[str, Any] = {
            "model": model, "messages": messages_to_openai(messages),
            "temperature": temperature, "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools_to_openai(tools)
            body["tool_choice"] = "auto"
        ok, payload = http_json(f"{self.base_url}/chat/completions", method="POST",
                                headers=self._headers(), body=body, timeout=timeout)
        if not ok:
            return LLMResponse(error=str(payload)[:600], provider=self.type, model=model)
        try:
            choice = payload["choices"][0]["message"]
        except Exception:
            return LLMResponse(error="Réponse inattendue du fournisseur.", provider=self.type, model=model)
        calls = []
        for tc in choice.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            calls.append(ToolCall(id=tc.get("id", fn.get("name", "")), name=fn.get("name", ""), arguments=args))
        return LLMResponse(text=(choice.get("content") or "").strip(), tool_calls=calls,
                           model=model, provider=self.type, usage=payload.get("usage") or {}, raw=payload)

    def embed(self, text: str, model: str = "") -> list[float]:
        model = model or "text-embedding-3-small"
        if not self.api_key:
            return []
        ok, payload = http_json(f"{self.base_url}/embeddings", method="POST", headers=self._headers(),
                                body={"model": model, "input": text[:8000]}, timeout=30)
        if not ok or not isinstance(payload, dict):
            return []
        try:
            return list(payload["data"][0]["embedding"])
        except Exception:
            return []


class GroqProvider(OpenAICompatProvider):
    type = "groq"
    supports_embeddings = False

    def embed(self, text: str, model: str = "") -> list[float]:
        return []


class OpenRouterProvider(OpenAICompatProvider):
    type = "openrouter"
    supports_embeddings = False

    def embed(self, text: str, model: str = "") -> list[float]:
        return []


class AnthropicProvider(LLMProvider):
    type = "anthropic"
    supports_tools = True
    supports_vision = True

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}

    def models(self) -> list[str]:
        if not self.api_key:
            return []
        ok, payload = http_json(f"{self.base_url}/models", headers=self._headers(), timeout=15)
        if not ok or not isinstance(payload, dict):
            return []
        return [m.get("id", "") for m in payload.get("data", []) if m.get("id")]

    def chat(self, messages, *, model="", tools=None, temperature=0.3, max_tokens=4096, timeout=180.0):
        model = model or self.default_model or "claude-sonnet-4-5"
        system_parts = [m.content for m in messages if m.role == "system" and m.content]
        converted: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                continue
            if m.role == "tool":
                converted.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content[:20000]}]})
                continue
            if m.role == "assistant" and m.tool_calls:
                blocks: list[dict[str, Any]] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
                converted.append({"role": "assistant", "content": blocks})
                continue
            if m.images:
                blocks: list[dict[str, Any]] = [
                    {"type": "image", "source": {"type": "base64",
                                                 "media_type": "image/png", "data": img}}
                    for img in m.images]
                blocks.append({"type": "text", "text": m.content or ""})
                converted.append({"role": m.role, "content": blocks})
                continue
            converted.append({"role": m.role, "content": m.content})
        body: dict[str, Any] = {"model": model, "messages": converted or [{"role": "user", "content": "Bonjour"}],
                                "max_tokens": max_tokens, "temperature": temperature}
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        if tools:
            body["tools"] = [{"name": t["name"], "description": t.get("description", "")[:900],
                              "input_schema": t.get("input_schema") or {"type": "object", "properties": {}}}
                             for t in tools]
        ok, payload = http_json(f"{self.base_url}/messages", method="POST", headers=self._headers(),
                                body=body, timeout=timeout)
        if not ok:
            return LLMResponse(error=str(payload)[:600], provider=self.type, model=model)
        text_parts, calls = [], []
        for block in (payload.get("content") or []) if isinstance(payload, dict) else []:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                calls.append(ToolCall(id=block.get("id", ""), name=block.get("name", ""),
                                      arguments=block.get("input") or {}))
        return LLMResponse(text="".join(text_parts).strip(), tool_calls=calls, model=model,
                           provider=self.type, usage=payload.get("usage") or {}, raw=payload)


class GeminiProvider(LLMProvider):
    type = "gemini"
    supports_tools = True
    supports_embeddings = True
    supports_vision = True

    def models(self) -> list[str]:
        if not self.api_key:
            return []
        ok, payload = http_json(f"{self.base_url}/models?key={self.api_key}", timeout=15)
        if not ok or not isinstance(payload, dict):
            return []
        out = []
        for m in payload.get("models", []):
            name = str(m.get("name", "")).replace("models/", "")
            if name and "generateContent" in (m.get("supportedGenerationMethods") or []):
                out.append(name)
        return out

    def chat(self, messages, *, model="", tools=None, temperature=0.3, max_tokens=4096, timeout=180.0):
        model = model or self.default_model or "gemini-2.0-flash"
        contents: list[dict[str, Any]] = []
        system_parts = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
                continue
            if m.role == "tool":
                contents.append({"role": "user", "parts": [{"functionResponse": {
                    "name": m.name or "tool", "response": {"result": m.content[:20000]}}}]})
                continue
            if m.role == "assistant" and m.tool_calls:
                parts = ([{"text": m.content}] if m.content else []) + [
                    {"functionCall": {"name": tc.name, "args": tc.arguments}} for tc in m.tool_calls]
                contents.append({"role": "model", "parts": parts})
                continue
            parts: list[dict[str, Any]] = [{"text": m.content}]
            for img in m.images:
                parts.append({"inlineData": {"mimeType": "image/png", "data": img}})
            contents.append({"role": "model" if m.role == "assistant" else "user",
                             "parts": parts})
        body: dict[str, Any] = {"contents": contents or [{"role": "user", "parts": [{"text": "Bonjour"}]}],
                                "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}}
        if system_parts:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        if tools:
            body["tools"] = [{"functionDeclarations": [
                {"name": t["name"], "description": t.get("description", "")[:900],
                 "parameters": _clean_gemini_schema(t.get("input_schema") or {"type": "object", "properties": {}})}
                for t in tools]}]
        ok, payload = http_json(f"{self.base_url}/models/{model}:generateContent?key={self.api_key}",
                                method="POST", body=body, timeout=timeout)
        if not ok:
            return LLMResponse(error=str(payload)[:600], provider=self.type, model=model)
        text_parts, calls = [], []
        try:
            parts = payload["candidates"][0]["content"]["parts"]
        except Exception:
            parts = []
        for i, part in enumerate(parts):
            if "text" in part:
                text_parts.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"]
                calls.append(ToolCall(id=f"gem_{i}", name=fc.get("name", ""), arguments=fc.get("args") or {}))
        return LLMResponse(text="".join(text_parts).strip(), tool_calls=calls, model=model,
                           provider=self.type, usage=payload.get("usageMetadata") or {}, raw=payload)

    def embed(self, text: str, model: str = "") -> list[float]:
        model = model or "text-embedding-004"
        ok, payload = http_json(f"{self.base_url}/models/{model}:embedContent?key={self.api_key}",
                                method="POST", body={"content": {"parts": [{"text": text[:8000]}]}}, timeout=30)
        if not ok or not isinstance(payload, dict):
            return []
        return list((payload.get("embedding") or {}).get("values") or [])


def _clean_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Gemini refuse certains mots-clés JSON Schema."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    out: dict[str, Any] = {}
    for k, v in schema.items():
        if k in {"additionalProperties", "$schema", "default", "examples"}:
            continue
        if k == "type" and isinstance(v, list):
            v = next((t for t in v if t != "null"), "string")
        if isinstance(v, dict):
            v = _clean_gemini_schema(v)
        elif isinstance(v, list) and k == "properties":
            pass
        out[k] = v
    if out.get("type") == "object" and "properties" in out and isinstance(out["properties"], dict):
        out["properties"] = {pk: _clean_gemini_schema(pv) if isinstance(pv, dict) else pv
                             for pk, pv in out["properties"].items()}
    return out


class OllamaProvider(LLMProvider):
    type = "ollama"
    supports_tools = True
    supports_embeddings = True
    # La vision depend du MODELE, pas du serveur : interrogee via /api/show.
    supports_vision = True

    @property
    def base_url(self) -> str:
        return str(self.config.get("base_url") or "http://127.0.0.1:11434").rstrip("/")

    def model_capabilities(self, model: str = "") -> list[str]:
        """Capacites reelles declarees par Ollama pour ce modele.

        Renvoie par exemple ['completion', 'vision', 'tools']. Liste vide si
        le serveur ne repond pas : on ne suppose JAMAIS la vision.
        """
        model = model or self.default_model
        if not model:
            return []
        cached = getattr(self, "_caps_cache", None)
        if cached is None:
            cached = self._caps_cache = {}
        if model in cached:
            return cached[model]
        ok, payload = http_json(f"{self.base_url}/api/show", method="POST",
                                body={"model": model}, timeout=10)
        caps = []
        if ok and isinstance(payload, dict):
            caps = [str(c) for c in (payload.get("capabilities") or [])]
        cached[model] = caps
        return caps

    def vision_model(self, model: str = "") -> str:
        """Modele local capable de vision : celui par defaut s'il l'est, sinon
        le premier installe qui declare la capacite. '' si aucun."""
        model = model or self.default_model
        if model and "vision" in self.model_capabilities(model):
            return model
        for name in self.models():
            if name != model and "vision" in self.model_capabilities(name):
                return name
        return ""

    def models(self) -> list[str]:
        ok, payload = http_json(f"{self.base_url}/api/tags", timeout=4)
        if not ok or not isinstance(payload, dict):
            return []
        return [m.get("name", "") for m in payload.get("models", []) if m.get("name")]

    def chat(self, messages, *, model="", tools=None, temperature=0.3, max_tokens=4096, timeout=180.0):
        available = self.models()
        model = model or self.default_model or (available[0] if available else "")
        if not model:
            return LLMResponse(error="Aucun modèle Ollama installé (ollama pull llama3.1).",
                               provider=self.type)
        body: dict[str, Any] = {
            "model": model, "stream": False, "messages": messages_to_ollama(messages),
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if tools:
            body["tools"] = tools_to_openai(tools)
        _llm_debug(f"OLLAMA REQUEST model={model} tools={len(tools or [])} messages={len(messages)}")
        ok, payload = http_json(f"{self.base_url}/api/chat", method="POST", body=body, timeout=timeout)
        if not ok:
            return LLMResponse(error=str(payload)[:500], provider=self.type, model=model)
        message = (payload.get("message") or {}) if isinstance(payload, dict) else {}
        from .tool_calls import coerce_arguments

        calls = []
        for i, tc in enumerate(message.get("tool_calls") or []):
            fn = tc.get("function", {})
            args = coerce_arguments(fn.get("arguments"))
            calls.append(ToolCall(id=f"oll_{i}", name=fn.get("name", ""), arguments=args))
        _llm_debug("OLLAMA RESPONSE text=" + ("oui" if message.get("content") else "non")
                   + " tool_calls=" + json.dumps([{"name": c.name, "arguments": _safe_tool_args(c.arguments)}
                                                  for c in calls], ensure_ascii=False))
        return LLMResponse(text=(message.get("content") or "").strip(), tool_calls=calls,
                           model=model, provider=self.type, raw=payload)

    def embed(self, text: str, model: str = "") -> list[float]:
        model = model or "nomic-embed-text"
        ok, payload = http_json(f"{self.base_url}/api/embeddings", method="POST",
                                body={"model": model, "prompt": text[:8000]}, timeout=30)
        if not ok or not isinstance(payload, dict):
            return []
        return list(payload.get("embedding") or [])


PROVIDER_CLASSES = {
    "openai": OpenAICompatProvider,
    "groq": GroqProvider,
    "openrouter": OpenRouterProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "ollama": OllamaProvider,
}
