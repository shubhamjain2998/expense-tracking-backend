"""Tests for the /dashboard/ytd income breakdown fix.

Before the fix, /dashboard/ytd filtered to ``txn_type IN ("expense", "refund")``
so income categories never surfaced. The frontend "Income sources YTD" panel
showed "No income recorded this year" even when income transactions existed.

After the fix, income categories are returned as additional YTDRow rows with
``actual_ytd`` set to the (negative) sum of income ``effective_amount`` and
``allocated_ytd = 0``. The frontend filters ``actual_ytd < 0`` to build the
per-category income breakdown; ``> 0`` to build the expense breakdown.
"""

import os
import uuid
from datetime import date, datetime
from decimal import Decimal

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SUPABASE_JWT_SECRET", "pytest-placeholder-secret")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.auth import get_current_user  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    BudgetPlan,
    Category,
    ProcessedTransaction,
    RawTransaction,
)

USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def client_and_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSession()

    def override_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: USER_ID
    with TestClient(app) as c:
        yield c, session
    app.dependency_overrides.clear()
    session.close()
    engine.dispose()


def _seed_txn(
    db,
    *,
    category_id: uuid.UUID,
    raw_id: uuid.UUID,
    amount: Decimal,
    effective: Decimal,
    txn_type: str,
    txn_date: date,
) -> None:
    raw = RawTransaction(
        id=raw_id,
        user_id=USER_ID,
        txn_date=datetime.combine(txn_date, datetime.min.time()),
        description=f"{txn_type} {raw_id.hex[:6]}",
        amount=float(amount),
    )
    db.add(raw)
    db.flush()
    txn = ProcessedTransaction(
        id=uuid.uuid4(),
        user_id=USER_ID,
        raw_txn_id=raw.id,
        category_id=category_id,
        txn_date=txn_date,
        description=raw.description,
        amount=float(amount),
        effective_amount=float(effective),
        month=txn_date.month,
        year=txn_date.year,
        txn_type=txn_type,
    )
    db.add(txn)


def _seed_category(db, name: str) -> Category:
    cat = Category(id=uuid.uuid4(), user_id=USER_ID, name=name)
    db.add(cat)
    db.flush()
    return cat


def test_ytd_returns_income_categories_as_negative_rows(client_and_db):
    """Income categories surface with negative actual_ytd and zero allocated."""
    client, db = client_and_db

    salary = _seed_category(db, "salary")
    food = _seed_category(db, "food")
    _seed_txn(
        db,
        category_id=salary.id,
        raw_id=uuid.uuid4(),
        amount=Decimal("-100000"),
        effective=Decimal("-100000"),
        txn_type="income",
        txn_date=date(2026, 4, 15),
    )
    _seed_txn(
        db,
        category_id=food.id,
        raw_id=uuid.uuid4(),
        amount=Decimal("2500"),
        effective=Decimal("2500"),
        txn_type="expense",
        txn_date=date(2026, 4, 16),
    )
    db.commit()

    r = client.get("/dashboard/ytd", params={"year": 2026, "period_mode": "fy"})
    assert r.status_code == 200
    rows = {row["category"]: row for row in r.json()}
    assert set(rows) == {"salary", "food"}
    assert Decimal(rows["salary"]["actual_ytd"]) == Decimal("-100000")
    assert Decimal(rows["salary"]["allocated_ytd"]) == Decimal("0")
    assert Decimal(rows["food"]["actual_ytd"]) == Decimal("2500")


def test_ytd_multiple_income_categories(client_and_db):
    """Multiple income categories each get their own negative-actual row."""
    client, db = client_and_db

    salary = _seed_category(db, "salary")
    interest = _seed_category(db, "interest")
    _seed_txn(
        db,
        category_id=salary.id,
        raw_id=uuid.uuid4(),
        amount=Decimal("-257238"),
        effective=Decimal("-257238"),
        txn_type="income",
        txn_date=date(2026, 5, 1),
    )
    _seed_txn(
        db,
        category_id=interest.id,
        raw_id=uuid.uuid4(),
        amount=Decimal("-4250"),
        effective=Decimal("-4250"),
        txn_type="income",
        txn_date=date(2026, 5, 10),
    )
    db.commit()

    r = client.get("/dashboard/ytd", params={"year": 2026, "period_mode": "fy"})
    assert r.status_code == 200
    rows = {row["category"]: row for row in r.json()}
    assert Decimal(rows["salary"]["actual_ytd"]) == Decimal("-257238")
    assert Decimal(rows["interest"]["actual_ytd"]) == Decimal("-4250")
    income_sum = sum(
        abs(Decimal(row["actual_ytd"]))
        for row in r.json()
        if Decimal(row["actual_ytd"]) < 0
    )
    assert income_sum == Decimal("261488")


