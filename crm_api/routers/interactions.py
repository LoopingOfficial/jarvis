"""Routes `/interactions` : le fil transverse, tous contacts confondus.

Utile pour « qu'ai-je fait cette semaine ? », question qu'on ne peut pas poser
au fil d'un contact unique.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import crud, schemas
from ..database import get_db
from ..models import InteractionKind

router = APIRouter(prefix="/interactions", tags=["interactions"])


@router.get("", response_model=list[schemas.InteractionOut])
def list_interactions(
    kind: InteractionKind | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[schemas.InteractionOut]:
    return crud.list_interactions(db, kind=kind, limit=limit, offset=offset)
