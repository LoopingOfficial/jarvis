"""Moteur SQLAlchemy et session par requête.

Deux réglages ne sont pas cosmétiques face à un MySQL mutualisé cPanel :

* `pool_pre_ping` — le serveur ferme les connexions inactives (wait_timeout,
  souvent 300 s). Sans ping préalable, la première requête après une pause
  échoue avec « MySQL server has gone away ». Le ping coûte un aller-retour ;
  l'erreur coûte une requête perdue.
* `pool_recycle` — on referme nous-mêmes les connexions avant que le serveur
  ne le fasse, ce qui évite de dépendre du timing exact du ping.
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    echo=_settings.sql_echo,
    pool_pre_ping=True,
    pool_recycle=280,       # < wait_timeout habituel (300 s) des hébergements cPanel
    pool_size=5,            # un mutualisé limite souvent à ~10 connexions simultanées
    max_overflow=5,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Dépendance FastAPI : une session par requête, toujours refermée."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Crée les tables manquantes. Ne modifie jamais une table existante."""
    from . import models  # noqa: F401  (enregistre les modèles sur Base.metadata)

    Base.metadata.create_all(bind=engine)
