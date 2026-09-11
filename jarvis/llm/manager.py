"""LLM Manager : rôles de modèles, sélection, bascule automatique (fallback)."""
from __future__ import annotations

import re
import threading
import time
from typing import Any

from .base import ChatMessage, LLMProvider, LLMResponse
from .normalize import normalize_messages
from .providers import PROVIDER_CLASSES
from .tool_calls import coerce_arguments, recover_response_tool_calls

ROLES = ("default", "fast", "reasoning", "coding", "fallback", "blender")

# Erreur Go encoding/json renvoyée par certains backends (Ollama, gateways)
# quand le modèle génère des arguments d'outil avec un backslash non échappé
# (ex. \; au lieu de \\;). On peut le corriger en faisant régénérer le modèle.
_JSON_ESCAPE_ERROR = re.compile(r"invalid character '.' in string escape code")


class LLMManager:
    def __init__(self, connectors, vault, settings, events) -> None:
        self._connectors = connectors
        self._vault = vault
        self._settings = settings
        self._events = events
        self._lock = threading.RLock()
        self._status_cache: dict[str, dict[str, Any]] = {}
        self._status_at = 0.0
        self._probing = False

    # -- providers ---------------------------------------------------------
    def providers(self) -> list[LLMProvider]:
        out: list[LLMProvider] = []
        for ctype, cls in PROVIDER_CLASSES.items():
            for c in self._connectors.by_type(ctype, enabled_only=True):
                raw = self._connectors.raw(c["id"])
                if raw:
                    out.append(cls(raw, self._vault.get))
        return out

    def provider_by_id(self, connector_id: str) -> LLMProvider | None:
        raw = self._connectors.raw(connector_id)
        if not raw:
            return None
        cls = PROVIDER_CLASSES.get(raw["type"])
        return cls(raw, self._vault.get) if cls else None

    def status(self, max_age: float = 20.0, blocking: bool = False) -> list[dict[str, Any]]:
        """État réel de chaque fournisseur — jamais « Connected » sans vérification.

        Les sondes réseau (jusqu'à plusieurs secondes) ne bloquent jamais une
        requête d'interface : le cache est renvoyé immédiatement et rafraîchi
        en arrière-plan. Tant qu'aucune sonde n'a abouti, l'état affiché est
        « Vérification… » — jamais « Connected ».
        """
        with self._lock:
            fresh = self._status_cache and (time.time() - self._status_at) < max_age
            if fresh:
                return list(self._status_cache.values())
            cached = list(self._status_cache.values())
            already_probing = self._probing
            if not already_probing:
                self._probing = True

        if blocking or (not cached and not already_probing):
            if blocking:
                try:
                    return self._probe()
                finally:
                    with self._lock:
                        self._probing = False

        if not already_probing:
            threading.Thread(target=self._probe_safe, daemon=True, name="jarvis-llm-probe").start()
        return cached or self._placeholder()

    def _placeholder(self) -> list[dict[str, Any]]:
        """État provisoire sans appel réseau : configuré ou non, jamais connecté."""
        out: list[dict[str, Any]] = []
        seen = set()
        for ctype in PROVIDER_CLASSES:
            for c in self._connectors.by_type(ctype, enabled_only=True):
                seen.add(ctype)
                out.append({"id": c["id"], "type": ctype, "name": c["name"], "connected": False,
                            "detail": "Vérification…", "models": [],
                            "default_model": (c.get("config") or {}).get("default_model", ""),
                            "supports_tools": False})
        for ctype in PROVIDER_CLASSES:
            if ctype not in seen:
                out.append({"id": "", "type": ctype, "name": ctype.capitalize(), "connected": False,
                            "detail": "Not configured", "models": [], "default_model": "",
                            "supports_tools": False})
        return out

    def _probe_safe(self) -> None:
        try:
            self._probe()
        except Exception:
            pass
        finally:
            with self._lock:
                self._probing = False

    def _probe(self) -> list[dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        configured_types = set()
        for provider in self.providers():
            configured_types.add(provider.type)
            models = provider.models()
            connected = bool(models)
            detail = f"{len(models)} modèle(s)" if connected else "Aucun modèle / clé invalide"
            if provider.type != "ollama" and not provider.api_key:
                connected, detail = False, "Clé API manquante"
            result[provider.id] = {
                "id": provider.id, "type": provider.type, "name": provider.name,
                "connected": connected, "detail": detail, "models": models[:60],
                "default_model": provider.default_model, "supports_tools": provider.supports_tools,
            }
        for ctype in PROVIDER_CLASSES:
            if ctype not in configured_types:
                result[f"__{ctype}"] = {
                    "id": "", "type": ctype, "name": ctype.capitalize(), "connected": False,
                    "detail": "Not configured", "models": [], "default_model": "", "supports_tools": False,
                }
        with self._lock:
            self._status_cache = result
            self._status_at = time.time()
        self._events.emit("llm.status", {"providers": [
            {"type": p["type"], "connected": p["connected"], "detail": p["detail"]}
            for p in result.values()]})
        return list(result.values())

    def invalidate(self) -> None:
        with self._lock:
            self._status_cache = {}
            self._status_at = 0.0

    def available(self) -> list[dict[str, Any]]:
        return [s for s in self.status() if s["connected"]]

    # -- spécialiste Blender ----------------------------------------------
    #
    # Le modèle 3D dédié n'est JAMAIS confondu avec le modèle général :
    # `resolve("blender")` ne retombe pas en cascade sur le modèle par défaut.
    # Sans spécialiste, l'appelant doit le dire (exigence : pas de bascule
    # silencieuse du général sur une grosse génération 3D).
    BLENDER_MODEL_HINTS = ("jarvis-blender", "blender")

    def blender_specialist(self) -> tuple[LLMProvider | None, str]:
        """(provider, model) du spécialiste Blender, ou (None, '')."""
        ref = str(self._settings.get("ai", "blender_model", "") or "").strip()
        if ref:
            connector_id, _, model = ref.partition(":")
            provider = self.provider_by_id(connector_id.strip())
            if provider and model.strip():
                return provider, model.strip()

        with self._lock:
            cold = not self._status_cache
        for status in self.status(blocking=cold):
            if not status["connected"] or not status["id"]:
                continue
            provider = self.provider_by_id(status["id"])
            if provider is None:
                continue
            for model in status["models"]:
                base = str(model).split(":")[0].lower()
                if base in self.BLENDER_MODEL_HINTS or base.endswith("-blender"):
                    return provider, model
        return None, ""

    def blender_status(self) -> dict[str, Any]:
        """Disponibilité du spécialiste 3D, pour l'UI et le routage."""
        provider, model = self.blender_specialist()
        if provider is None:
            return {"available": False, "provider": "", "model": "",
                    "reason": "Le spécialiste Blender n'est pas disponible "
                              "(aucun modèle « jarvis-blender » chez un "
                              "fournisseur connecté)."}
        supports_tools = bool(getattr(provider, "supports_tools", False))
        return {"available": True, "provider": provider.type, "model": model,
                "name": provider.name, "supports_tools": supports_tools, "reason": ""}

    # -- résolution de modèle ---------------------------------------------
    def resolve(self, role: str = "default") -> tuple[LLMProvider | None, str]:
        """Retourne (provider, model) pour un rôle. Repli en cascade si non configuré."""
        # Rôle « blender » : spécialiste strict, aucune cascade vers le général.
        if role == "blender":
            return self.blender_specialist()
        setting_key = {"default": "default_model", "fast": "fast_model", "reasoning": "reasoning_model",
                       "coding": "coding_model", "fallback": "fallback_model"}.get(role, "default_model")
        ref = str(self._settings.get("ai", setting_key, "") or "").strip()
        if not ref and role != "default":
            ref = str(self._settings.get("ai", "default_model", "") or "").strip()
        if ref:
            connector_id, _, model = ref.partition(":")
            provider = self.provider_by_id(connector_id.strip())
            if provider:
                chosen = model.strip() or provider.default_model
                if not chosen:
                    models = provider.models()
                    chosen = models[0] if models else ""
                if chosen:
                    return provider, chosen
        # Auto : premier fournisseur réellement connecté, priorité aux outils.
        # Ici on bloque si nécessaire : un appel au modèle exige un état sûr.
        with self._lock:
            cold = not self._status_cache
        for status in self.status(blocking=cold):
            if not status["connected"] or not status["id"]:
                continue
            provider = self.provider_by_id(status["id"])
            if not provider:
                continue
            model = provider.default_model or (status["models"][0] if status["models"] else "")
            if model:
                return provider, model
        return None, ""

    def chat(
        self, messages: Any, *, role: str = "default", tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None, max_tokens: int = 4096, timeout: float = 180.0,
        model_override: str = "",
    ) -> LLMResponse:
        # Frontiere unique de normalisation : dict, dataclass, objet Pydantic ou
        # chaine sont acceptes. Sans cela, un appelant passant des dicts faisait
        # planter les providers sur `m.role` ('dict' object has no attribute
        # 'role') et le pipeline repartait silencieusement sur des defauts.
        messages = normalize_messages(messages)
        temperature = self._settings.get("ai", "temperature", 0.3) if temperature is None else temperature
        provider, model = self.resolve(role)
        if model_override:
            connector_id, _, m = model_override.partition(":")
            override_provider = self.provider_by_id(connector_id)
            if override_provider:
                provider, model = override_provider, (m or override_provider.default_model)
        if provider is None:
            if role == "blender":
                return LLMResponse(error=self.blender_status()["reason"])
            return LLMResponse(error=(
                "Aucun fournisseur de modèle connecté. Ouvre Settings → AI Providers "
                "pour ajouter une clé API, ou démarre Ollama en local."
            ))
        tried = []
        response = provider.chat(messages, model=model, tools=tools if provider.supports_tools else None,
                                 temperature=temperature, max_tokens=max_tokens, timeout=timeout)
        tried.append(f"{provider.type}:{model}")
        if response.ok:
            response = self._finalize_tool_response(response, tools)
            self._events.emit("llm.status", {"provider": provider.type, "model": model, "ok": True})
            return response

        # Auto-correction : si le backend a rejeté un JSON d'appel d'outil mal échappé
        # (backslash seul), on fait régénérer le modèle avant de basculer en fallback.
        healed = self._heal_escape_error(provider, messages, error=response.error, model=model,
                                         tools=tools, temperature=temperature,
                                         max_tokens=max_tokens, timeout=timeout)
        if healed is not None:
            self._events.emit("llm.status", {"provider": provider.type, "model": model, "ok": True,
                                             "retried": True})
            self._events.feed("Réponse du modèle corrigée", level="info", kind="llm",
                              detail="JSON d'appel d'outil invalide → régénération avec consigne.",
                              source=provider.type)
            return healed

        # Spécialiste 3D : jamais de bascule silencieuse vers le modèle
        # général — une grosse génération Blender improvisée est pire qu'un
        # refus explicite.
        if role == "blender":
            self._events.emit("llm.status", {"provider": provider.type, "model": model,
                                             "ok": False, "role": "blender",
                                             "error": response.error[:200]})
            return response
        if self._settings.get("ai", "auto_fallback", True):
            fallback_provider, fallback_model = self.resolve("fallback")
            candidates: list[tuple[LLMProvider, str]] = []
            if fallback_provider and f"{fallback_provider.type}:{fallback_model}" not in tried:
                candidates.append((fallback_provider, fallback_model))
            for status in self.status(max_age=0, blocking=True):
                if not status["connected"] or not status["id"]:
                    continue
                alt = self.provider_by_id(status["id"])
                if not alt:
                    continue
                alt_model = alt.default_model or (status["models"][0] if status["models"] else "")
                if alt_model and f"{alt.type}:{alt_model}" not in tried:
                    candidates.append((alt, alt_model))
            for alt, alt_model in candidates[:3]:
                self._events.emit("llm.status", {"provider": provider.type, "model": model, "ok": False,
                                                 "fallback_to": f"{alt.type}:{alt_model}"})
                self._events.feed(f"Bascule vers {alt.name}", level="warn", kind="llm",
                                  detail=f"{provider.name} indisponible: {response.error[:200]}", source="llm")
                alt_response = alt.chat(messages, model=alt_model,
                                        tools=tools if alt.supports_tools else None,
                                        temperature=temperature, max_tokens=max_tokens, timeout=timeout)
                tried.append(f"{alt.type}:{alt_model}")
                if alt_response.ok:
                    return self._finalize_tool_response(alt_response, tools)
                response = alt_response
        self._events.emit("llm.status", {"provider": provider.type, "model": model, "ok": False,
                                         "error": response.error[:200]})
        return response

    @staticmethod
    def _finalize_tool_response(response: LLMResponse,
                                tools: list[dict[str, Any]] | None) -> LLMResponse:
        """Normalise une réponse provider avant toute décision UI/orchestrateur.

        Le backend ne doit jamais avoir deux chemins divergents : un tool call
        natif et le même call sérialisé dans le texte aboutissent au même
        ``ToolCall`` avec ``arguments`` objet JSON. Le texte brut est vidé dès
        qu'il ne contient que l'appel récupéré, ce qui empêche son affichage.
        """
        for call in response.tool_calls:
            call.arguments = coerce_arguments(call.arguments)
        if not response.tool_calls and tools:
            recovered = recover_response_tool_calls(response.text, tools)
            if recovered:
                response.tool_calls = recovered
                response.text = ""
        return response

    # ------------------------------------------------------------------
    # Vision — une seule porte d'entrée pour analyser des IMAGES réelles
    # ------------------------------------------------------------------
    def vision_provider(self) -> tuple[LLMProvider | None, str]:
        """Premier fournisseur connecté capable de VISION, avec son modèle.

        Pour Ollama la capacité dépend du modèle : elle est lue sur le serveur
        (`/api/show` → capabilities). On ne suppose jamais la vision.
        """
        # Cache froid : il faut sonder les fournisseurs, sinon `status()`
        # renvoie une liste vide et la vision passerait pour indisponible.
        with self._lock:
            cold = not self._status_cache
        for status in self.status(blocking=cold):
            if not status["connected"] or not status["id"]:
                continue
            provider = self.provider_by_id(status["id"])
            if provider is None or not provider.supports_vision:
                continue
            picker = getattr(provider, "vision_model", None)
            if callable(picker):
                model = picker()
                if model:
                    return provider, model
                continue
            model = provider.default_model or (status["models"][0] if status["models"] else "")
            if model:
                return provider, model
        return None, ""

    def vision_status(self) -> dict[str, Any]:
        """État de la capacité vision, pour l'UI et les diagnostics."""
        provider, model = self.vision_provider()
        if provider is None:
            return {"available": False, "provider": "", "model": "",
                    "reason": "Aucun fournisseur connecté ne sait analyser une image."}
        return {"available": True, "provider": provider.type, "model": model,
                "name": provider.name, "reason": ""}

    def analyze_images(
        self, images: list[str], prompt: str, *, system: str = "",
        temperature: float = 0.1, max_tokens: int = 2048, timeout: float = 300.0,
    ) -> LLMResponse:
        """Envoie de VRAIES images (base64 nu) à un modèle de vision.

        Retourne une `LLMResponse` en erreur — jamais un texte inventé — si
        aucun fournisseur vision n'est disponible.
        """
        images = [i for i in (images or []) if i]
        if not images:
            return LLMResponse(error="Aucune image à analyser.")
        provider, model = self.vision_provider()
        if provider is None:
            return LLMResponse(error=self.vision_status()["reason"])
        messages: list[ChatMessage] = []
        if system:
            messages.append(ChatMessage(role="system", content=system))
        messages.append(ChatMessage(role="user", content=prompt, images=images))
        response = provider.chat(messages, model=model, tools=None,
                                 temperature=temperature, max_tokens=max_tokens,
                                 timeout=timeout)
        response.provider = response.provider or provider.type
        response.model = response.model or model
        return response

    def embed(self, text: str) -> list[float]:
        ref = str(self._settings.get("ai", "embedding_model", "") or "").strip()
        if ref:
            connector_id, _, model = ref.partition(":")
            provider = self.provider_by_id(connector_id)
            if provider and provider.supports_embeddings:
                return provider.embed(text, model)
        for provider in self.providers():
            if provider.supports_embeddings:
                vec = provider.embed(text)
                if vec:
                    return vec
        return []

    def _heal_escape_error(
        self, provider: LLMProvider, messages: list[ChatMessage], *, error: str,
        model: str, tools: list[dict[str, Any]] | None, temperature: float,
        max_tokens: int, timeout: float,
    ) -> LLMResponse | None:
        """Régénère la réponse si le backend rejette un JSON mal échappé.

        Go (Ollama, certaines gateways) renvoie « invalid character 'X' in string
        escape code » quand le modèle produit des arguments d'outil contenant un
        backslash non échappé (ex. ``\\;``). On refait l'appel sur une copie des
        messages, avec une consigne, sans polluer la conversation réelle.
        """
        if not _JSON_ESCAPE_ERROR.search(error):
            return None
        note = ChatMessage(role="user", content=(
            "[Correction JSON] Ton appel d'outil précédent a été rejeté par l'API : "
            "les backslashes dans les arguments JSON doivent être doublés (un "
            "backslash se représente par deux backslashes successifs, jamais seul "
            "devant un autre caractère). Refais cet appel d'outil avec des "
            "arguments JSON valides."
        ))
        final = ChatMessage(role="user", content=(
            note.content + " Si tu ne peux pas produire un JSON valide, réponds "
            "directement en texte, sans appeler d'outil."
        ))
        candidates = [
            list(messages),
            list(messages) + [note],
            list(messages) + [final],
        ]
        for attempt in candidates:
            resp = provider.chat(
                attempt, model=model,
                tools=tools if provider.supports_tools else None,
                temperature=temperature, max_tokens=max_tokens, timeout=timeout)
            if resp.ok:
                return resp
            if not _JSON_ESCAPE_ERROR.search(resp.error):
                break
        return None

    def model_options(self) -> list[dict[str, str]]:
        """Liste « connector_id:model » proposée dans Settings."""
        out = []
        for status in self.status():
            if not status["id"]:
                continue
            for model in status["models"][:40]:
                out.append({"value": f"{status['id']}:{model}",
                            "label": f"{status['name']} · {model}", "provider": status["type"]})
        return out
