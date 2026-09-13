"""Client Ollama HTTP direct pour le LocalCodeAgent (indépendant du LLMManager)."""
from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Any


def _post(url: str, body: dict[str, Any], timeout: float = 180.0) -> tuple[bool, Any]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            detail = str(exc)
        return False, {"error": detail}
    except Exception as exc:
        return False, {"error": str(exc)}


def _get(url: str, timeout: float = 5.0) -> tuple[bool, Any]:
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return False, {"error": str(exc)}


class OllamaClient:
    def __init__(self, base_url: str = "http://127.0.0.1:11434") -> None:
        self._base = base_url.rstrip("/")

    def list_models(self) -> list[str]:
        ok, payload = _get(f"{self._base}/api/tags", timeout=4)
        if not ok:
            return []
        return [m.get("name", "") for m in (payload.get("models") or []) if m.get("name")]

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 8192,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "stream": False,
            "messages": messages,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if tools:
            body["tools"] = tools
        ok, payload = _post(f"{self._base}/api/chat", body, timeout=timeout)
        if not ok:
            return {"error": payload.get("error", "Unknown error"), "text": "", "tool_calls": []}
        message = payload.get("message") or {}
        calls = []
        for i, tc in enumerate(message.get("tool_calls") or []):
            fn = tc.get("function", {})
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            calls.append({"id": f"lc_{i}", "name": fn.get("name", ""), "arguments": args})
        return {
            "text": (message.get("content") or "").strip(),
            "tool_calls": calls,
            "model": payload.get("model", model),
            "raw": payload,
        }
