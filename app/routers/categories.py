"""User-defined budgeting categories — list, create, rename, delete."""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, outerjoin, select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import BudgetPlan, Category, CategoryMapping, ProcessedTransaction
from app.schemas import CategoryCreate, CategoryOut, CategoryRename

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
    category = Category(user_id=user_id, name=name)
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.patch("/{id}", response_model=CategoryOut)
def rename_category(
    id: uuid.UUID,
    body: CategoryRename,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    category = db.execute(
        select(Category).where(Category.id == id, Category.user_id == user_id)
    ).scalar_one_or_none()
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    name = body.name.strip().casefold()
    clash = db.execute(
        select(Category).where(Category.user_id == user_id, Category.name == name)
    ).scalar_one_or_none()
    if clash and clash.id != id:
        raise HTTPException(status_code=409, detail=f"Category '{name}' already exists")
    category.name = name
    db.commit()
    db.refresh(category)
    return category


@router.delete("/{id}", status_code=204)
def delete_category(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    category = db.execute(
        select(Category).where(Category.id == id, Category.user_id == user_id)
    ).scalar_one_or_none()
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")

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

    db.delete(category)
    db.commit()
