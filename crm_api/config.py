"""Configuration lue dans l'environnement (jamais en dur dans le code).

Les identifiants MySQL cPanel ne doivent pas se retrouver dans le dépôt : ils
donnent un accès en écriture à des données clients. Le module refuse de
démarrer si `DB_PASSWORD` ou `CRM_API_KEY` manquent, plutôt que de se rabattre
sur une valeur par défaut — un défaut silencieux ici, c'est une API ouverte.
"""
from __future__ import annotations

import os
from functools import lru_cache
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Variable d'environnement manquante : {name}. "
            f"Copiez crm_api/.env.example vers .env et renseignez-la."
        )
    return value


class Settings:
    def __init__(self) -> None:
        # cPanel préfixe systématiquement bases et utilisateurs par le compte :
        # « moncompte_crm », « moncompte_crmuser ». Le nom complet est attendu ici.
        self.db_host: str = os.getenv("DB_HOST", "localhost").strip()
        self.db_port: int = int(os.getenv("DB_PORT", "3306"))
        self.db_name: str = _required("DB_NAME")
        self.db_user: str = _required("DB_USER")
        self.db_password: str = _required("DB_PASSWORD")

        # La clé que l'assistant IA présentera dans l'en-tête X-API-Key.
        self.api_key: str = _required("CRM_API_KEY")

        self.sql_echo: bool = os.getenv("SQL_ECHO", "0") == "1"
        # Origines autorisées pour le navigateur ; l'assistant, lui, appelle en
        # direct et n'est pas concerné par CORS.
        self.cors_origins: list[str] = [
            o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()
        ]

    @property
    def database_url(self) -> str:
        # quote_plus : un mot de passe généré par cPanel contient souvent @, #, /
        # qui casseraient l'URL s'ils n'étaient pas encodés.
        return (
            f"mysql+pymysql://{quote_plus(self.db_user)}:{quote_plus(self.db_password)}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
