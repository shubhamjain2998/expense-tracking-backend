import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import BudgetPlan, Category
from app.schemas import BudgetPlanCreate, BudgetPlanOut, BudgetPlanUpdate

router = APIRouter(prefix="/budget", tags=["budget"])


@router.post("", response_model=List[BudgetPlanOut], status_code=201)
def create_budget(
    payload: BudgetPlanCreate,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    category_ids = [e.category_id for e in payload.entries]

    if len(category_ids) != len(set(category_ids)):
        raise HTTPException(status_code=422, detail="Duplicate categories in request")

    # Validate all categories exist and belong to this user
    for cid in category_ids:
        cat = db.execute(
            select(Category).where(Category.id == cid, Category.user_id == user_id)
        ).scalar_one_or_none()
        if cat is None:
            raise HTTPException(status_code=404, detail=f"Category {cid} not found")

    # Check for conflicts with existing rows for this year
    existing_ids = {
        row.category_id
        for row in db.execute(
            select(BudgetPlan.category_id).where(
                BudgetPlan.year == payload.year, BudgetPlan.user_id == user_id
            )
        ).all()
    }
    conflicts = [str(cid) for cid in category_ids if cid in existing_ids]
    if conflicts:
        raise HTTPException(
            status_code=409,
            detail=f"Budget entries already exist for year {payload.year}: {conflicts}",
        )

    rows = [
        BudgetPlan(
            user_id=user_id,
            year=payload.year,
            category_id=e.category_id,
            allocated_amount=e.allocated_amount,
        )
        for e in payload.entries
    ]
    db.add_all(rows)
    db.commit()
    for row in rows:
        db.refresh(row)
    return [BudgetPlanOut.from_orm(r) for r in rows]


@router.get("/{year}", response_model=List[BudgetPlanOut])
def get_budget(
    year: int,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    rows = (
        db.execute(
            select(BudgetPlan).where(
                BudgetPlan.year == year, BudgetPlan.user_id == user_id
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"No budget found for year {year}")
    return [BudgetPlanOut.from_orm(r) for r in rows]


@router.put("/{id}", response_model=BudgetPlanOut)
def update_budget(
    id: uuid.UUID,
    payload: BudgetPlanUpdate,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    row = db.execute(
        select(BudgetPlan).where(BudgetPlan.id == id, BudgetPlan.user_id == user_id)
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Budget entry not found")

    if payload.category_id is not None:
        cat = db.execute(
            select(Category).where(
                Category.id == payload.category_id, Category.user_id == user_id
            )
        ).scalar_one_or_none()
        if cat is None:
            raise HTTPException(
                status_code=404, detail=f"Category {payload.category_id} not found"
            )
        # Ensure no other budget entry for this year already uses the new category
        clash = db.execute(
            select(BudgetPlan).where(
                BudgetPlan.year == row.year,
                BudgetPlan.category_id == payload.category_id,
                BudgetPlan.user_id == user_id,
            )
        ).scalar_one_or_none()
        if clash and clash.id != row.id:
            raise HTTPException(
                status_code=409,
                detail=f"A budget entry for this category already exists in {row.year}",
            )
        row.category_id = payload.category_id

    if payload.allocated_amount is not None:
        row.allocated_amount = payload.allocated_amount

    db.commit()
    db.refresh(row)
    return BudgetPlanOut.from_orm(row)


@router.delete("/{id}", status_code=204)
def delete_budget(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    row = db.execute(
        select(BudgetPlan).where(BudgetPlan.id == id, BudgetPlan.user_id == user_id)
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Budget entry not found")
    db.delete(row)
    db.commit()
