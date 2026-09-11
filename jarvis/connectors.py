"""Connector Manager — enregistrement unique des accès externes.

Un connecteur = { id, type, name, config (non sensible), permissions, enabled }
+ des secrets stockés séparément dans le Secret Vault, référencés par `id`.

L'API ne renvoie jamais un secret : `secret_fields` indique seulement quels
champs sont renseignés, avec un aperçu masqué.
"""
from __future__ import annotations

import base64
import json
import os
import re
import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .db import Database, dumps, loads, new_id
from .permissions import PERMISSIONS


@dataclass
class FieldSpec:
    key: str
    label: str
    kind: str = "text"          # text | password | number | textarea | select | path | bool
    required: bool = False
    secret: bool = False
    placeholder: str = ""
    options: tuple[str, ...] = ()
    default: Any = ""
    help: str = ""


@dataclass
class ConnectorType:
    type: str
    label: str
    category: str
    icon: str
    fields: tuple[FieldSpec, ...]
    default_permissions: tuple[str, ...] = ("read",)
    supports_test: bool = True
    description: str = ""
    provider_of: str = ""       # "llm" pour les fournisseurs de modèles


def _f(key, label, **kw) -> FieldSpec:
    return FieldSpec(key=key, label=label, **kw)


# ---------------------------------------------------------------------------
# Catalogue des types de connecteurs
# ---------------------------------------------------------------------------
CONNECTOR_TYPES: dict[str, ConnectorType] = {}


def _register(ct: ConnectorType) -> None:
    CONNECTOR_TYPES[ct.type] = ct


_register(ConnectorType(
    type="ssh", label="SSH", category="Infrastructure", icon="terminal",
    description="Serveur distant accessible en SSH (clé ou mot de passe).",
    default_permissions=("read", "write", "execute"),
    fields=(
        _f("host", "Hôte", required=True, placeholder="exemple.com"),
        _f("port", "Port", kind="number", default=22),
        _f("username", "Utilisateur", required=True, placeholder="root"),
        _f("auth_method", "Authentification", kind="select", options=("key", "password"), default="key"),
        _f("key_path", "Chemin clé privée", kind="path", placeholder="~/.ssh/id_ed25519"),
        _f("password", "Mot de passe", kind="password", secret=True),
        _f("private_key", "Clé privée (collée)", kind="textarea", secret=True,
           help="Alternative au chemin. Stockée chiffrée."),
        _f("passphrase", "Passphrase de la clé", kind="password", secret=True),
        _f("working_directory", "Répertoire de travail", placeholder="/home/deploy/public_html"),
        _f("remote_path", "Chemin de déploiement", placeholder="/home/deploy/public_html"),
        _f("local_path", "Projet local associé", kind="path"),
    ),
))

_register(ConnectorType(
    type="sftp", label="SFTP", category="Infrastructure", icon="folder",
    description="Transfert de fichiers via SSH.",
    default_permissions=("read", "write"),
    fields=(
        _f("host", "Hôte", required=True), _f("port", "Port", kind="number", default=22),
        _f("username", "Utilisateur", required=True),
        _f("password", "Mot de passe", kind="password", secret=True),
        _f("key_path", "Chemin clé privée", kind="path"),
        _f("remote_path", "Dossier distant", default="/"),
    ),
))

_register(ConnectorType(
    type="ftp", label="FTP / FTPS", category="Infrastructure", icon="folder",
    description="Serveur FTP classique.",
    default_permissions=("read", "write"),
    fields=(
        _f("host", "Hôte", required=True), _f("port", "Port", kind="number", default=21),
        _f("username", "Utilisateur", required=True),
        _f("password", "Mot de passe", kind="password", secret=True),
        _f("tls", "FTPS (TLS)", kind="bool", default=True),
        _f("remote_path", "Dossier distant", default="/"),
    ),
))

_register(ConnectorType(
    type="cpanel", label="cPanel", category="Hébergement", icon="server",
    description="UAPI cPanel via jeton API.",
    default_permissions=("read", "write"),
    fields=(
        _f("host", "URL cPanel", required=True, placeholder="https://exemple.com:2083"),
        _f("username", "Utilisateur cPanel", required=True),
        _f("token", "Jeton API", kind="password", secret=True, required=True),
        _f("verify_ssl", "Vérifier le certificat", kind="bool", default=True),
    ),
))

_register(ConnectorType(
    type="whm", label="WHM", category="Hébergement", icon="server",
    description="API WHM (root ou revendeur).",
    default_permissions=("read", "admin"),
    fields=(
        _f("host", "URL WHM", required=True, placeholder="https://exemple.com:2087"),
        _f("username", "Utilisateur", required=True, default="root"),
        _f("token", "Jeton API", kind="password", secret=True, required=True),
        _f("verify_ssl", "Vérifier le certificat", kind="bool", default=True),
    ),
))

_register(ConnectorType(
    type="plesk", label="Plesk", category="Hébergement", icon="server",
    description="API REST Plesk.",
    default_permissions=("read", "write"),
    fields=(
        _f("host", "URL Plesk", required=True, placeholder="https://exemple.com:8443"),
        _f("username", "Utilisateur", required=True),
        _f("password", "Mot de passe", kind="password", secret=True),
        _f("api_key", "Clé API (alternative)", kind="password", secret=True),
        _f("verify_ssl", "Vérifier le certificat", kind="bool", default=True),
    ),
))

