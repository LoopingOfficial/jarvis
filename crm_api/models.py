"""Tables `contacts` et `interactions`.

Le collationnement est `utf8mb4_unicode_ci` : les noms français sont accentués,
et la recherche doit rapprocher « Frederic » de « Frédéric » sans que l'API
n'ait à replier les accents elle-même.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    DateTime, Enum, ForeignKey, Index, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

# MySQL/InnoDB : utf8mb4 accepte les emojis, utf8 (3 octets) non.
_TABLE_ARGS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


class InteractionKind(str, enum.Enum):
    """Canal de l'échange. Enum et non texte libre : l'assistant IA écrirait
    tantôt « mail », tantôt « e-mail », et les filtres deviendraient faux."""

    call = "call"
    email = "email"
    meeting = "meeting"
    note = "note"
    task = "task"
    other = "other"


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        Index("ix_contacts_company", "company"),
        Index("ix_contacts_full_name", "full_name"),
        _TABLE_ARGS,
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    company: Mapped[str | None] = mapped_column(String(160))
    # `unique` sur l'e-mail : c'est la clé naturelle dont dispose l'assistant
    # pour éviter de recréer une fiche déjà connue. Nullable, car un contact
    # peut n'avoir qu'un téléphone — MySQL autorise plusieurs NULL sous unique.
    email: Mapped[str | None] = mapped_column(String(180), unique=True)
    phone: Mapped[str | None] = mapped_column(String(40))
    address: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str | None] = mapped_column(String(80))
    # « prospect », « client », « perdu »… laissé libre : chaque activité a son
    # vocabulaire, et figer un enum ici obligerait à migrer la table pour un mot.
    status: Mapped[str] = mapped_column(String(40), default="prospect", nullable=False)
    tags: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    interactions: Mapped[list["Interaction"]] = relationship(
        back_populates="contact",
        # Supprimer un contact sans ses interactions laisserait des lignes
        # orphelines invisibles dans l'API mais bien présentes dans la base.
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Interaction.occurred_at.desc()",
    )


class Interaction(Base):
    __tablename__ = "interactions"
    __table_args__ = (
        # L'accès dominant est « le fil d'un contact, du plus récent au plus
        # ancien » : l'index composite évite un tri en mémoire à chaque lecture.
        Index("ix_interactions_contact_date", "contact_id", "occurred_at"),
        _TABLE_ARGS,
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[InteractionKind] = mapped_column(
        Enum(InteractionKind, native_enum=False, length=20),
        default=InteractionKind.note,
        nullable=False,
    )
    subject: Mapped[str | None] = mapped_column(String(200))
    content: Mapped[str | None] = mapped_column(Text)
    # Date de l'échange, distincte de created_at : l'assistant enregistre
    # souvent après coup un appel passé la veille.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    # Qui a écrit la ligne : « jarvis » ou un humain. Sans cette colonne, on ne
    # peut plus distinguer une note dictée d'une note générée automatiquement.
    author: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    contact: Mapped[Contact] = relationship(back_populates="interactions")
