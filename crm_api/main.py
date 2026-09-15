"""Application FastAPI du mini-CRM.

Lancement local :
    uvicorn crm_api.main:app --reload

Toutes les routes métier sont derrière `require_api_key`. Seul `/health` reste
ouvert : c'est la sonde de l'hébergeur, qui ne connaît pas la clé, et elle ne
divulgue aucune donnée client.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .config import get_settings
from .database import engine, init_db
from .routers import contacts, interactions
from .security import require_api_key

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Créer les tables au démarrage évite une étape manuelle sur cPanel, où l'on
    # n'a pas toujours un shell. L'échec n'est pas fatal : l'hébergeur peut être
    # momentanément indisponible, et /health dira alors la vérité.
    try:
        init_db()
    except Exception:  # pragma: no cover - dépend de l'hébergeur
        logger.exception("Initialisation de la base impossible au démarrage")
    yield


app = FastAPI(
    lifespan=lifespan,
    title="JARVIS CRM API",
    version="1.0.0",
    description="Contacts et interactions, pilotables par l'assistant.",
)

_settings = get_settings()
if _settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_settings.cors_origins,
        allow_credentials=False,   # l'auth passe par un en-tête, pas par cookie
        allow_methods=["*"],
        allow_headers=["X-API-Key", "Content-Type"],
    )


@app.get("/health", tags=["service"])
def health() -> dict[str, str]:
    """Vérifie aussi la base : un processus vivant devant un MySQL injoignable
    n'est pas « en bonne santé », et un simple 200 le masquerait."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - dépend de l'hébergeur
        return {"status": "degraded", "database": "unreachable", "detail": str(exc)[:200]}
    return {"status": "ok", "database": "ok"}


app.include_router(contacts.router, dependencies=[Depends(require_api_key)])
app.include_router(interactions.router, dependencies=[Depends(require_api_key)])