_register(ConnectorType(
    type="github", label="GitHub", category="Développement", icon="git",
    description="API GitHub (dépôts, PR, issues, actions).",
    default_permissions=("read", "write"),
    fields=(
        _f("api_url", "URL API", default="https://api.github.com"),
        _f("username", "Compte", placeholder="loopingdu91"),
        _f("token", "Token (PAT)", kind="password", secret=True, required=True),
        _f("default_repo", "Dépôt par défaut", placeholder="user/repo"),
    ),
))

_register(ConnectorType(
    type="gitlab", label="GitLab", category="Développement", icon="git",
    default_permissions=("read", "write"),
    fields=(
        _f("api_url", "URL API", default="https://gitlab.com/api/v4"),
        _f("token", "Token", kind="password", secret=True, required=True),
        _f("default_project", "Projet par défaut"),
    ),
))

_register(ConnectorType(
    type="n8n", label="n8n", category="Automatisation", icon="workflow",
    description="Instance n8n (locale ou distante).",
    default_permissions=("read", "execute"),
    fields=(
        _f("url", "URL", required=True, default="http://127.0.0.1:5678"),
        _f("api_key", "Clé API", kind="password", secret=True),
    ),
))

_register(ConnectorType(
    type="mysql", label="MySQL / MariaDB", category="Base de données", icon="database",
    default_permissions=("read",),
    fields=(
        _f("host", "Hôte", required=True, default="127.0.0.1"),
        _f("port", "Port", kind="number", default=3306),
        _f("database", "Base"), _f("username", "Utilisateur", required=True),
        _f("password", "Mot de passe", kind="password", secret=True),
        _f("via_ssh", "Via connecteur SSH (id)", help="Exécute le client mysql à travers ce serveur SSH."),
    ),
))

_register(ConnectorType(
    type="postgres", label="PostgreSQL", category="Base de données", icon="database",
    default_permissions=("read",),
    fields=(
        _f("host", "Hôte", required=True, default="127.0.0.1"),
        _f("port", "Port", kind="number", default=5432),
        _f("database", "Base"), _f("username", "Utilisateur", required=True),
        _f("password", "Mot de passe", kind="password", secret=True),
        _f("via_ssh", "Via connecteur SSH (id)"),
    ),
))

_register(ConnectorType(
    type="google", label="Google (OAuth)", category="Productivité", icon="google",
    description="Gmail, Agenda, Drive via OAuth Desktop. Sans OAuth, les raccourcis web restent disponibles.",
    default_permissions=("read",),
    fields=(
        _f("credentials_file", "credentials.json", kind="path"),
        _f("token_file", "token.json", kind="path"),
        _f("client_id", "Client ID"),
        _f("client_secret", "Client secret", kind="password", secret=True),
        _f("refresh_token", "Refresh token", kind="password", secret=True),
        _f("scopes", "Scopes", default="calendar.readonly gmail.readonly drive.readonly"),
    ),
))

_register(ConnectorType(
    type="webhook", label="Webhook", category="Automatisation", icon="link",
    default_permissions=("execute",),
    fields=(
        _f("url", "URL", required=True), _f("method", "Méthode", kind="select",
                                            options=("POST", "GET", "PUT"), default="POST"),
        _f("secret", "Secret / signature", kind="password", secret=True),
        _f("headers", "En-têtes JSON", kind="textarea", placeholder='{"X-Token": "..."}'),
    ),
))

_register(ConnectorType(
    type="http_api", label="API personnalisée", category="Automatisation", icon="link",
    default_permissions=("read", "execute"),
    fields=(
        _f("base_url", "URL de base", required=True),
        _f("api_key", "Clé API", kind="password", secret=True),
        _f("auth_header", "En-tête d'auth", default="Authorization"),
        _f("auth_prefix", "Préfixe", default="Bearer"),
        _f("headers", "En-têtes JSON", kind="textarea"),
    ),
))

_register(ConnectorType(
    type="discord", label="Discord", category="Communication", icon="chat",
    default_permissions=("read", "write"),
    fields=(
        _f("webhook_url", "URL Webhook", kind="password", secret=True),
        _f("bot_token", "Token bot", kind="password", secret=True),
        _f("default_channel", "Salon par défaut"),
    ),
))

_register(ConnectorType(
    type="slack", label="Slack", category="Communication", icon="chat",
    default_permissions=("read", "write"),
    fields=(
        _f("webhook_url", "URL Webhook", kind="password", secret=True),
        _f("bot_token", "Token bot (xoxb-)", kind="password", secret=True),
        _f("default_channel", "Canal par défaut", default="#general"),
    ),
))

_register(ConnectorType(
    type="docker", label="Docker", category="Infrastructure", icon="server",
    default_permissions=("read", "execute"),
    fields=(
        _f("mode", "Mode", kind="select", options=("local", "ssh"), default="local"),
        _f("via_ssh", "Connecteur SSH (id)", help="Requis si mode = ssh."),
    ),
))

