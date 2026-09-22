"""Shared logic for the context a category mapping carries.

A mapping is a rule: "descriptions like this one get this category, these
tags, and this split." Three call sites write that rule (POST
/category-mappings, PATCH /category-mappings/{id}, and the save_mapping flag
on POST /transactions/process) and one call site reads it back out
(POST /transactions/auto-categorise). Keeping the read and the writes in one
module is what stops those four drifting apart.

Shares are stored as (share_type, share_value) only. share_amount is money and
depends on the transaction's own total, so it is recomputed per transaction —
see _build_share_records in routers/transactions.py.
"""

import uuid
from typing import List, Optional, Sequence

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CategoryMapping, CategoryMappingShare, Person, Tag
from app.schemas import PersonShareIn


def resolve_tags(tag_ids: Sequence[uuid.UUID], user_id: uuid.UUID, db: Session):
    """Load the user's own tags, 404ing on anything they don't own."""
    tags: List[Tag] = []
    for tid in tag_ids:
        tag = db.execute(
            select(Tag).where(Tag.id == tid, Tag.user_id == user_id)
        ).scalar_one_or_none()
        if tag is None:
            raise HTTPException(status_code=404, detail=f"Tag {tid} not found")
        tags.append(tag)
    return tags


def _assert_person(person_id: uuid.UUID, user_id: uuid.UUID, db: Session) -> None:
    person = db.execute(
        select(Person).where(Person.id == person_id, Person.user_id == user_id)
    ).scalar_one_or_none()
    if person is None:
        raise HTTPException(status_code=404, detail=f"Person {person_id} not found")


def validate_mapping_context(
    db: Session,
    user_id: uuid.UUID,
    tag_ids: Optional[Sequence[uuid.UUID]] = None,
    shares: Optional[Sequence[PersonShareIn]] = None,
) -> None:
    """Raise before anything is written if the rule names a foreign entity.

    Callers that insert the mapping row first would otherwise leave a
    half-built rule behind on a 404.
    """
    if tag_ids is not None:
        resolve_tags(tag_ids, user_id, db)
    if shares is not None:
        for s in shares:
            _assert_person(s.person_id, user_id, db)


def set_mapping_context(
    db: Session,
    mapping: CategoryMapping,
    user_id: uuid.UUID,
    *,
    tag_ids: Optional[Sequence[uuid.UUID]] = None,
    shares: Optional[Sequence[PersonShareIn]] = None,
) -> None:
    """Replace a mapping's tags and/or split.

    ``None`` leaves that facet untouched; an empty list clears it. Same
    convention as PATCH /transactions/processed/{id}, so a caller that forwards
    an optional field does the right thing without special-casing.
    """
    if tag_ids is not None:
        mapping.tags = resolve_tags(tag_ids, user_id, db)

    if shares is not None:
        for s in shares:
            _assert_person(s.person_id, user_id, db)
        # delete-orphan on the relationship removes the rows the rule no
        # longer names; assigning a fresh list is enough.
        mapping.shares = [
            CategoryMappingShare(
                mapping_id=mapping.id,
                person_id=s.person_id,
                share_type=s.share_type,
                share_value=float(s.share_value),
            )
            for s in shares
        ]


def mapping_share_inputs(mapping: CategoryMapping) -> List[PersonShareIn]:
    """The mapping's split, in the shape the per-transaction builders take."""
    return [
        PersonShareIn(
            person_id=s.person_id,
            share_type=s.share_type,
            share_value=s.share_value,
        )
        for s in mapping.shares
    ]
