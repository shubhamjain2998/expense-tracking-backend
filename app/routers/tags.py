import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Tag, transaction_tags
from app.schemas import TagCreate, TagOut

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("", response_model=List[TagOut])
def list_tags(
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    return (
        db.execute(select(Tag).where(Tag.user_id == user_id).order_by(Tag.name))
        .scalars()
        .all()
    )


@router.post("", response_model=TagOut, status_code=201)
def create_tag(
    body: TagCreate,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    name = body.name.strip().casefold()
    existing = db.execute(
        select(Tag).where(Tag.user_id == user_id, Tag.name == name)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Tag '{name}' already exists")
    tag = Tag(user_id=user_id, name=name)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


@router.delete("/{id}", status_code=204)
def delete_tag(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    tag = db.execute(
        select(Tag).where(Tag.id == id, Tag.user_id == user_id)
    ).scalar_one_or_none()
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    # Detach from transactions first (junction rows), then delete the tag
    db.execute(transaction_tags.delete().where(transaction_tags.c.tag_id == id))
    db.delete(tag)
    db.commit()
