"""Analytics endpoints — budget vs. actual, trends, and split ledger.

All endpoints accept ``period_mode=calendar|fy``; see ``services/period.py``
for the conversion from calendar to Indian financial year.
"""

import uuid
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
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
from app.schemas import (
    MonthlyTrendRow,
    MultiMonthCategoryRow,
    MultiMonthSummaryItem,
    SplitLedgerRow,
    SummaryRow,
    YTDRow,
)
from app.services.period import (
    PeriodMode,
    period_year_range,
    resolve_period_month,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# ─── /summary ────────────────────────────────────────────────────────────────


@router.get("/summary", response_model=List[SummaryRow])
def summary(
    year: int,
    month: int,
    tag_id: Optional[uuid.UUID] = None,
    period_mode: PeriodMode = Query("calendar"),
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    cal_year, cal_month = resolve_period_month(year, month, period_mode)

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
            ProcessedTransaction.year == cal_year,
            ProcessedTransaction.month == cal_month,
            ProcessedTransaction.user_id == user_id,
            ProcessedTransaction.txn_type.in_(["expense", "refund"]),
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


# ─── /monthly-trend ──────────────────────────────────────────────────────────


@router.get("/monthly-trend", response_model=List[MonthlyTrendRow])
def monthly_trend(
    year: int,
    category_id: Optional[str] = None,
    tag_id: Optional[uuid.UUID] = None,
    period_mode: PeriodMode = Query("calendar"),
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    start, end = period_year_range(year, period_mode)

    query = (
        select(
            ProcessedTransaction.year,
            ProcessedTransaction.month,
            func.sum(
                case(
                    (
                        ProcessedTransaction.txn_type.in_(["expense", "refund"]),
                        ProcessedTransaction.effective_amount,
                    ),
                    else_=0,
                )
            ).label("actual_amount"),
            func.sum(
                case(
                    (
                        ProcessedTransaction.txn_type == "income",
                        -ProcessedTransaction.effective_amount,
                    ),
                    else_=0,
                )
            ).label("income_amount"),
            func.count(ProcessedTransaction.id).label("txn_count"),
        )
        .where(
            ProcessedTransaction.txn_date >= start,
            ProcessedTransaction.txn_date <= end,
            ProcessedTransaction.user_id == user_id,
        )
        .group_by(ProcessedTransaction.year, ProcessedTransaction.month)
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
    # Always return the raw calendar month (1=Jan … 12=Dec).
    # period_mode controls which months are INCLUDED (Apr-Mar vs Jan-Dec),
    # not how they are labelled. The DB stores ProcessedTransaction.month as
    # a calendar month, so we return it unchanged.
    result = [
        MonthlyTrendRow(
            month=row.month,
            actual_amount=Decimal(str(row.actual_amount or 0)),
            income_amount=Decimal(str(row.income_amount or 0)),
            txn_count=row.txn_count,
        )
        for row in rows
    ]
    result.sort(key=lambda r: r.month)
    return result


# ─── /multi-month-summary ────────────────────────────────────────────────────


@router.get("/multi-month-summary", response_model=List[MultiMonthSummaryItem])
def multi_month_summary(
    end_year: int,
    end_month: int = Query(..., ge=1, le=12),
    months: int = Query(6, ge=1, le=12),
    tag_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    """Return per-category expense breakdown + income/expense totals for the
    ``months`` calendar months ending at (end_year, end_month) inclusive.

    Always works with calendar months regardless of the client's period_mode.
    Replaces the frontend's per-month /dashboard/summary loop (finding 1.3).
    """
    cal_months: list = []
    y, m = end_year, end_month
    for _ in range(months):
        cal_months.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    cal_months.reverse()  # oldest first

    result = []
    for cal_year, cal_month in cal_months:
        actual_query = (
            select(
                Category.name,
                func.sum(ProcessedTransaction.effective_amount).label("actual"),
            )
            .join(Category, Category.id == ProcessedTransaction.category_id)
            .where(
                ProcessedTransaction.year == cal_year,
                ProcessedTransaction.month == cal_month,
                ProcessedTransaction.user_id == user_id,
                ProcessedTransaction.txn_type.in_(["expense", "refund"]),
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

        income_scalar = db.execute(
            select(func.sum(ProcessedTransaction.effective_amount)).where(
                ProcessedTransaction.year == cal_year,
                ProcessedTransaction.month == cal_month,
                ProcessedTransaction.user_id == user_id,
                ProcessedTransaction.txn_type == "income",
            )
        ).scalar()
        # income rows carry negative effective_amount; negate to get a positive total
        income_total = Decimal(str(abs(income_scalar or 0)))

        category_breakdown = []
        expense_total = Decimal("0")
        for row in sorted(actual_rows, key=lambda r: r.name):
            actual = Decimal(str(row.actual))
            if actual > 0:
                category_breakdown.append(
                    MultiMonthCategoryRow(category=row.name, actual=actual)
                )
                expense_total += actual

        result.append(
            MultiMonthSummaryItem(
                year=cal_year,
                month=cal_month,
                expense_total=expense_total,
                income_total=income_total,
                category_breakdown=category_breakdown,
            )
        )

    return result


# ─── /split-ledger ───────────────────────────────────────────────────────────


@router.get("/split-ledger", response_model=List[SplitLedgerRow])
def split_ledger(
    month: int,
    year: int,
    include_settled: bool = False,
    period_mode: PeriodMode = Query("calendar"),
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    cal_year, cal_month = resolve_period_month(year, month, period_mode)

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
            ProcessedTransaction.year == cal_year,
            ProcessedTransaction.month == cal_month,
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


# ─── /ytd ────────────────────────────────────────────────────────────────────────────


@router.get("/ytd", response_model=List[YTDRow])
def ytd(
    year: int,
    period_mode: PeriodMode = Query("calendar"),
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    start, end = period_year_range(year, period_mode)

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
            ProcessedTransaction.txn_date >= start,
            ProcessedTransaction.txn_date <= end,
            ProcessedTransaction.user_id == user_id,
            ProcessedTransaction.txn_type.in_(["expense", "refund"]),
        )
        .group_by(Category.name)
    ).all()
    actual_map = {row.name: Decimal(str(row.actual)) for row in actual_rows}

    # Income categories: surfaced as separate rows with a NEGATIVE actual_ytd
    # (income rows carry negative effective_amount in the DB). Frontend filters
    # `actual_ytd < 0` to extract the per-category income breakdown; `> 0` keeps
    # expense rows. A category with both expense and income txns is reported
    # under expenses only — the per-month income KPI total still reflects all
    # income via /multi-month-summary.
    income_rows = db.execute(
        select(
            Category.name,
            func.sum(ProcessedTransaction.effective_amount).label("actual"),
        )
        .join(Category, Category.id == ProcessedTransaction.category_id)
        .where(
            ProcessedTransaction.txn_date >= start,
            ProcessedTransaction.txn_date <= end,
            ProcessedTransaction.user_id == user_id,
            ProcessedTransaction.txn_type == "income",
        )
        .group_by(Category.name)
    ).all()
    income_map = {
        row.name: Decimal(str(row.actual))
        for row in income_rows
        if row.name not in actual_map
    }

    all_categories = set(budget_map) | set(actual_map) | set(income_map)
    result = []
    for cat in sorted(all_categories):
        allocated = budget_map.get(cat, Decimal("0"))
        if cat in actual_map:
            actual = actual_map[cat]
        elif cat in income_map:
            actual = income_map[cat]
        else:
            actual = Decimal("0")
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
