"""Authentification par clé d'API.

L'API expose des données clients sur Internet : sans porte, n'importe qui
listerait le carnet d'adresses. Une clé partagée suffit ici car l'appelant est
une machine (l'assistant), pas un humain à qui il faudrait un compte.

`compare_digest` plutôt que `==` : une comparaison ordinaire s'arrête au premier
octet différent, et ce délai mesurable permet de reconstituer la clé caractère
par caractère.
"""
from __future__ import annotations

from secrets import compare_digest

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from .config import get_settings

_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(provided: str | None = Depends(_header)) -> None:
    if not provided or not compare_digest(provided, get_settings().api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clé d'API absente ou invalide (en-tête X-API-Key).",
        )
