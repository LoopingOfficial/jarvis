"""Client WebSocket minimal (RFC 6455), sans dépendance externe.

Utilisé pour écouter la progression et les aperçus intermédiaires de ComfyUI,
qui ne les expose que sur son canal WebSocket. Volontairement réduit à ce dont
JARVIS a besoin : connexion, lecture de trames texte/binaires, ping/pong.
"""
from __future__ import annotations

import base64
import os
import socket
import ssl
import struct
from urllib.parse import urlparse


class WSError(Exception):
    pass


class WebSocketClient:
    def __init__(self, url: str, timeout: float = 30.0) -> None:
        self.url = url
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buf = b""

    # -- cycle de vie ------------------------------------------------------
    def connect(self) -> None:
        parsed = urlparse(self.url)
        secure = parsed.scheme == "wss"
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if secure else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        raw = socket.create_connection((host, port), timeout=self.timeout)
        if secure:
            raw = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        raw.sendall(handshake.encode())
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = raw.recv(4096)
            if not chunk:
                raise WSError("Handshake WebSocket interrompu.")
            response += chunk
            if len(response) > 65536:
                raise WSError("Handshake WebSocket trop long.")
        if b"101" not in response.split(b"\r\n", 1)[0]:
            raise WSError(f"Handshake refusé : {response[:120]!r}")
        self._buf = response.split(b"\r\n\r\n", 1)[1]
        self._sock = raw

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- E/S bas niveau ----------------------------------------------------
    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            if self._sock is None:
                raise WSError("Socket fermée.")
            chunk = self._sock.recv(65536)
            if not chunk:
                raise WSError("Connexion WebSocket fermée par le serveur.")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _send_frame(self, opcode: int, payload: bytes = b"") -> None:
        if self._sock is None:
            return
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        header = bytes([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", length)
        self._sock.sendall(header + mask + masked)

    def recv(self) -> tuple[str, bytes]:
        """Renvoie ('text'|'binary', payload). Gère ping/pong en interne."""
        while True:
            first, second = self._read(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8))[0]
            masked = bool(second & 0x80)
            mask = self._read(4) if masked else b""
            payload = self._read(length) if length else b""
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x9:                     # ping
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:                     # pong
                continue
            if opcode == 0x8:                     # close
                raise WSError("Le serveur a fermé la connexion WebSocket.")
            if opcode == 0x1:
                return "text", payload
            if opcode == 0x2:
                return "binary", payload
            # continuation / opcodes inconnus : ignorés

    def settimeout(self, value: float) -> None:
        if self._sock is not None:
            self._sock.settimeout(value)
