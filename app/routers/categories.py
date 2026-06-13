"""User-defined budgeting categories — list, create, rename, delete."""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, outerjoin, select
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import (
    BudgetPlan,
    Category,
    CategoryMapping,
    ProcessedTransaction,
    RawTransaction,
)
from app.schemas import CategoryCreate, CategoryOut, CategoryPatch

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=List[CategoryOut])
def list_categories(
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    """List all categories for the authenticated user.

    ``txn_count`` is the number of processed transactions that reference each
    category.  It is computed with a single LEFT OUTER JOIN + GROUP BY so the
    full list is fetched in one round-trip regardless of how many categories
    the user has.
    """
    rows = db.execute(
        select(
            Category,
            func.count(ProcessedTransaction.id).label("txn_count"),
        )
        .select_from(
            outerjoin(
                Category,
                ProcessedTransaction,
                (ProcessedTransaction.category_id == Category.id)
                & (ProcessedTransaction.user_id == user_id),
            )
        )
        .where(Category.user_id == user_id)
        .group_by(Category.id)
        .order_by(Category.name)
    ).all()

    result = []
    for category, txn_count in rows:
        out = CategoryOut.model_validate(category)
        out.txn_count = txn_count
        result.append(out)
    return result


@router.post("", response_model=CategoryOut, status_code=201)
def create_category(
    body: CategoryCreate,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    name = body.name.strip().casefold()
    existing = db.execute(
        select(Category).where(Category.user_id == user_id, Category.name == name)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Category '{name}' already exists")
    category = Category(user_id=user_id, name=name, is_income=body.is_income)
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.patch("/{id}", response_model=CategoryOut)
def patch_category(
    id: uuid.UUID,
    body: CategoryPatch,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    category = db.execute(
        select(Category).where(Category.id == id, Category.user_id == user_id)
    ).scalar_one_or_none()
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    if body.name is not None:
        name = body.name.strip().casefold()
        clash = db.execute(
            select(Category).where(Category.user_id == user_id, Category.name == name)
        ).scalar_one_or_none()
        if clash and clash.id != id:
            raise HTTPException(
                status_code=409, detail=f"Category '{name}' already exists"
            )
        category.name = name
    if body.is_income is not None:
        category.is_income = body.is_income
    db.commit()
    db.refresh(category)
    return category


@router.delete("/{id}", status_code=204)
def delete_category(
    id: uuid.UUID,
    action: str = Query(default="block"),  # block | pending | move
    target_category_id: Optional[uuid.UUID] = Query(default=None),
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    """Delete a category.

    action=block (default): fail with 409 if referenced anywhere.
    action=pending: move processed txns back to raw/pending, delete mappings.
    action=move + target_category_id: reassign txns + mappings to target.

    Budget plan entries are always deleted regardless of action.
    """
    category = db.execute(
        select(Category).where(Category.id == id, Category.user_id == user_id)
    ).scalar_one_or_none()
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")

    if action == "block":
        in_use = (
            db.execute(
                select(ProcessedTransaction)
                .where(
                    ProcessedTransaction.category_id == id,
                    ProcessedTransaction.user_id == user_id,
                )
                .limit(1)
            ).scalar_one_or_none()
            or db.execute(
                select(BudgetPlan)
                .where(BudgetPlan.category_id == id, BudgetPlan.user_id == user_id)
                .limit(1)
            ).scalar_one_or_none()
            or db.execute(
                select(CategoryMapping)
                .where(
                    CategoryMapping.category_id == id,
                    CategoryMapping.user_id == user_id,
                )
                .limit(1)
            ).scalar_one_or_none()
        )
        if in_use:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot delete category: it is referenced by transactions, "
                    "budget entries, or category mappings"
                ),
            )

    elif action == "pending":
        # Collect raw_txn_ids before deleting processed transactions
        raw_txn_ids = (
            db.execute(
                select(ProcessedTransaction.raw_txn_id).where(
                    ProcessedTransaction.category_id == id,
                    ProcessedTransaction.user_id == user_id,
                )
            )
            .scalars()
            .all()
        )

        if raw_txn_ids:
            # Delete processed transactions; DB-level CASCADE removes shares + tag links
            db.execute(
                sa_delete(ProcessedTransaction)
                .where(
                    ProcessedTransaction.category_id == id,
                    ProcessedTransaction.user_id == user_id,
                )
                .execution_options(synchronize_session=False)
            )
            # Reset raw transactions to pending so they re-appear in the queue
            db.execute(
                sa_update(RawTransaction)
                .where(
                    RawTransaction.id.in_(raw_txn_ids),
                    RawTransaction.user_id == user_id,
                )
                .values(status="pending")
                .execution_options(synchronize_session=False)
            )

        # Delete all mappings for this category
        db.execute(
            sa_delete(CategoryMapping)
            .where(
                CategoryMapping.category_id == id, CategoryMapping.user_id == user_id
            )
            .execution_options(synchronize_session=False)
        )

    elif action == "move":
        if target_category_id is None:
            raise HTTPException(
                status_code=422, detail="target_category_id is required for action=move"
            )
        if target_category_id == id:
            raise HTTPException(
                status_code=422,
                detail="target_category_id must differ from the deleted category",
            )
        target = db.execute(
            select(Category).where(
                Category.id == target_category_id, Category.user_id == user_id
            )
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=404, detail="Target category not found")

        # Reassign all processed transactions
        db.execute(
            sa_update(ProcessedTransaction)
            .where(
                ProcessedTransaction.category_id == id,
                ProcessedTransaction.user_id == user_id,
            )
            .values(category_id=target_category_id)
            .execution_options(synchronize_session=False)
        )

        # Move mappings; skip if target already has a mapping with the same pattern
        source_mappings = (
            db.execute(
                select(CategoryMapping).where(
                    CategoryMapping.category_id == id,
                    CategoryMapping.user_id == user_id,
                )
            )
            .scalars()
            .all()
        )

        for mapping in source_mappings:
            conflict = db.execute(
                select(CategoryMapping).where(
                    CategoryMapping.category_id == target_category_id,
                    CategoryMapping.user_id == user_id,
                    CategoryMapping.description_pattern == mapping.description_pattern,
                )
            ).scalar_one_or_none()
            if conflict:
                db.delete(mapping)
            else:
                mapping.category_id = target_category_id

    else:
        raise HTTPException(status_code=422, detail=f"Unknown action '{action}'")

    # Always delete budget plans (cannot meaningfully merge them)
    db.execute(
        sa_delete(BudgetPlan)
        .where(BudgetPlan.category_id == id, BudgetPlan.user_id == user_id)
        .execution_options(synchronize_session=False)
    )

    db.delete(category)
    db.commit()
