import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import BudgetPlan, CategoryMapping, ProcessedTransaction
from app.schemas import CategoryMappingOut

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=List[CategoryMappingOut])
def get_all_mappings(db: Session = Depends(get_db)):
    rows = db.execute(select(CategoryMapping)).scalars().all()
    return rows


@router.delete("/{id}", status_code=204)
def delete_mapping(id: uuid.UUID, db: Session = Depends(get_db)):
    mapping = db.get(CategoryMapping, id)
    if mapping is None:
        raise HTTPException(status_code=404, detail="Category mapping not found")
    db.delete(mapping)
    db.commit()


@router.get("/list", response_model=List[str])
def list_categories(db: Session = Depends(get_db)):
    budget_cats = db.execute(select(BudgetPlan.category).distinct()).scalars().all()
    processed_cats = (
        db.execute(select(ProcessedTransaction.category).distinct()).scalars().all()
    )
    categories = sorted(set(budget_cats) | set(processed_cats))
    return categories
