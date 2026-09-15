"""Accès aux données. Séparé des routes pour rester testable sans serveur HTTP."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from . import models, schemas


# -- contacts -------------------------------------------------------------

def get_contact(db: Session, contact_id: int) -> models.Contact | None:
    return db.get(models.Contact, contact_id)


def get_contact_detail(db: Session, contact_id: int) -> models.Contact | None:
    stmt = (
        select(models.Contact)
        .options(selectinload(models.Contact.interactions))
        .where(models.Contact.id == contact_id)
    )
    return db.execute(stmt).scalar_one_or_none()


def get_contact_by_email(db: Session, email: str) -> models.Contact | None:
    stmt = select(models.Contact).where(models.Contact.email == email)
    return db.execute(stmt).scalar_one_or_none()


def search_contacts(
    db: Session,
    *,
    q: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, list[models.Contact]]:
    stmt = select(models.Contact)
    if q:
        # `%` et `_` sont des jokers LIKE : un utilisateur cherchant « 100_% »
        # obtiendrait sinon des résultats absurdes. On les échappe.
        needle = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        # `escape` explicite : MySQL suppose le backslash par défaut, mais pas
        # SQLite ni un MySQL en NO_BACKSLASH_ESCAPES. Sans lui, l'échappement
        # ci-dessus serait ignoré sur ces moteurs.
        stmt = stmt.where(
            or_(
                models.Contact.full_name.like(needle, escape="\\"),
                models.Contact.company.like(needle, escape="\\"),
                models.Contact.email.like(needle, escape="\\"),
                models.Contact.phone.like(needle, escape="\\"),
                models.Contact.tags.like(needle, escape="\\"),
            )
        )
    if status:
        stmt = stmt.where(models.Contact.status == status)

    # Le total est calculé sur la requête filtrée mais sans pagination :
    # c'est ce qui permet à l'appelant de savoir qu'il reste des pages.
    total = db.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()

    rows = db.execute(
        stmt.order_by(models.Contact.updated_at.desc()).limit(limit).offset(offset)
    ).scalars().all()
    return total, list(rows)


def create_contact(db: Session, payload: schemas.ContactCreate) -> models.Contact:
    contact = models.Contact(**payload.model_dump(exclude_none=False))
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


def update_contact(
    db: Session, contact: models.Contact, payload: schemas.ContactUpdate
) -> models.Contact:
    # `exclude_unset` : seuls les champs réellement envoyés sont appliqués.
    # Sans lui, les valeurs par défaut du schéma écraseraient la fiche.
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(contact, field, value)
    db.commit()
    db.refresh(contact)
    return contact


def delete_contact(db: Session, contact: models.Contact) -> None:
    db.delete(contact)
    db.commit()


# -- interactions ---------------------------------------------------------

def create_interaction(
    db: Session, contact_id: int, payload: schemas.InteractionCreate
) -> models.Interaction:
    data = payload.model_dump()
    occurred = data.pop("occurred_at", None)
    if occurred is None:
        occurred = datetime.now(timezone.utc).replace(tzinfo=None)
    elif occurred.tzinfo is not None:
        # MySQL DATETIME ne porte pas de fuseau : on normalise en UTC naïf
        # plutôt que de laisser MySQL tronquer silencieusement l'offset.
        occurred = occurred.astimezone(timezone.utc).replace(tzinfo=None)

    interaction = models.Interaction(contact_id=contact_id, occurred_at=occurred, **data)
    db.add(interaction)
    db.commit()
    db.refresh(interaction)
    return interaction


def list_interactions(
    db: Session,
    *,
    contact_id: int | None = None,
    kind: models.InteractionKind | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[models.Interaction]:
    stmt = select(models.Interaction)
    if contact_id is not None:
        stmt = stmt.where(models.Interaction.contact_id == contact_id)
    if kind is not None:
        stmt = stmt.where(models.Interaction.kind == kind)
    stmt = stmt.order_by(models.Interaction.occurred_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())
