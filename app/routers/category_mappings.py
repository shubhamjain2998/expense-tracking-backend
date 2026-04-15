import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import CategoryMapping
from app.schemas import CategoryMappingOut

router = APIRouter(prefix="/category-mappings", tags=["category-mappings"])


@router.get("", response_model=List[CategoryMappingOut])
def list_mappings(
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    rows = (
        db.execute(select(CategoryMapping).where(CategoryMapping.user_id == user_id))
        .scalars()
        .all()
    )
    return [CategoryMappingOut.from_orm(r) for r in rows]


@router.delete("/{id}", status_code=204)
def delete_mapping(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    mapping = db.execute(
        select(CategoryMapping).where(
            CategoryMapping.id == id, CategoryMapping.user_id == user_id
        )
    ).scalar_one_or_none()
    if mapping is None:
        raise HTTPException(status_code=404, detail="Category mapping not found")
    db.delete(mapping)
    db.commit()
