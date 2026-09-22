"""Learned description-pattern → category rules used by auto-categorise.

A rule carries three things, not one: the category, the tags, and the split.
Auto-categorise applies all three, so editing a rule here changes what every
future matching transaction looks like. See services/mapping_rules.py.
"""

import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import get_current_user
from app.database import get_db
from app.models import Category, CategoryMapping
from app.schemas import CategoryMappingCreate, CategoryMappingOut, CategoryMappingPatch
from app.services.mapping_rules import set_mapping_context, validate_mapping_context

router = APIRouter(prefix="/category-mappings", tags=["category-mappings"])


@router.get("", response_model=List[CategoryMappingOut])
def list_mappings(
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    rows = (
        db.execute(
            select(CategoryMapping)
            .where(CategoryMapping.user_id == user_id)
            .options(
                selectinload(CategoryMapping.tags),
                selectinload(CategoryMapping.shares),
                selectinload(CategoryMapping.category),
            )
        )
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

    # Validate the rule's tags and split before writing anything, so a 404
    # never leaves a category-only mapping behind.
    validate_mapping_context(db, user_id, tag_ids=body.tag_ids, shares=body.shares)

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
        set_mapping_context(
            db, existing, user_id, tag_ids=body.tag_ids, shares=body.shares
        )
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
    db.flush()  # mapping.id, needed by the share rows
    set_mapping_context(db, mapping, user_id, tag_ids=body.tag_ids, shares=body.shares)
    db.commit()
    db.refresh(mapping)
    return CategoryMappingOut.from_orm(mapping)


@router.patch("/{id}", response_model=CategoryMappingOut)
def update_mapping(
    id: uuid.UUID,
    body: CategoryMappingPatch,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    """Edit the pattern and/or category of an existing mapping.

    Returns 404 for a mapping that does not exist or belongs to another user.
    Returns 404 if the supplied category_id does not exist or belongs to
    another user.  An empty body is a valid no-op that returns the mapping
    unchanged.
    """
    mapping = db.execute(
        select(CategoryMapping).where(
            CategoryMapping.id == id, CategoryMapping.user_id == user_id
        )
    ).scalar_one_or_none()
    if mapping is None:
        raise HTTPException(status_code=404, detail="Category mapping not found")

    if body.category_id is not None:
        cat = db.execute(
            select(Category).where(
                Category.id == body.category_id, Category.user_id == user_id
            )
        ).scalar_one_or_none()
        if cat is None:
            raise HTTPException(
                status_code=404, detail=f"Category {body.category_id} not found"
            )
        mapping.category_id = body.category_id

    if body.description_pattern is not None:
        mapping.description_pattern = body.description_pattern.strip()

    set_mapping_context(db, mapping, user_id, tag_ids=body.tag_ids, shares=body.shares)

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