_register(ConnectorType(
    type="smtp", label="Email (SMTP)", category="Communication", icon="mail",
    default_permissions=("write",),
    fields=(
        _f("host", "Serveur SMTP", required=True), _f("port", "Port", kind="number", default=587),
        _f("username", "Utilisateur", required=True),
        _f("password", "Mot de passe", kind="password", secret=True),
        _f("from_address", "Expéditeur"), _f("tls", "STARTTLS", kind="bool", default=True),
    ),
))

_register(ConnectorType(
    type="imap", label="Email (IMAP)", category="Communication", icon="mail",
    default_permissions=("read",),
    fields=(
        _f("host", "Serveur IMAP", required=True), _f("port", "Port", kind="number", default=993),
        _f("username", "Utilisateur", required=True),
        _f("password", "Mot de passe", kind="password", secret=True),
        _f("ssl", "SSL", kind="bool", default=True),
    ),
))

# --- Moteurs de génération d'image ------------------------------------------
_register(ConnectorType(
    type="comfyui", label="ComfyUI", category="Génération d'image", icon="brain",
    description=("Moteur de diffusion local ComfyUI (progression et aperçus temps réel). "
                 "Détecte aussi les modèles multi-fichiers : Z-Image-Turbo, Flux…"),
    default_permissions=("read", "execute"),
    fields=(
        _f("base_url", "URL de base", required=True, default="http://127.0.0.1:8188",
           placeholder="http://127.0.0.1:8188"),
        _f("engine", "Moteur (auto)", help="zimage | sd | flux… Vide = meilleur moteur détecté."),
        _f("models_dir", "Dossier modèles (vide = auto)",
           help="Dossier ComfyUI/models local si le disque n'est pas détecté automatiquement."),
        _f("steps", "Étapes", kind="number", default=8),
        _f("cfg", "CFG", kind="number", default=1),
        _f("sampler", "Sampler", default="euler"),
        _f("scheduler", "Scheduler", default="simple"),
        _f("timeout", "Délai max (s)", kind="number", default=600),
    ),
))

_register(ConnectorType(
    type="automatic1111", label="AUTOMATIC1111 / Forge", category="Génération d'image", icon="brain",
    description="WebUI Stable Diffusion lancée avec --api (aperçu live pendant le rendu).",
    default_permissions=("read", "execute"),
    fields=(
        _f("base_url", "URL de base", required=True, default="http://127.0.0.1:7860"),
        _f("steps", "Étapes", kind="number", default=25),
        _f("cfg", "CFG", kind="number", default=7),
        _f("sampler", "Sampler", default="DPM++ 2M Karras"),
        _f("denoise", "Denoise (retouche)", kind="number", default=0.6),
    ),
))

