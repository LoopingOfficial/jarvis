"""Secret Vault — chiffrement au repos des identifiants.

Principes non négociables :
  * aucun secret n'est jamais écrit en clair sur le disque ;
  * aucun secret n'est jamais renvoyé par l'API (seulement des aperçus masqués) ;
  * aucun secret n'est jamais transmis à un LLM ni journalisé ;
  * les agents manipulent uniquement un `connector_id`. Seul le Secure Tool
    Runner (tools/runner.py) déchiffre au moment de l'appel au service externe.

Clé maître :
  1. macOS Keychain (`security add-generic-password`) si disponible ;
  2. sinon fichier ~/.config/jarvis/master.key en 0600 ;
  3. variable JARVIS_MASTER_KEY (base64) prioritaire si définie (CI/tests).

Chiffrement : AES-256-GCM (cryptography) avec nonce aléatoire par secret ;
repli sur une construction HMAC-SHA256 + XOR de flux si `cryptography` est
absent (toujours authentifié, jamais du clair).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets as pysecrets
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .config import IS_DARWIN, IS_WINDOWS, USER_CONFIG_DIR, ensure_dirs
from .db import Database, new_id

KEYCHAIN_SERVICE = "jarvis-vault"
KEYCHAIN_ACCOUNT = "master-key"
KEY_FILE = USER_CONFIG_DIR / "master.key"

try:  # pragma: no cover - dépend de l'environnement
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    HAVE_AESGCM = True
except Exception:  # pragma: no cover
    AESGCM = None  # type: ignore
    HAVE_AESGCM = False


# ---------------------------------------------------------------------------
# Clé maître
# ---------------------------------------------------------------------------
def _keychain_get() -> bytes | None:
    if not IS_DARWIN:
        return None
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
            capture_output=True, text=True, timeout=8,
        )
        if out.returncode == 0 and out.stdout.strip():
            return base64.urlsafe_b64decode(out.stdout.strip())
    except Exception:
        pass
    return None


def _keychain_set(key: bytes) -> bool:
    if not IS_DARWIN:
        return False
    try:
        encoded = base64.urlsafe_b64encode(key).decode()
        out = subprocess.run(
            ["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE,
             "-a", KEYCHAIN_ACCOUNT, "-w", encoded],
            capture_output=True, text=True, timeout=8,
        )
        return out.returncode == 0
    except Exception:
        return False


def _file_get() -> bytes | None:
    try:
        if KEY_FILE.exists():
            return base64.urlsafe_b64decode(KEY_FILE.read_text().strip())
    except Exception:
        pass
    return None


def _file_set(key: bytes) -> None:
    ensure_dirs()
    KEY_FILE.write_text(base64.urlsafe_b64encode(key).decode())
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass


class MasterKey:
    def __init__(self) -> None:
        self.source = "unknown"
        self.key = self._resolve()

    def _resolve(self) -> bytes:
        env = os.getenv("JARVIS_MASTER_KEY", "").strip()
        if env:
            self.source = "env"
            digest = hashlib.sha256(env.encode("utf-8")).digest()
            return digest
        if IS_WINDOWS:
            from .windows import master_key
            try:
                key = master_key()
            except Exception as exc:
                raise RuntimeError("Coffre Windows indisponible. Relance install_windows.bat pour vérifier les dépendances et le Gestionnaire d’identifiants.") from exc
            self.source = "windows-credential-manager"
            return key
        key = _keychain_get()
        if key and len(key) == 32:
            self.source = "keychain"
            return key
        key = _file_get()
        if key and len(key) == 32:
            self.source = "file"
            return key
        key = pysecrets.token_bytes(32)
        if _keychain_set(key):
            self.source = "keychain"
        else:
            _file_set(key)
            self.source = "file"
        return key


# ---------------------------------------------------------------------------
# Chiffrement
# ---------------------------------------------------------------------------
def _fallback_encrypt(key: bytes, plaintext: bytes, aad: bytes) -> tuple[bytes, bytes]:
    """Chiffrement de repli authentifié (HMAC-SHA256 en mode compteur + MAC)."""
    nonce = pysecrets.token_bytes(16)
    stream = b""
    counter = 0
    while len(stream) < len(plaintext):
        stream += hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        counter += 1
    cipher = bytes(a ^ b for a, b in zip(plaintext, stream[: len(plaintext)]))
    mac = hmac.new(key, b"jarvis-v1" + nonce + aad + cipher, hashlib.sha256).digest()
    return nonce, cipher + mac


def _fallback_decrypt(key: bytes, nonce: bytes, blob: bytes, aad: bytes) -> bytes:
    cipher, mac = blob[:-32], blob[-32:]
    expected = hmac.new(key, b"jarvis-v1" + nonce + aad + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise ValueError("Secret altéré ou clé maître différente.")
    stream = b""
    counter = 0
    while len(stream) < len(cipher):
        stream += hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(a ^ b for a, b in zip(cipher, stream[: len(cipher)]))


class SecretVault:
    """Stocke les secrets chiffrés dans la table `secrets`."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._master = MasterKey()
        self._lock = threading.RLock()
        self._cache: dict[str, str] = {}

    @property
    def backend(self) -> str:
        algo = "aes-256-gcm" if HAVE_AESGCM else "hmac-sha256-ctr"
        return f"{algo} / clé maître: {self._master.source}"

    def _aad(self, connector_id: str, field: str) -> bytes:
        return f"{connector_id}:{field}".encode("utf-8")

    def _encrypt(self, connector_id: str, field: str, value: str) -> str:
        aad = self._aad(connector_id, field)
        data = value.encode("utf-8")
        if HAVE_AESGCM:
            nonce = pysecrets.token_bytes(12)
            blob = AESGCM(self._master.key).encrypt(nonce, data, aad)
            algo = "aesgcm"
        else:
            nonce, blob = _fallback_encrypt(self._master.key, data, aad)
            algo = "hmacctr"
        return json.dumps({
            "v": 1, "algo": algo,
            "n": base64.b64encode(nonce).decode(),
            "c": base64.b64encode(blob).decode(),
        })

    def _decrypt(self, connector_id: str, field: str, raw: str) -> str:
        payload = json.loads(raw)
        aad = self._aad(connector_id, field)
        nonce = base64.b64decode(payload["n"])
        blob = base64.b64decode(payload["c"])
        if payload.get("algo") == "aesgcm":
            if not HAVE_AESGCM:
                raise ValueError("Secret chiffré en AES-GCM mais la bibliothèque cryptography est absente.")
            return AESGCM(self._master.key).decrypt(nonce, blob, aad).decode("utf-8")
        return _fallback_decrypt(self._master.key, nonce, blob, aad).decode("utf-8")

    # -- API ---------------------------------------------------------------
    def set(self, connector_id: str, field: str, value: str) -> None:
        if value is None:
            return
        value = str(value)
        if not value:
            self.delete(connector_id, field)
            return
        with self._lock:
            blob = self._encrypt(connector_id, field, value)
            now = time.time()
            self._db.execute(
                "INSERT INTO secrets(id, connector_id, field, blob, created_at, updated_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(connector_id, field) DO UPDATE SET blob=excluded.blob, updated_at=excluded.updated_at",
                (new_id("sec"), connector_id, field, blob, now, now),
            )
            self._cache.pop(f"{connector_id}:{field}", None)

    def get(self, connector_id: str, field: str, default: str = "") -> str:
        """Déchiffre un secret. À n'appeler QUE depuis le Secure Tool Runner."""
        cache_key = f"{connector_id}:{field}"
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]
            row = self._db.one(
                "SELECT blob FROM secrets WHERE connector_id=? AND field=?", (connector_id, field)
            )
            if not row:
                return default
            try:
                value = self._decrypt(connector_id, field, row["blob"])
            except Exception:
                return default
            self._cache[cache_key] = value
            return value

    def has(self, connector_id: str, field: str) -> bool:
        return bool(self._db.one(
            "SELECT 1 FROM secrets WHERE connector_id=? AND field=?", (connector_id, field)
        ))

    def fields(self, connector_id: str) -> list[str]:
        return [r["field"] for r in self._db.query(
            "SELECT field FROM secrets WHERE connector_id=? ORDER BY field", (connector_id,)
        )]

    def preview(self, connector_id: str, field: str) -> str:
        """Aperçu masqué, jamais la valeur complète."""
        value = self.get(connector_id, field)
        if not value:
            return ""
        if len(value) <= 8:
            return "••••••"
        return "••••" + value[-4:]

    def delete(self, connector_id: str, field: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM secrets WHERE connector_id=? AND field=?", (connector_id, field))
            self._cache.pop(f"{connector_id}:{field}", None)

    def delete_all(self, connector_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM secrets WHERE connector_id=?", (connector_id,))
            for k in [k for k in self._cache if k.startswith(f"{connector_id}:")]:
                self._cache.pop(k, None)

    def count(self) -> int:
        return int(self._db.scalar("SELECT COUNT(*) FROM secrets") or 0)

    def scrub(self, text: str) -> str:
        """Retire de `text` toute valeur secrète connue (logs, sorties d'outils)."""
        if not text:
            return text
        out = text
        rows = self._db.query("SELECT connector_id, field FROM secrets")
        for r in rows:
            value = self.get(r["connector_id"], r["field"])
            if value and len(value) >= 6 and value in out:
                out = out.replace(value, "[secret masqué]")
        return out
