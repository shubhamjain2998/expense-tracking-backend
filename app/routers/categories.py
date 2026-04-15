import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import BudgetPlan, Category, CategoryMapping, ProcessedTransaction
from app.schemas import CategoryCreate, CategoryOut, CategoryRename

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=List[CategoryOut])
def list_categories(db: Session = Depends(get_db)):
    return db.execute(select(Category).order_by(Category.name)).scalars().all()


@router.post("", response_model=CategoryOut, status_code=201)
def create_category(body: CategoryCreate, db: Session = Depends(get_db)):
    name = body.name.strip().lower()
    existing = db.execute(
        select(Category).where(Category.name == name)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Category '{name}' already exists")
    category = Category(name=name)
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.patch("/{id}", response_model=CategoryOut)
def rename_category(id: uuid.UUID, body: CategoryRename, db: Session = Depends(get_db)):
    category = db.get(Category, id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    name = body.name.strip().lower()
    clash = db.execute(
        select(Category).where(Category.name == name)
    ).scalar_one_or_none()
    if clash and clash.id != id:
        raise HTTPException(status_code=409, detail=f"Category '{name}' already exists")
    category.name = name
    db.commit()
    db.refresh(category)
    return category


@router.delete("/{id}", status_code=204)
def delete_category(id: uuid.UUID, db: Session = Depends(get_db)):
    category = db.get(Category, id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")

    in_use = (
        db.execute(
            select(ProcessedTransaction)
            .where(ProcessedTransaction.category_id == id)
            .limit(1)
        ).scalar_one_or_none()
        or db.execute(
            select(BudgetPlan).where(BudgetPlan.category_id == id).limit(1)
        ).scalar_one_or_none()
        or db.execute(
            select(CategoryMapping).where(CategoryMapping.category_id == id).limit(1)
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
