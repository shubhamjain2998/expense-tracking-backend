import uuid
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import (
    BudgetPlan,
    Category,
    Person,
    ProcessedTransaction,
    TransactionPersonShare,
    transaction_tags,
)
from app.schemas import MonthlyTrendRow, SplitLedgerRow, SummaryRow, YTDRow

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# ─── /summary ─────────────────────────────────────────────────────────────────


@router.get("/summary", response_model=List[SummaryRow])
def summary(
    year: int,
    month: int,
    tag_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    budget_rows = db.execute(
        select(Category.name, BudgetPlan.allocated_amount)
        .join(Category, Category.id == BudgetPlan.category_id)
        .where(BudgetPlan.year == year, BudgetPlan.user_id == user_id)
    ).all()
    budget_map = {
        row.name: Decimal(str(row.allocated_amount)) / 12 for row in budget_rows
    }

    actual_query = (
        select(
            Category.name,
            func.sum(ProcessedTransaction.effective_amount).label("actual"),
        )
        .join(Category, Category.id == ProcessedTransaction.category_id)
        .where(
            ProcessedTransaction.year == year,
            ProcessedTransaction.month == month,
            ProcessedTransaction.user_id == user_id,
        )
        .group_by(Category.name)
    )
    if tag_id is not None:
        actual_query = actual_query.where(
            ProcessedTransaction.id.in_(
                select(transaction_tags.c.processed_txn_id).where(
                    transaction_tags.c.tag_id == tag_id
                )
            )
        )
    actual_rows = db.execute(actual_query).all()
    actual_map = {row.name: Decimal(str(row.actual)) for row in actual_rows}

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
    category_id: Optional[str] = None,
    tag_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    query = (
        select(
            ProcessedTransaction.month,
            func.sum(ProcessedTransaction.effective_amount).label("actual_amount"),
        )
        .where(
            ProcessedTransaction.year == year,
            ProcessedTransaction.user_id == user_id,
        )
        .group_by(ProcessedTransaction.month)
        .order_by(ProcessedTransaction.month)
    )
    if category_id is not None:
        query = query.where(ProcessedTransaction.category_id == category_id)
    if tag_id is not None:
        query = query.where(
            ProcessedTransaction.id.in_(
                select(transaction_tags.c.processed_txn_id).where(
                    transaction_tags.c.tag_id == tag_id
                )
            )
        )

    rows = db.execute(query).all()
    return [
        MonthlyTrendRow(month=row.month, actual_amount=Decimal(str(row.actual_amount)))
        for row in rows
    ]


# ─── /split-ledger ────────────────────────────────────────────────────────────


@router.get("/split-ledger", response_model=List[SplitLedgerRow])
def split_ledger(
    month: int,
    year: int,
    include_settled: bool = False,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    query = (
        select(
            Person.name.label("person_name"),
            func.sum(TransactionPersonShare.share_amount).label("total_split_amount"),
        )
        .join(TransactionPersonShare, TransactionPersonShare.person_id == Person.id)
        .join(
            ProcessedTransaction,
            ProcessedTransaction.id == TransactionPersonShare.processed_txn_id,
        )
        .where(
            ProcessedTransaction.year == year,
            ProcessedTransaction.month == month,
            ProcessedTransaction.user_id == user_id,
        )
        .group_by(Person.name)
        .order_by(Person.name)
    )
    if not include_settled:
        query = query.where(TransactionPersonShare.settled.is_(False))
    rows = db.execute(query).all()

    return [
        SplitLedgerRow(
            person_name=row.person_name,
            total_split_amount=Decimal(str(row.total_split_amount)),
        )
        for row in rows
    ]


# ─── /ytd ─────────────────────────────────────────────────────────────────────


@router.get("/ytd", response_model=List[YTDRow])
def ytd(
    year: int,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    budget_rows = db.execute(
        select(Category.name, BudgetPlan.allocated_amount)
        .join(Category, Category.id == BudgetPlan.category_id)
        .where(BudgetPlan.year == year, BudgetPlan.user_id == user_id)
    ).all()
    budget_map = {row.name: Decimal(str(row.allocated_amount)) for row in budget_rows}

    actual_rows = db.execute(
        select(
            Category.name,
            func.sum(ProcessedTransaction.effective_amount).label("actual"),
        )
        .join(Category, Category.id == ProcessedTransaction.category_id)
        .where(
            ProcessedTransaction.year == year,
            ProcessedTransaction.user_id == user_id,
        )
        .group_by(Category.name)
    ).all()
    actual_map = {row.name: Decimal(str(row.actual)) for row in actual_rows}

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
