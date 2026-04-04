from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import BudgetPlan, Person, ProcessedTransaction, transaction_persons
from app.schemas import MonthlyTrendRow, SplitLedgerRow, SummaryRow, YTDRow

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# ─── /summary ─────────────────────────────────────────────────────────────────


@router.get("/summary", response_model=List[SummaryRow])
def summary(year: int, month: int, db: Session = Depends(get_db)):
    # Budget rows for the year (annual amount → monthly = /12)
    budget_rows = db.execute(
        select(BudgetPlan.category, BudgetPlan.allocated_amount).where(
            BudgetPlan.year == year
        )
    ).all()
    budget_map = {
        row.category: Decimal(str(row.allocated_amount)) / 12 for row in budget_rows
    }

    # Actual spend for the month
    actual_rows = db.execute(
        select(
            ProcessedTransaction.category,
            func.sum(ProcessedTransaction.effective_amount).label("actual"),
        )
        .where(ProcessedTransaction.year == year, ProcessedTransaction.month == month)
        .group_by(ProcessedTransaction.category)
    ).all()
    actual_map = {row.category: Decimal(str(row.actual)) for row in actual_rows}

    all_categories = set(budget_map) | set(actual_map)
    result = []
    for cat in sorted(all_categories):
        allocated = budget_map.get(cat, Decimal("0"))
        actual = actual_map.get(cat, Decimal("0"))
        variance = allocated - actual
        pct_used = float(actual / allocated * 100) if allocated else None
        result.append(
            SummaryRow(
                category=cat,
                allocated_monthly=allocated,
                actual=actual,
                variance=variance,
                pct_used=pct_used,
            )
        )
    return result


# ─── /monthly-trend ───────────────────────────────────────────────────────────


@router.get("/monthly-trend", response_model=List[MonthlyTrendRow])
def monthly_trend(
    year: int,
    category: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = (
        select(
            ProcessedTransaction.month,
            func.sum(ProcessedTransaction.effective_amount).label("actual_amount"),
        )
        .where(ProcessedTransaction.year == year)
        .group_by(ProcessedTransaction.month)
        .order_by(ProcessedTransaction.month)
    )
    if category is not None:
        query = query.where(ProcessedTransaction.category == category)

    rows = db.execute(query).all()
    return [
        MonthlyTrendRow(month=row.month, actual_amount=Decimal(str(row.actual_amount)))
        for row in rows
    ]


# ─── /split-ledger ────────────────────────────────────────────────────────────


@router.get("/split-ledger", response_model=List[SplitLedgerRow])
def split_ledger(month: int, year: int, db: Session = Depends(get_db)):
    rows = db.execute(
        select(
            Person.name.label("person_name"),
            func.sum(ProcessedTransaction.effective_amount).label("total_split_amount"),
        )
        .join(transaction_persons, transaction_persons.c.person_id == Person.id)
        .join(
            ProcessedTransaction,
            ProcessedTransaction.id == transaction_persons.c.processed_txn_id,
        )
        .where(ProcessedTransaction.year == year, ProcessedTransaction.month == month)
        .group_by(Person.name)
        .order_by(Person.name)
    ).all()

    return [
        SplitLedgerRow(
            person_name=row.person_name,
            total_split_amount=Decimal(str(row.total_split_amount)),
        )
        for row in rows
    ]


# ─── /ytd ─────────────────────────────────────────────────────────────────────


@router.get("/ytd", response_model=List[YTDRow])
def ytd(year: int, db: Session = Depends(get_db)):
    budget_rows = db.execute(
        select(BudgetPlan.category, BudgetPlan.allocated_amount).where(
            BudgetPlan.year == year
        )
    ).all()
    budget_map = {
        row.category: Decimal(str(row.allocated_amount)) for row in budget_rows
    }

    actual_rows = db.execute(
        select(
            ProcessedTransaction.category,
            func.sum(ProcessedTransaction.effective_amount).label("actual"),
        )
        .where(ProcessedTransaction.year == year)
        .group_by(ProcessedTransaction.category)
    ).all()
    actual_map = {row.category: Decimal(str(row.actual)) for row in actual_rows}

    all_categories = set(budget_map) | set(actual_map)
    result = []
    for cat in sorted(all_categories):
        allocated = budget_map.get(cat, Decimal("0"))
        actual = actual_map.get(cat, Decimal("0"))
        variance = allocated - actual
        pct_used = float(actual / allocated * 100) if allocated else None
        result.append(
            YTDRow(
                category=cat,
                allocated_ytd=allocated,
                actual_ytd=actual,
                variance=variance,
                pct_used=pct_used,
            )
        )
    return result
