"""Routes `/contacts`."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import crud, schemas
from ..database import get_db

router = APIRouter(prefix="/contacts", tags=["contacts"])


def _get_or_404(db: Session, contact_id: int):
    contact = crud.get_contact(db, contact_id)
    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contact {contact_id} introuvable.",
        )
    return contact


@router.get("", response_model=schemas.ContactList)
def list_contacts(
    q: str | None = Query(default=None, max_length=160, description="Nom, société, e-mail, téléphone ou tag"),
    status_filter: str | None = Query(default=None, alias="status", max_length=40),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> schemas.ContactList:
    total, rows = crud.search_contacts(
        db, q=q, status=status_filter, limit=limit, offset=offset
    )
    return schemas.ContactList(total=total, limit=limit, offset=offset, items=rows)


@router.post("", response_model=schemas.ContactOut, status_code=status.HTTP_201_CREATED)
def create_contact(
    payload: schemas.ContactCreate, db: Session = Depends(get_db)
) -> schemas.ContactOut:
    # L'e-mail est unique en base. On vérifie d'abord pour renvoyer un message
    # utile, mais on rattrape aussi l'IntegrityError : entre le SELECT et
    # l'INSERT, un autre client a pu créer la même fiche.
    if payload.email:
        existing = crud.get_contact_by_email(db, payload.email)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Un contact existe déjà avec cet e-mail (id {existing.id}).",
            )
    try:
        return crud.create_contact(db, payload)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Un contact existe déjà avec cet e-mail.",
        )


@router.get("/{contact_id}", response_model=schemas.ContactDetail)
def read_contact(contact_id: int, db: Session = Depends(get_db)) -> schemas.ContactDetail:
    contact = crud.get_contact_detail(db, contact_id)
    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contact {contact_id} introuvable.",
        )
    return contact


@router.patch("/{contact_id}", response_model=schemas.ContactOut)
def update_contact(
    contact_id: int, payload: schemas.ContactUpdate, db: Session = Depends(get_db)
) -> schemas.ContactOut:
    contact = _get_or_404(db, contact_id)
    try:
        return crud.update_contact(db, contact, payload)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Un autre contact porte déjà cet e-mail.",
        )


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contact(contact_id: int, db: Session = Depends(get_db)) -> Response:
    crud.delete_contact(db, _get_or_404(db, contact_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{contact_id}/interactions",
    response_model=schemas.InteractionOut,
    status_code=status.HTTP_201_CREATED,
    tags=["interactions"],
)
def add_interaction(
    contact_id: int, payload: schemas.InteractionCreate, db: Session = Depends(get_db)
) -> schemas.InteractionOut:
    _get_or_404(db, contact_id)
    return crud.create_interaction(db, contact_id, payload)


@router.get(
    "/{contact_id}/interactions",
    response_model=list[schemas.InteractionOut],
    tags=["interactions"],
)
def list_contact_interactions(
    contact_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[schemas.InteractionOut]:
    _get_or_404(db, contact_id)
    return crud.list_interactions(db, contact_id=contact_id, limit=limit, offset=offset)
