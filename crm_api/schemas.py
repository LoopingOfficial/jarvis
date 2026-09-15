"""Schémas Pydantic : la frontière entre l'assistant IA et la base.

Un modèle de langage produit volontiers des chaînes vides, des « null » textuels
ou des champs inventés. Les schémas coupent court : champs inconnus refusés,
chaînes vides ramenées à None, longueurs bornées comme en base pour que l'erreur
remonte en 422 lisible plutôt qu'en « Data too long for column » côté MySQL.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .models import InteractionKind

Str40 = Annotated[str, Field(max_length=40)]
Str80 = Annotated[str, Field(max_length=80)]


def _blank_to_none(v):
    if isinstance(v, str):
        v = v.strip()
        # Un LLM écrit parfois littéralement « null » ou « N/A » dans un champ.
        return None if v.lower() in {"", "null", "none", "n/a"} else v
    return v


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ContactCreate(_Base):
    full_name: str = Field(min_length=1, max_length=160)
    company: str | None = Field(default=None, max_length=160)
    email: EmailStr | None = None
    phone: Str40 | None = None
    address: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=100)
    country: Str80 | None = None
    status: Str40 = "prospect"
    tags: str | None = Field(default=None, max_length=255)
    notes: str | None = None

    _clean = field_validator(
        "company", "email", "phone", "address", "city", "country", "tags", "notes",
        mode="before",
    )(_blank_to_none)


class ContactUpdate(_Base):
    """Mise à jour partielle : seuls les champs fournis sont écrits.

    Un champ absent laisse la valeur en base intacte ; passer explicitement
    `null` l'efface. Sans cette distinction, un assistant qui ne renvoie que le
    téléphone effacerait tout le reste de la fiche.
    """

    full_name: str | None = Field(default=None, min_length=1, max_length=160)
    company: str | None = Field(default=None, max_length=160)
    email: EmailStr | None = None
    phone: Str40 | None = None
    address: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=100)
    country: Str80 | None = None
    status: Str40 | None = None
    tags: str | None = Field(default=None, max_length=255)
    notes: str | None = None


class InteractionCreate(_Base):
    kind: InteractionKind = InteractionKind.note
    subject: str | None = Field(default=None, max_length=200)
    content: str | None = None
    occurred_at: datetime | None = None
    author: Str80 | None = "jarvis"

    _clean = field_validator("subject", "content", "author", mode="before")(_blank_to_none)


class InteractionOut(_Base):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contact_id: int
    kind: InteractionKind
    subject: str | None
    content: str | None
    occurred_at: datetime
    author: str | None
    created_at: datetime


class ContactOut(_Base):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    company: str | None
    email: str | None
    phone: str | None
    address: str | None
    city: str | None
    country: str | None
    status: str
    tags: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class ContactDetail(ContactOut):
    interactions: list[InteractionOut] = []


class ContactList(_Base):
    """Réponse paginée. Le total est renvoyé pour que l'appelant sache s'il
    manque des résultats — un assistant qui voit 20 lignes sans savoir qu'il y
    en a 200 conclura à tort que le client n'existe pas."""

    total: int
    limit: int
    offset: int
    items: list[ContactOut]
