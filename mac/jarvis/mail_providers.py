"""Presets des fournisseurs de messagerie + détection + normalisation.

Pourquoi ce module existe
------------------------
Un seul connecteur « email » couvre à la fois l'envoi (SMTP) et la lecture
(IMAP) d'une même boîte. Plutôt que de demander à l'utilisateur hôte, port et
chiffrement pour chaque fournisseur, on les connaît ici : Gmail, Outlook /
Hotmail, iCloud, Yahoo, et un mode « custom » pour un serveur maison.

`mail_settings()` est la porte d'entrée unique : quelle que soit la forme du
connecteur (le type unifié `email`, l'ancien `imap`, l'ancien `smtp`), elle
renvoie les réglages concrets (hôte, port, SSL/STARTTLS, identifiant, adresse
d'envoi). Le code d'envoi et de lecture (jarvis/mail.py) ne manipule plus que
cette forme normalisée.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MailProvider:
    key: str
    label: str
    imap_host: str
    imap_port: int
    imap_ssl: bool
    smtp_host: str
    smtp_port: int
    smtp_tls: bool
    domains: tuple[str, ...]
    app_password: str = ""        # aide affichée : mot de passe d'application
    oauth: bool = False           # fournisseur où OAuth est préférable


MAIL_PROVIDERS: dict[str, MailProvider] = {
    "gmail": MailProvider(
        key="gmail", label="Gmail",
        imap_host="imap.gmail.com", imap_port=993, imap_ssl=True,
        smtp_host="smtp.gmail.com", smtp_port=587, smtp_tls=True,
        domains=("gmail.com", "googlemail.com"),
        app_password=("Gmail exige un mot de passe d'application : compte Google → "
                      "Sécurité → Mots de passe des applications."),
        oauth=True,
    ),
    "outlook": MailProvider(
        key="outlook", label="Outlook / Hotmail",
        imap_host="outlook.office365.com", imap_port=993, imap_ssl=True,
        smtp_host="smtp.office365.com", smtp_port=587, smtp_tls=True,
        domains=("outlook.com", "outlook.fr", "hotmail.com", "hotmail.fr",
                 "live.com", "live.fr", "msn.com", "office365.com", "microsoft.com"),
        app_password=("Microsoft exige un mot de passe d'application : compte Microsoft → "
                      "Sécurité → Mots de passe d'application."),
        oauth=True,
    ),
    "icloud": MailProvider(
        key="icloud", label="iCloud",
        imap_host="imap.mail.me.com", imap_port=993, imap_ssl=True,
        smtp_host="smtp.mail.me.com", smtp_port=587, smtp_tls=True,
        domains=("icloud.com", "me.com", "mac.com"),
        app_password=("iCloud exige un mot de passe d'application dédié "
                      "(appleid.apple.com → Mots de passe d'application)."),
    ),
    "yahoo": MailProvider(
        key="yahoo", label="Yahoo",
        imap_host="imap.mail.yahoo.com", imap_port=993, imap_ssl=True,
        smtp_host="smtp.mail.yahoo.com", smtp_port=587, smtp_tls=True,
        domains=("yahoo.com", "yahoo.fr", "ymail.com"),
        app_password=("Yahoo exige un mot de passe d'application (sécurité du compte Yahoo)."),
    ),
    "custom": MailProvider(
        key="custom", label="Autre (SMTP/IMAP manuel)",
        imap_host="", imap_port=993, imap_ssl=True,
        smtp_host="", smtp_port=587, smtp_tls=True,
        domains=(),
    ),
}

PROVIDER_KEYS: tuple[str, ...] = tuple(MAIL_PROVIDERS)
PROVIDER_LABELS: tuple[str, ...] = tuple(p.label for p in MAIL_PROVIDERS.values())


def detect_provider(address: str) -> str:
    """Reconnaît le fournisseur à partir d'une adresse ou d'un domaine.

    ``"vous@gmail.com"`` → ``"gmail"`` ; ``"hotmail.fr"`` → ``"outlook"``.
    Inconnu ou vide → ``"custom"``.
    """
    value = (address or "").strip().casefold()
    if not value:
        return "custom"
    domain = value.rsplit("@", 1)[1] if "@" in value else value
    for key, prov in MAIL_PROVIDERS.items():
        if key == "custom":
            continue
        if any(domain == d or domain.endswith("." + d) for d in prov.domains):
            return key
    return "custom"


def provider_for(key_or_address: str) -> MailProvider:
    """Résout un fournisseur : clé directe (« gmail ») ou adresse (« x@icloud.com »)."""
    key = (key_or_address or "").strip().casefold()
    if key in MAIL_PROVIDERS:
        return MAIL_PROVIDERS[key]
    return MAIL_PROVIDERS[detect_provider(key_or_address)]


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return value.casefold() in {"1", "true", "on", "oui", "yes"}
    return bool(value)


def mail_settings(config: dict[str, Any], connector_type: str = "") -> dict[str, Any]:
    """Normalise la config d'un connecteur en réglages IMAP + SMTP concrets.

    Accepte trois formes, toutes historiques ou nouvelles :
    - connecteur `email` (unifié) : ``provider`` + ``email``, hôtes surchargeables ;
    - connecteur `imap` (lecture seule) : ``host`` / ``port`` / ``ssl`` ;
    - connecteur `smtp` (envoi seul) : ``host`` / ``port`` / ``tls``.

    Retourne un dictionnaire stable avec les clés ``imap_*``, ``smtp_*``,
    ``username``, ``email`` et ``from_address``. Le mot de passe n'apparaît
    jamais ici : il reste dans le Secret Vault.
    """
    cfg = config or {}
    ctype = (connector_type or "").casefold()

    provider = provider_for(str(cfg.get("provider") or cfg.get("email") or "custom"))
    email_addr = str(cfg.get("email") or "").strip()

    if ctype == "imap":
        imap_host = str(cfg.get("host") or "")
        imap_port = int(cfg.get("port") or 993)
        imap_ssl = _as_bool(cfg.get("ssl"), True)
        smtp_host = smtp_port = ""
        smtp_tls = True
        username = str(cfg.get("username") or "")
        from_address = ""
        from_name = ""
    elif ctype == "smtp":
        imap_host = imap_port = ""
        imap_ssl = True
        smtp_host = str(cfg.get("host") or "")
        smtp_port = int(cfg.get("port") or 587)
        smtp_tls = _as_bool(cfg.get("tls"), True)
        username = str(cfg.get("username") or "")
        from_address = str(cfg.get("from_address") or cfg.get("username") or "")
        from_name = ""
    else:
        imap_host = str(cfg.get("imap_host") or provider.imap_host)
        imap_port = int(cfg.get("imap_port") or provider.imap_port)
        imap_ssl = _as_bool(cfg.get("imap_ssl"), provider.imap_ssl)
        smtp_host = str(cfg.get("smtp_host") or provider.smtp_host)
        smtp_port = int(cfg.get("smtp_port") or provider.smtp_port)
        smtp_tls = _as_bool(cfg.get("smtp_tls"), provider.smtp_tls)
        username = str(cfg.get("username") or email_addr)
        from_address = str(cfg.get("from_address") or email_addr)
        from_name = str(cfg.get("from_name") or "").strip()

    return {
        "provider": provider.key,
        "provider_label": provider.label,
        "app_password": provider.app_password,
        "email": email_addr,
        "username": username,
        "from_address": from_address,
        "from_name": from_name,
        "imap_host": imap_host,
        "imap_port": imap_port,
        "imap_ssl": imap_ssl,
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_tls": smtp_tls,
    }