def test_ytd_pure_expense_unchanged(client_and_db):
    """Categories with only expense activity behave exactly as before."""
    client, db = client_and_db

    food = _seed_category(db, "food")
    _seed_txn(
        db,
        category_id=food.id,
        raw_id=uuid.uuid4(),
        amount=Decimal("1000"),
        effective=Decimal("1000"),
        txn_type="expense",
        txn_date=date(2026, 4, 15),
    )
    _seed_txn(
        db,
        category_id=food.id,
        raw_id=uuid.uuid4(),
        amount=Decimal("500"),
        effective=Decimal("500"),
        txn_type="refund",
        txn_date=date(2026, 4, 20),
    )
    db.commit()

    r = client.get("/dashboard/ytd", params={"year": 2026, "period_mode": "fy"})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["category"] == "food"
    assert Decimal(rows[0]["actual_ytd"]) == Decimal("1500")


def test_ytd_mixed_category_only_emits_expense_row(client_and_db):
    """Categories with both expense and income txns get one expense row.

    Avoids a Map collision in the frontend budget feature which keys rows by
    category name. The total income KPI still reflects mixed-category income
    via /multi-month-summary (which sums by txn_type, not category).
    """
    client, db = client_and_db

    family = _seed_category(db, "family")
    _seed_txn(
        db,
        category_id=family.id,
        raw_id=uuid.uuid4(),
        amount=Decimal("3000"),
        effective=Decimal("3000"),
        txn_type="expense",
        txn_date=date(2026, 4, 15),
    )
    _seed_txn(
        db,
        category_id=family.id,
        raw_id=uuid.uuid4(),
        amount=Decimal("-1500"),
        effective=Decimal("-1500"),
        txn_type="income",
        txn_date=date(2026, 4, 20),
    )
    db.commit()

    r = client.get("/dashboard/ytd", params={"year": 2026, "period_mode": "fy"})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["category"] == "family"
    assert Decimal(rows[0]["actual_ytd"]) == Decimal("3000")


def test_ytd_income_respects_fy_window(client_and_db):
    """Income txns outside the fiscal-year window are excluded."""
    client, db = client_and_db

    salary = _seed_category(db, "salary")
    _seed_txn(
        db,
        category_id=salary.id,
        raw_id=uuid.uuid4(),
        amount=Decimal("-100000"),
        effective=Decimal("-100000"),
        txn_type="income",
        txn_date=date(2026, 3, 30),  # FY 2025-26 (ends 2026-03-31)
    )
    _seed_txn(
        db,
        category_id=salary.id,
        raw_id=uuid.uuid4(),
        amount=Decimal("-200000"),
        effective=Decimal("-200000"),
        txn_type="income",
        txn_date=date(2026, 4, 1),  # FY 2026-27 (starts 2026-04-01)
    )
    db.commit()

    r = client.get("/dashboard/ytd", params={"year": 2026, "period_mode": "fy"})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert Decimal(rows[0]["actual_ytd"]) == Decimal("-200000")


def test_ytd_budgeted_category_with_no_activity(client_and_db):
    """Budgeted category with zero activity still has actual_ytd = 0 (regression)."""
    client, db = client_and_db

    food = _seed_category(db, "food")
    db.add(
        BudgetPlan(
            id=uuid.uuid4(),
            user_id=USER_ID,
            category_id=food.id,
            year=2026,
            allocated_amount=Decimal("12000"),
        )
    )
    db.commit()

    r = client.get("/dashboard/ytd", params={"year": 2026, "period_mode": "fy"})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["category"] == "food"
    assert Decimal(rows[0]["actual_ytd"]) == Decimal("0")
    assert Decimal(rows[0]["allocated_ytd"]) == Decimal("12000")
