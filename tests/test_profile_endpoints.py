"""Tests for profile-related auth endpoints.

Covers:
  GET  /auth/me         → now returns created_at + has_password

# More tests added in subsequent tasks
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
    Category,
    ProcessedTransaction,
    RawTransaction,
    User,
)

USER_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000002")


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
        yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: USER_ID

    user = User(id=USER_ID, email="test@example.com", password_hash="$2b$12$fakehash")
    session.add(user)
    session.commit()

    with TestClient(app) as c:
        yield c, session
    app.dependency_overrides.clear()
    session.close()
    engine.dispose()


# ── /auth/me ─────────────────────────────────────────────────────────────────


def test_me_returns_created_at_and_has_password(client_and_db):
    client, _ = client_and_db
    r = client.get("/auth/me", headers={"Authorization": "Bearer fake"})
    assert r.status_code == 200
    body = r.json()
    assert "created_at" in body
    assert body["has_password"] is True


def test_me_has_password_false_for_google_user(client_and_db):
    client, session = client_and_db
    google_id = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000003")
    session.add(
        User(
            id=google_id,
            email="g@example.com",
            password_hash=None,
            google_sub="gsub123",
        )
    )
    session.commit()

    app.dependency_overrides[get_current_user] = lambda: google_id
    try:
        r = client.get("/auth/me", headers={"Authorization": "Bearer fake"})
    finally:
        app.dependency_overrides[get_current_user] = lambda: USER_ID
    assert r.status_code == 200
    assert r.json()["has_password"] is False


# ── /auth/me/stats ───────────────────────────────────────────────────────────


def _seed_processed_txn(
    session,
    *,
    amount: Decimal,
    txn_date: date = date(2025, 6, 1),
) -> None:
    raw_id = uuid.uuid4()
    raw = RawTransaction(
        id=raw_id,
        user_id=USER_ID,
        txn_date=datetime.combine(txn_date, datetime.min.time()),
        description="test txn",
        amount=float(amount),
        status="processed",
    )
    cat_id = uuid.uuid4()
    cat = Category(
        id=cat_id, user_id=USER_ID, name=f"Cat-{cat_id.hex[:4]}", is_income=amount > 0
    )
    session.add_all([raw, cat])
    session.flush()

    processed = ProcessedTransaction(
        id=uuid.uuid4(),
        user_id=USER_ID,
        raw_txn_id=raw_id,
        category_id=cat_id,
        txn_date=txn_date,
        description="test txn",
        amount=float(amount),
        effective_amount=float(amount),
        month=txn_date.month,
        year=txn_date.year,
        txn_type="expense" if amount < 0 else "income",
    )
    session.add(processed)
    session.commit()


def test_stats_empty(client_and_db):
    client, _ = client_and_db
    r = client.get("/auth/me/stats", headers={"Authorization": "Bearer fake"})
    assert r.status_code == 200
    body = r.json()
    assert body["transaction_count"] == 0
    assert body["total_spend"] == 0.0


def test_stats_counts_and_sums_expenses(client_and_db):
    client, session = client_and_db
    _seed_processed_txn(session, amount=Decimal("-500.00"))
    _seed_processed_txn(session, amount=Decimal("-250.00"))
    _seed_processed_txn(
        session, amount=Decimal("1000.00")
    )  # income — not counted in spend
    r = client.get("/auth/me/stats", headers={"Authorization": "Bearer fake"})
    assert r.status_code == 200
    body = r.json()
    assert body["transaction_count"] == 3
    assert abs(body["total_spend"] - 750.0) < 0.01
