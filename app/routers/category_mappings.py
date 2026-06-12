"""Learned description-pattern → category rules used by auto-categorise."""

import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Category, CategoryMapping
from app.schemas import CategoryMappingCreate, CategoryMappingOut

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


@router.post("", response_model=CategoryMappingOut, status_code=201)
def create_mapping(
    body: CategoryMappingCreate,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    """Create a new description-pattern → category mapping for the authenticated user.

    Duplicate-pattern policy: consistent with the "Save as rule" path in
    POST /transactions/process, which *upserts* when the same pattern already
    exists (updates category_id + last_used, keeps match_count).  We do the
    same here: a 409 would frustrate users who tweak a rule's category; an
    upsert silently reassigns it.  The response returns the (possibly updated)
    mapping with 201 in both cases so callers need only check for errors.
    """
    cat = db.execute(
        select(Category).where(
            Category.id == body.category_id, Category.user_id == user_id
        )
    ).scalar_one_or_none()
    if cat is None:
        raise HTTPException(
            status_code=404, detail=f"Category {body.category_id} not found"
        )

    pattern = body.description_pattern.strip()
    existing = db.execute(
        select(CategoryMapping).where(
            CategoryMapping.description_pattern == pattern,
            CategoryMapping.user_id == user_id,
        )
    ).scalar_one_or_none()

    if existing:
        existing.category_id = body.category_id
        existing.last_used = datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing)
        return CategoryMappingOut.from_orm(existing)

    mapping = CategoryMapping(
        user_id=user_id,
        description_pattern=pattern,
        category_id=body.category_id,
        match_count=0,
        last_used=datetime.now(timezone.utc),
    )
    db.add(mapping)
    db.commit()
    db.refresh(mapping)
    return CategoryMappingOut.from_orm(mapping)


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