# --- Fournisseurs de modèles ------------------------------------------------
for _t, _label, _base, _model, _needs_key in (
    ("openai", "OpenAI", "https://api.openai.com/v1", "gpt-4o-mini", True),
    ("anthropic", "Anthropic / Claude", "https://api.anthropic.com/v1", "claude-sonnet-4-5", True),
    ("gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta", "gemini-2.0-flash", True),
    ("groq", "Groq", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", True),
    ("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "", True),
    ("ollama", "Ollama", "http://127.0.0.1:11434", "", False),
):
    _register(ConnectorType(
        type=_t, label=_label, category="Fournisseurs IA", icon="brain", provider_of="llm",
        default_permissions=("read", "execute"),
        fields=(
            _f("base_url", "URL de base", default=_base),
            _f("api_key", "Clé API", kind="password", secret=True, required=_needs_key),
            _f("default_model", "Modèle par défaut", default=_model),
        ),
    ))


def type_catalog() -> list[dict[str, Any]]:
    out = []
    for ct in CONNECTOR_TYPES.values():
        out.append({
            "type": ct.type, "label": ct.label, "category": ct.category, "icon": ct.icon,
            "description": ct.description, "supports_test": ct.supports_test,
            "provider_of": ct.provider_of,
            "default_permissions": list(ct.default_permissions),
            "fields": [
                {"key": f.key, "label": f.label, "kind": f.kind, "required": f.required,
                 "secret": f.secret, "placeholder": f.placeholder, "options": list(f.options),
                 "default": f.default, "help": f.help}
                for f in ct.fields
            ],
        })
    return sorted(out, key=lambda x: (x["category"], x["label"]))


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------
class ConnectorManager:
    def __init__(self, db: Database, vault, events, audit) -> None:
        self._db = db
        self._vault = vault
        self._events = events
        self._audit = audit
        self._lock = threading.RLock()

    # -- CRUD --------------------------------------------------------------
    def list(self, include_disabled: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM connectors"
        if not include_disabled:
            sql += " WHERE enabled=1"
        sql += " ORDER BY type, name"
        return [self._public(r) for r in self._db.query(sql)]

    def get(self, connector_id: str) -> dict[str, Any] | None:
        row = self._db.one("SELECT * FROM connectors WHERE id=?", (connector_id,))
        return self._public(row) if row else None

    def raw(self, connector_id: str) -> dict[str, Any] | None:
        """Config non sensible + type. Ne contient AUCUN secret."""
        row = self._db.one("SELECT * FROM connectors WHERE id=?", (connector_id,))
        if not row:
            return None
        d = {k: row[k] for k in row.keys()}
        d["config"] = loads(d["config"], {})
        d["permissions"] = loads(d["permissions"], ["read"])
        return d

    def by_type(self, ctype: str, enabled_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM connectors WHERE type=?"
        params: list[Any] = [ctype]
        if enabled_only:
            sql += " AND enabled=1"
        sql += " ORDER BY name"
        return [self._public(r) for r in self._db.query(sql, params)]

    def active(self, ctype: str = "") -> list[dict[str, Any]]:
        """Connecteurs réellement utilisables, filtrés par enable + test réussi."""
        rows = self.list(include_disabled=False)
        return [c for c in rows if (not ctype or c.get("type") == ctype)
                and c.get("status") == "connected"]

    def resolve_connector(self, ctype: str, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        candidates = self.active(ctype)
        if len(candidates) == 1:
            return self.raw(candidates[0]["id"])
        context = context or {}
        wanted = str(context.get("connector_id") or context.get("connector") or context.get("host") or "").casefold()
        matches = [c for c in candidates if wanted and wanted in {str(c.get("id", "")).casefold(), str(c.get("name", "")).casefold(), str(c.get("host", "")).casefold()}]
        return self.raw(matches[0]["id"]) if len(matches) == 1 else None

    def llm_context(self) -> list[dict[str, Any]]:
        """Contexte public minimal : jamais de secrets ni de config sensible.

        Inclut `working_directory` (source de vérité pour les chemins) : le
        modèle ne doit JAMAIS deviner un chemin quand un vrai existe.
        """
        out = []
        for c in self.active():
            cfg = c.get("config") or {}
            out.append({"id": c["id"], "type": c["type"], "name": c["name"],
                        "host": cfg.get("host") or cfg.get("url") or "",
                        "working_directory": cfg.get("working_directory") or "",
                        "deployment_path": cfg.get("deployment_path") or "",
                        "remote_path": cfg.get("remote_path") or "",
                        "connected": True,
                        "permissions": c.get("permissions", [])})
        return out

    def routing_candidates(self, ctype: str) -> list[dict[str, Any]]:
        """Connecteurs utilisables pour le routage déterministe.

        Priorité aux connecteurs « connected », mais un connecteur activé
        reste candidat même si son statut est 'error' : un échec de lecture
        antérieur ne doit pas désactiver silencieusement le workflow.
        """
        connected = self.active(ctype)
        if connected:
            return connected
        return [c for c in self.list(include_disabled=False) if c.get("type") == ctype]

    def find(self, query: str, ctype: str = "") -> dict[str, Any] | None:
        """Résolution souple : id exact, nom, hôte, puis sous-chaîne."""
        q = (query or "").strip().casefold()
        rows = self._db.query(
            "SELECT * FROM connectors WHERE enabled=1" + (" AND type=?" if ctype else ""),
            (ctype,) if ctype else (),
        )
        items = [self.raw(r["id"]) for r in rows]
        items = [i for i in items if i]
        if not q:
            return items[0] if len(items) == 1 else None
        for i in items:
            if i["id"].casefold() == q or i["name"].casefold() == q:
                return i
            cfg = i.get("config") or {}
            if str(cfg.get("host", "")).casefold() == q or str(cfg.get("url", "")).casefold() == q:
                return i
        for i in items:
            hay = f"{i['id']} {i['name']} {(i.get('config') or {}).get('host','')}".casefold()
            if q in hay:
                return i
        if len(items) == 1:
            return items[0]
        return None

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        ctype = str(payload.get("type") or "").strip()
        spec = CONNECTOR_TYPES.get(ctype)
        if not spec:
            raise ValueError(f"Type de connecteur inconnu: {ctype}")
        name = str(payload.get("name") or spec.label).strip()[:120]
        cid = str(payload.get("id") or "").strip() or self._slug(name, ctype)
        if self._db.one("SELECT 1 FROM connectors WHERE id=?", (cid,)):
            cid = f"{cid}-{new_id()[:4]}"
        config, secret_values = self._split_fields(spec, payload.get("config") or {})
        perms = [p for p in (payload.get("permissions") or spec.default_permissions) if p in PERMISSIONS]
        now = time.time()
        with self._lock:
            self._db.execute(
                "INSERT INTO connectors(id, type, name, config, permissions, enabled, status, status_detail, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (cid, ctype, name, dumps(config), dumps(perms),
                 1 if payload.get("enabled", True) else 0, "unknown", "", now, now),
            )
            for field, value in secret_values.items():
                self._vault.set(cid, field, value)
        self._audit.record(action=f"Connecteur créé: {name} ({ctype})", tool="connectors", connector_id=cid)
        self._events.emit("connector.updated", {"id": cid, "type": ctype, "name": name})
        return self.get(cid)  # type: ignore[return-value]

    def update(self, connector_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.raw(connector_id)
        if not current:
            raise ValueError("Connecteur introuvable.")
        spec = CONNECTOR_TYPES.get(current["type"])
        if not spec:
            raise ValueError("Type de connecteur inconnu.")
        name = str(payload.get("name") or current["name"]).strip()[:120]
        incoming = payload.get("config")
        if incoming is None:
            config = current["config"]
            secret_values: dict[str, str] = {}
        else:
            config, secret_values = self._split_fields(spec, incoming, existing=current["config"])
        perms = payload.get("permissions")
        perms = [p for p in perms if p in PERMISSIONS] if isinstance(perms, list) else current["permissions"]
        enabled = payload.get("enabled", current["enabled"])
        with self._lock:
            self._db.execute(
                "UPDATE connectors SET name=?, config=?, permissions=?, enabled=?, updated_at=? WHERE id=?",
                (name, dumps(config), dumps(perms), 1 if enabled else 0, time.time(), connector_id),
            )
            for field, value in secret_values.items():
                if value == "":       # champ vidé explicitement → suppression
                    self._vault.delete(connector_id, field)
                else:
                    self._vault.set(connector_id, field, value)
        self._audit.record(action=f"Connecteur modifié: {name}", tool="connectors", connector_id=connector_id)
        self._events.emit("connector.updated", {"id": connector_id, "name": name})
        return self.get(connector_id)  # type: ignore[return-value]

    def delete(self, connector_id: str) -> bool:
        current = self.raw(connector_id)
        if not current:
            return False
        with self._lock:
            self._vault.delete_all(connector_id)
            self._db.execute("DELETE FROM connectors WHERE id=?", (connector_id,))
        self._audit.record(action=f"Connecteur supprimé: {current['name']}", tool="connectors", connector_id=connector_id)
        self._events.emit("connector.deleted", {"id": connector_id})
        return True

    def set_enabled(self, connector_id: str, enabled: bool) -> dict[str, Any] | None:
        self._db.execute(
            "UPDATE connectors SET enabled=?, updated_at=? WHERE id=?",
            (1 if enabled else 0, time.time(), connector_id),
        )
        self._events.emit("connector.updated", {"id": connector_id, "enabled": bool(enabled)})
        return self.get(connector_id)

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _slug(name: str, ctype: str) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or ctype
        return base[:40]

    def _split_fields(
        self, spec: ConnectorType, incoming: dict[str, Any], existing: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """Sépare la config publique des valeurs secrètes."""
        config: dict[str, Any] = dict(existing or {})
        secret_values: dict[str, str] = {}
        for f in spec.fields:
            if f.key not in incoming:
                continue
            value = incoming[f.key]
            if f.secret:
                text = "" if value is None else str(value)
                # Une valeur masquée renvoyée telle quelle = pas de changement.
                if text.startswith("••••") or text == "[configuré]":
                    continue
                secret_values[f.key] = text
                continue
            if f.kind == "number":
                try:
                    config[f.key] = int(value)
                except (TypeError, ValueError):
                    config[f.key] = f.default or 0
            elif f.kind == "bool":
                config[f.key] = bool(value) if not isinstance(value, str) else value.lower() in {"1", "true", "on", "oui"}
            else:
                config[f.key] = "" if value is None else str(value)
        for f in spec.fields:
            if not f.secret and f.key not in config and f.default != "":
                config[f.key] = f.default
        return config, secret_values

    def _public(self, row) -> dict[str, Any]:
        d = {k: row[k] for k in row.keys()}
        cid = d["id"]
        d["config"] = loads(d["config"], {})
        d["permissions"] = loads(d["permissions"], ["read"])
        spec = CONNECTOR_TYPES.get(d["type"])
        secret_fields = {}
        if spec:
            for f in spec.fields:
                if f.secret:
                    has = self._vault.has(cid, f.key)
                    secret_fields[f.key] = {"configured": has, "preview": self._vault.preview(cid, f.key) if has else ""}
            d["label"] = spec.label
            d["category"] = spec.category
            d["icon"] = spec.icon
            d["provider_of"] = spec.provider_of
        d["secret_fields"] = secret_fields
        d["enabled"] = bool(d["enabled"])
        return d

    # -- test de connexion --------------------------------------------------
    def test(self, connector_id: str) -> tuple[bool, str]:
        c = self.raw(connector_id)
        if not c:
            return False, "Connecteur introuvable."
        started = time.time()
        try:
            ok, detail = self._run_test(c)
        except Exception as exc:  # jamais de secret dans le message
            ok, detail = False, self._vault.scrub(str(exc))[:400]
        duration = int((time.time() - started) * 1000)
        status = "connected" if ok else "error"
        now = time.time()
        self._db.execute(
            "UPDATE connectors SET status=?, status_detail=?, last_test_at=?, last_connected_at=? WHERE id=?",
            (status, detail[:500], now, now if ok else c.get("last_connected_at"), connector_id),
        )
        self._audit.record(
            action=f"Test connexion {c['name']}", tool="connectors", connector_id=connector_id,
            status="ok" if ok else "error", duration_ms=duration, detail=detail[:500],
        )
        self._events.emit(
            "connector.connected" if ok else "connector.failed",
            {"id": connector_id, "name": c["name"], "type": c["type"], "detail": detail[:300]},
        )
        if not ok:
            self._events.feed(
                f"Connecteur {c['name']} injoignable", level="warn", kind="connector",
                detail=detail[:300], source=c["type"], meta={"connector_id": connector_id},
            )
        return ok, detail

    def test_all(self) -> dict[str, Any]:
        results = {}
        for c in self.list(include_disabled=False):
            ok, detail = self.test(c["id"])
            results[c["id"]] = {"ok": ok, "detail": detail}
        return results

    def _run_test(self, c: dict[str, Any]) -> tuple[bool, str]:
        cid, ctype, cfg = c["id"], c["type"], c.get("config") or {}
        sec = lambda field, default="": self._vault.get(cid, field, default)  # noqa: E731

        if ctype in {"ssh", "sftp"}:
            from .tools.ssh_client import ssh_exec

            ok, out = ssh_exec(cfg, {"password": sec("password"), "private_key": sec("private_key"),
                                     "passphrase": sec("passphrase")}, "echo JARVIS_OK && hostname", timeout=20)
            if ok and "JARVIS_OK" in out:
                host = out.replace("JARVIS_OK", "").strip().splitlines()
                return True, f"Connecté ({host[-1] if host else cfg.get('host','')})"
            return False, out[:400] or "Connexion SSH refusée."

        if ctype == "ftp":
            return self._test_ftp(cfg, sec("password"))

        if ctype in {"cpanel", "whm"}:
            host = str(cfg.get("host") or "").rstrip("/")
            user = str(cfg.get("username") or "")
            token = sec("token")
            if not (host and user and token):
                return False, "Hôte, utilisateur ou jeton manquant."
            if ctype == "cpanel":
                url = f"{host}/execute/StatsBar/get_stats?display=hostname"
                headers = {"Authorization": f"cpanel {user}:{token}"}
            else:
                url = f"{host}/json-api/version?api.version=1"
                headers = {"Authorization": f"whm {user}:{token}"}
            ok, payload = http_json(url, headers=headers, verify_ssl=bool(cfg.get("verify_ssl", True)), timeout=15)
            return (True, "API accessible") if ok else (False, str(payload)[:400])

        if ctype == "plesk":
            host = str(cfg.get("host") or "").rstrip("/")
            key = sec("api_key")
            headers = {"Accept": "application/json"}
            if key:
                headers["X-API-Key"] = key
            else:
                creds = f"{cfg.get('username','')}:{sec('password')}".encode()
                headers["Authorization"] = "Basic " + base64.b64encode(creds).decode()
            ok, payload = http_json(f"{host}/api/v2/server", headers=headers,
                                    verify_ssl=bool(cfg.get("verify_ssl", True)), timeout=15)
            return (True, "API Plesk accessible") if ok else (False, str(payload)[:400])

        if ctype == "github":
            token = sec("token")
            if not token:
                return False, "Token GitHub manquant."
            ok, payload = http_json(f"{str(cfg.get('api_url') or 'https://api.github.com').rstrip('/')}/user",
                                    headers={"Authorization": f"Bearer {token}",
                                             "Accept": "application/vnd.github+json"}, timeout=15)
            if ok and isinstance(payload, dict) and payload.get("login"):
                return True, f"Authentifié comme {payload['login']}"
            return False, str(payload)[:400]

        if ctype == "gitlab":
            token = sec("token")
            ok, payload = http_json(f"{str(cfg.get('api_url') or '').rstrip('/')}/user",
                                    headers={"PRIVATE-TOKEN": token}, timeout=15)
            if ok and isinstance(payload, dict) and payload.get("username"):
                return True, f"Authentifié comme {payload['username']}"
            return False, str(payload)[:400]

        if ctype == "n8n":
            url = str(cfg.get("url") or "").rstrip("/")
            if not url:
                return False, "URL n8n manquante."
            ok, _ = http_json(f"{url}/healthz", timeout=5)
            if ok:
                return True, "n8n en ligne"
            key = sec("api_key")
            ok2, payload = http_json(f"{url}/api/v1/workflows?limit=1",
                                     headers={"X-N8N-API-KEY": key} if key else {}, timeout=6)
            return (True, "API n8n joignable") if ok2 else (False, str(payload)[:300])

        if ctype in {"mysql", "postgres"}:
            host, port = str(cfg.get("host") or "127.0.0.1"), int(cfg.get("port") or (3306 if ctype == "mysql" else 5432))
            return self._test_socket(host, port)

        if ctype in {"smtp", "imap"}:
            return self._test_socket(str(cfg.get("host") or ""), int(cfg.get("port") or 0))

        if ctype == "google":
            cred = str(cfg.get("credentials_file") or "")
            token_file = str(cfg.get("token_file") or "")
            if cred and Path(cred).expanduser().is_file():
                if token_file and Path(token_file).expanduser().is_file():
                    return True, "OAuth configuré (credentials + token)"
                return True, "credentials.json présent — jeton OAuth à générer"
            if sec("refresh_token"):
                return True, "Refresh token enregistré"
            return False, "Aucun credentials.json ni refresh token : seuls les raccourcis web sont disponibles."

        if ctype == "comfyui":
            from .comfyui_detect import detect_comfy_image_engines

            base = str(cfg.get("base_url") or "http://127.0.0.1:8188").rstrip("/")
            det = detect_comfy_image_engines(base, cfg.get("models_dir"))
            if not det.get("reachable"):
                return False, f"ComfyUI injoignable : {det.get('detail') or base}"
            engines = det.get("engines") or []
            if not engines:
                return False, ("ComfyUI répond mais aucun modèle exploitable "
                               "(ni checkpoint, ni diffusion_models + text_encoders + vae).")
            labels = ", ".join(f"{e['label']}" for e in engines)
            return True, f"ComfyUI prêt — {len(engines)} moteur(s) : {labels}"

        if ctype == "automatic1111":
            base = str(cfg.get("base_url") or "http://127.0.0.1:7860").rstrip("/")
            ok, payload = http_json(f"{base}/sdapi/v1/sd-models", timeout=8)
            if not ok:
                return False, f"WebUI injoignable (lancée avec --api ?) : {str(payload)[:140]}"
            count = len(payload) if isinstance(payload, list) else 0
            if not count:
                return False, "WebUI répond mais aucun modèle n'est installé."
            return True, f"WebUI prête ({count} modèle(s))"

        if ctype == "ollama":
            base = str(cfg.get("base_url") or "http://127.0.0.1:11434").rstrip("/")
            ok, payload = http_json(f"{base}/api/tags", timeout=4)
            if ok and isinstance(payload, dict):
                models = [m.get("name") for m in payload.get("models", []) if m.get("name")]
                if models:
                    return True, f"{len(models)} modèle(s): " + ", ".join(models[:4])
                return False, "Ollama répond mais aucun modèle n'est installé (ollama pull …)."
            return False, "Ollama injoignable."

        if ctype in {"openai", "groq", "openrouter"}:
            base = str(cfg.get("base_url") or "").rstrip("/")
            key = sec("api_key")
            if not key:
                return False, "Clé API manquante."
            ok, payload = http_json(f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=15)
            if ok:
                data = payload.get("data") if isinstance(payload, dict) else None
                return True, f"{len(data)} modèle(s) disponibles" if isinstance(data, list) else "API accessible"
            return False, str(payload)[:300]

        if ctype == "anthropic":
            key = sec("api_key")
            if not key:
                return False, "Clé API manquante."
            base = str(cfg.get("base_url") or "https://api.anthropic.com/v1").rstrip("/")
            ok, payload = http_json(f"{base}/models",
                                    headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout=15)
            if ok:
                data = payload.get("data") if isinstance(payload, dict) else None
                return True, f"{len(data)} modèle(s) disponibles" if isinstance(data, list) else "API accessible"
            return False, str(payload)[:300]

        if ctype == "gemini":
            key = sec("api_key")
            if not key:
                return False, "Clé API manquante."
            base = str(cfg.get("base_url") or "").rstrip("/")
            ok, payload = http_json(f"{base}/models?key={urllib.parse.quote(key)}", timeout=15)
            if ok and isinstance(payload, dict) and payload.get("models"):
                return True, f"{len(payload['models'])} modèle(s) disponibles"
            return False, str(payload)[:300]

        if ctype in {"discord", "slack"}:
            hook = sec("webhook_url")
            token = sec("bot_token")
            if ctype == "slack" and token:
                ok, payload = http_json("https://slack.com/api/auth.test",
                                        headers={"Authorization": f"Bearer {token}"}, method="POST", timeout=12)
                if ok and isinstance(payload, dict) and payload.get("ok"):
                    return True, f"Bot Slack authentifié ({payload.get('team','')})"
                return False, str(payload)[:300]
            if hook:
                return True, "Webhook enregistré (non déclenché pour éviter un message de test)."
            return False, "Aucun webhook ni token configuré."

        if ctype == "docker":
            if str(cfg.get("mode") or "local") == "local":
                try:
                    out = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                                         capture_output=True, text=True, timeout=12)
                    if out.returncode == 0:
                        return True, f"Docker {out.stdout.strip()}"
                    return False, (out.stderr or "Docker ne répond pas.").strip()[:300]
                except FileNotFoundError:
                    return False, "Docker n'est pas installé."
                except Exception as exc:
                    return False, str(exc)[:200]
            return False, "Mode SSH : testez plutôt le connecteur SSH associé."

        if ctype in {"webhook", "http_api"}:
            url = str(cfg.get("url") or cfg.get("base_url") or "")
            if not url:
                return False, "URL manquante."
            ok, payload = http_json(url, method="GET", timeout=10)
            return (True, "Point de terminaison joignable") if ok else (False, str(payload)[:300])

        return False, "Aucun test disponible pour ce type."

    @staticmethod
    def _test_socket(host: str, port: int) -> tuple[bool, str]:
        if not host or not port:
            return False, "Hôte ou port manquant."
        try:
            with socket.create_connection((host, port), timeout=8):
                return True, f"Port {port} ouvert sur {host}"
        except Exception as exc:
            return False, f"Connexion impossible: {exc}"

    @staticmethod
    def _test_ftp(cfg: dict[str, Any], password: str) -> tuple[bool, str]:
        import ftplib

        host = str(cfg.get("host") or "")
        port = int(cfg.get("port") or 21)
        user = str(cfg.get("username") or "")
        try:
            client = ftplib.FTP_TLS() if cfg.get("tls", True) else ftplib.FTP()
            client.connect(host, port, timeout=15)
            client.login(user, password)
            if isinstance(client, ftplib.FTP_TLS):
                client.prot_p()
            pwd = client.pwd()
            client.quit()
            return True, f"Connecté (répertoire {pwd})"
        except Exception as exc:
            return False, str(exc)[:300]

    # -- migration depuis l'ancien connections.json ------------------------
    def migrate_legacy(self, path: Path) -> int:
        """Importe l'ancien data/connections.json (secrets → vault, fichier sauvegardé)."""
        if not path.exists():
            return 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return 0
        if not isinstance(data, dict):
            return 0
        imported = 0
        placeholder = re.compile(r"^(REMPLACE|exemple\.com|cpaneluser|deploy$)", re.I)

        for srv in data.get("servers") or []:
            host = str(srv.get("host") or "")
            if not host or placeholder.match(host):
                continue
            if self._db.one("SELECT 1 FROM connectors WHERE type='ssh' AND json_extract(config,'$.host')=?", (host,)):
                continue
            self.create({
                "type": "ssh", "name": srv.get("name") or srv.get("id") or host,
                "id": str(srv.get("id") or "").strip() or None,
                "config": {
                    "host": host, "port": int(srv.get("port") or 22),
                    "username": srv.get("user") or "", "auth_method": "key",
                    "key_path": srv.get("key_path") or "",
                    "remote_path": srv.get("remote_path") or "",
                    "local_path": srv.get("local_path") or "",
                },
                "permissions": ["read", "write", "execute"],
            })
            imported += 1

        for acc in data.get("cpanel") or []:
            host = str(acc.get("host") or "")
            token = str(acc.get("token") or "")
            if not host or placeholder.match(host) or placeholder.match(token):
                continue
            if self._db.one("SELECT 1 FROM connectors WHERE type='cpanel' AND json_extract(config,'$.host')=?", (host,)):
                continue
            self.create({
                "type": "cpanel", "name": acc.get("name") or "cPanel",
                "config": {"host": host, "username": acc.get("user") or "", "token": token,
                           "verify_ssl": bool(acc.get("verify_ssl", True))},
                "permissions": ["read", "write"],
            })
            imported += 1

        n8n = data.get("n8n") or {}
        if n8n.get("url") and not self._db.one("SELECT 1 FROM connectors WHERE type='n8n'"):
            self.create({"type": "n8n", "name": "n8n",
                         "config": {"url": n8n["url"], "api_key": n8n.get("api_key") or ""},
                         "permissions": ["read", "execute"]})
            imported += 1

        google = data.get("google") or {}
        if (google.get("credentials_file") or google.get("token_file")) and not self._db.one(
            "SELECT 1 FROM connectors WHERE type='google'"
        ):
            self.create({"type": "google", "name": "Google",
                         "config": {"credentials_file": google.get("credentials_file") or "",
                                    "token_file": google.get("token_file") or ""}})
            imported += 1

        if imported:
            from .db import copy_file_backup

            copy_file_backup(path, "legacy-import")
            self._audit.record(action=f"Migration connections.json: {imported} connecteur(s) importé(s)", tool="connectors")
        return imported

    def bootstrap_from_env(self) -> int:
        """Crée les connecteurs LLM à partir des variables d'environnement, une fois."""
        created = 0
        env_map = {
            "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
            "gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "openrouter": "OPENROUTER_API_KEY",
        }
        for ctype, env_key in env_map.items():
            value = os.getenv(env_key, "").strip()
            if not value or self._db.one("SELECT 1 FROM connectors WHERE type=?", (ctype,)):
                continue
            spec = CONNECTOR_TYPES[ctype]
            base = next((f.default for f in spec.fields if f.key == "base_url"), "")
            model = next((f.default for f in spec.fields if f.key == "default_model"), "")
            self.create({"type": ctype, "name": spec.label,
                         "config": {"base_url": base, "api_key": value, "default_model": model}})
            created += 1
        ollama_url = os.getenv("JARVIS_OLLAMA_URL", "http://127.0.0.1:11434").strip()
        if ollama_url and not self._db.one("SELECT 1 FROM connectors WHERE type='ollama'"):
            self.create({"type": "ollama", "name": "Ollama",
                         "config": {"base_url": ollama_url,
                                    "default_model": os.getenv("JARVIS_OLLAMA_MODEL", "").strip()}})
            created += 1
        return created


# ---------------------------------------------------------------------------
# HTTP utilitaire partagé
# ---------------------------------------------------------------------------
def http_json(
    url: str, *, method: str = "GET", headers: dict[str, str] | None = None,
    body: Any = None, verify_ssl: bool = True, timeout: float = 20.0,
) -> tuple[bool, Any]:
    data = None
    hdrs = {"Accept": "application/json", "User-Agent": "JARVIS/3.0"}
    hdrs.update(headers or {})
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        else:
            data = str(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    ctx = None
    if url.lower().startswith("https") and not verify_ssl:
        ctx = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        if not raw.strip():
            return True, {}
        try:
            return True, json.loads(raw)
        except json.JSONDecodeError:
            return True, {"raw": raw[:8000]}
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:1500]
        except Exception:
            detail = exc.reason
        return False, f"HTTP {exc.code}: {detail}"
    except Exception as exc:
        return False, str(exc)
