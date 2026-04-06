from fastapi import APIRouter, Depends
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    BudgetPlan,
    CategoryMapping,
    Person,
    ProcessedTransaction,
    RawTransaction,
    transaction_persons,
)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.delete("/transactions/raw", status_code=204)
def delete_all_raw_transactions(db: Session = Depends(get_db)):
    db.execute(delete(RawTransaction))
    db.commit()


@router.delete("/transactions/processed", status_code=204)
def delete_all_processed_transactions(db: Session = Depends(get_db)):
    db.execute(delete(transaction_persons))
    db.execute(delete(ProcessedTransaction))
    db.execute(delete(RawTransaction))
    db.commit()


@router.delete("/categories", status_code=204)
def delete_all_category_mappings(db: Session = Depends(get_db)):
    db.execute(delete(CategoryMapping))
    db.commit()


@router.delete("/budget", status_code=204)
def delete_all_budget_plans(db: Session = Depends(get_db)):
    db.execute(delete(BudgetPlan))
    db.commit()


@router.delete("/persons", status_code=204)
def delete_all_persons(db: Session = Depends(get_db)):
    db.execute(delete(transaction_persons))
    db.execute(delete(Person))
    db.commit()


@router.delete("/all", status_code=204)
def delete_everything(db: Session = Depends(get_db)):
    db.execute(delete(transaction_persons))
    db.execute(delete(ProcessedTransaction))
    db.execute(delete(RawTransaction))
    db.execute(delete(CategoryMapping))
    db.execute(delete(BudgetPlan))
    db.execute(delete(Person))
    db.commit()
