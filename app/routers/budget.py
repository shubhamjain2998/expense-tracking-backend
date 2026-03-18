import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import BudgetPlan
from app.schemas import BudgetPlanCreate, BudgetPlanOut, BudgetPlanUpdate

router = APIRouter(prefix="/budget", tags=["budget"])


@router.post("", response_model=List[BudgetPlanOut], status_code=201)
def create_budget(payload: BudgetPlanCreate, db: Session = Depends(get_db)):
    # Check for duplicate categories within the request itself
    categories = [e.category for e in payload.entries]
    if len(categories) != len(set(categories)):
        raise HTTPException(status_code=422, detail="Duplicate categories in request")

    # Check for conflicts with existing rows for this year
    existing = (
        db.query(BudgetPlan.category).filter(BudgetPlan.year == payload.year).all()
    )
    existing_categories = {row.category for row in existing}
    conflicts = [c for c in categories if c in existing_categories]
    if conflicts:
        raise HTTPException(
            status_code=409,
            detail=f"Categories already exist for {payload.year}: {conflicts}",
        )

    rows = [
        BudgetPlan(
            year=payload.year, category=e.category, allocated_amount=e.allocated_amount
        )
        for e in payload.entries
    ]
    db.add_all(rows)
    db.commit()
    for row in rows:
        db.refresh(row)
    return rows


@router.get("/{year}", response_model=List[BudgetPlanOut])
def get_budget(year: int, db: Session = Depends(get_db)):
    rows = db.query(BudgetPlan).filter(BudgetPlan.year == year).all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No budget found for year {year}")
    return rows


@router.put("/{id}", response_model=BudgetPlanOut)
def update_budget(
    id: uuid.UUID, payload: BudgetPlanUpdate, db: Session = Depends(get_db)
):
    row = db.get(BudgetPlan, id)
    if not row:
        raise HTTPException(status_code=404, detail="Budget entry not found")

    if payload.category is not None:
        # Ensure the new category name doesn't clash with another row in the same year
        clash = (
            db.query(BudgetPlan)
            .filter(
                BudgetPlan.year == row.year, BudgetPlan.category == payload.category
            )
            .first()
        )
        if clash and clash.id != row.id:
            raise HTTPException(
                status_code=409,
                detail=f"Category '{payload.category}' already exists for {row.year}",
            )
        row.category = payload.category

    if payload.allocated_amount is not None:
        row.allocated_amount = payload.allocated_amount

    db.commit()
    db.refresh(row)
    return row


@router.delete("/{id}", status_code=204)
def delete_budget(id: uuid.UUID, db: Session = Depends(get_db)):
    row = db.get(BudgetPlan, id)
    if not row:
        raise HTTPException(status_code=404, detail="Budget entry not found")
    db.delete(row)
    db.commit()
