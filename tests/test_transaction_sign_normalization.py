"""Tests for the storage sign convention on processed transactions.

Storage invariant: ``expense`` rows have positive ``amount`` / ``effective_amount``;
``income`` / ``refund`` / ``transfer`` rows have negative values. Manual entries
used to violate the invariant (user types a positive amount, backend stored it
verbatim) which broke aggregation like the YTD income tile.

These tests cover the three write paths that previously skipped normalization:
``POST /transactions/process``, ``PATCH /transactions/processed/{id}``, and the
mapping-driven ``auto_categorise`` promotion.
"""

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

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
    CategoryMapping,
    Person,
    ProcessedTransaction,
    RawTransaction,
)

USER_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


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


def _seed_category(db, name: str) -> Category:
    cat = Category(id=uuid.uuid4(), user_id=USER_ID, name=name)
    db.add(cat)
    db.flush()
    return cat


def _seed_raw(db, *, amount: Decimal, txn_type: Optional[str] = None) -> RawTransaction:
    raw = RawTransaction(
        id=uuid.uuid4(),
        user_id=USER_ID,
        txn_date=datetime(2026, 5, 27, tzinfo=timezone.utc),
        description=f"manual {txn_type or 'untyped'} {uuid.uuid4().hex[:6]}",
        amount=float(amount),
        status="pending",
        txn_type=txn_type,
    )
    db.add(raw)
    db.flush()
    return raw


def test_process_income_normalizes_positive_amount_to_negative(client_and_db):
    client, db = client_and_db
    cat = _seed_category(db, "income")
    raw = _seed_raw(db, amount=Decimal("100000"), txn_type="income")
    db.commit()

    r = client.post(
        "/transactions/process",
        json={
            "raw_txn_id": str(raw.id),
            "category_id": str(cat.id),
            "save_mapping": False,
            "shares": [],
            "txn_type": "income",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert Decimal(body["effective_amount"]) == Decimal("-100000")
    assert Decimal(body["amount"]) == Decimal("-100000")


def test_process_refund_typed_positive_is_stored_negative(client_and_db):
    client, db = client_and_db
    cat = _seed_category(db, "groceries")
    raw = _seed_raw(db, amount=Decimal("500"), txn_type="refund")
    db.commit()

    r = client.post(
        "/transactions/process",
        json={
            "raw_txn_id": str(raw.id),
            "category_id": str(cat.id),
            "save_mapping": False,
            "shares": [],
            "txn_type": "refund",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert Decimal(body["effective_amount"]) == Decimal("-500")
    assert Decimal(body["amount"]) == Decimal("-500")


def test_process_expense_keeps_positive_amount(client_and_db):
    """Sanity: expense path unchanged — amount and effective_amount stay positive."""
    client, db = client_and_db
    cat = _seed_category(db, "food")
    raw = _seed_raw(db, amount=Decimal("250"), txn_type="expense")
    db.commit()

    r = client.post(
        "/transactions/process",
        json={
            "raw_txn_id": str(raw.id),
            "category_id": str(cat.id),
            "save_mapping": False,
            "shares": [],
            "txn_type": "expense",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert Decimal(body["effective_amount"]) == Decimal("250")
    assert Decimal(body["amount"]) == Decimal("250")


def test_process_income_with_shares_normalizes_shares_too(client_and_db):
    """Splits on income should record a negative share_amount, matching the
    sign of the parent. Otherwise the share-ledger view would mis-classify
    money owed back to the user."""
    client, db = client_and_db
    cat = _seed_category(db, "salary")
    raw = _seed_raw(db, amount=Decimal("100000"), txn_type="income")
    person = Person(id=uuid.uuid4(), user_id=USER_ID, name="Spouse")
    db.add(person)
    db.commit()

    r = client.post(
        "/transactions/process",
        json={
            "raw_txn_id": str(raw.id),
            "category_id": str(cat.id),
            "save_mapping": False,
            "shares": [
                {
                    "person_id": str(person.id),
                    "share_type": "percentage",
                    "share_value": 50,
                }
            ],
            "txn_type": "income",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # effective = my half of the income = -50000
    assert Decimal(body["effective_amount"]) == Decimal("-50000")
    assert Decimal(body["amount"]) == Decimal("-100000")
    assert len(body["shares"]) == 1
    assert Decimal(body["shares"][0]["share_amount"]) == Decimal("-50000")


def test_patch_flipping_expense_to_income_resigns_amount(client_and_db):
    """Editing txn_type from expense → income on an existing row must flip
    the stored sign so YTD math stays correct."""
    client, db = client_and_db
    cat = _seed_category(db, "misc")
    raw = _seed_raw(db, amount=Decimal("750"), txn_type="expense")
    db.commit()

    create = client.post(
        "/transactions/process",
        json={
            "raw_txn_id": str(raw.id),
            "category_id": str(cat.id),
            "save_mapping": False,
            "shares": [],
            "txn_type": "expense",
        },
    )
    assert create.status_code == 200
    txn_id = create.json()["id"]
    assert Decimal(create.json()["effective_amount"]) == Decimal("750")

    patch = client.patch(
        f"/transactions/processed/{txn_id}",
        json={"txn_type": "income"},
    )
    assert patch.status_code == 200, patch.text
    body = patch.json()
    assert body["txn_type"] == "income"
    assert Decimal(body["effective_amount"]) == Decimal("-750")
    assert Decimal(body["amount"]) == Decimal("-750")


def test_patch_amount_on_income_keeps_negative_sign(client_and_db):
    """Editing the amount on an income row should preserve the negative sign
    regardless of whether the user types the new amount as positive."""
    client, db = client_and_db
    cat = _seed_category(db, "salary")
    raw = _seed_raw(db, amount=Decimal("100000"), txn_type="income")
    db.commit()

    create = client.post(
        "/transactions/process",
        json={
            "raw_txn_id": str(raw.id),
            "category_id": str(cat.id),
            "save_mapping": False,
            "shares": [],
            "txn_type": "income",
        },
    )
    txn_id = create.json()["id"]

    patch = client.patch(
        f"/transactions/processed/{txn_id}",
        # User typed a positive 125000 into the amount field
        json={"amount": 125000, "shares": []},
    )
    assert patch.status_code == 200, patch.text
    body = patch.json()
    assert Decimal(body["effective_amount"]) == Decimal("-125000")
    assert Decimal(body["amount"]) == Decimal("-125000")


def test_auto_categorise_income_mapping_stores_negative(client_and_db):
    """A pending raw with txn_type='income' that matches an existing mapping
    must come out the other side with negative effective_amount."""
    client, db = client_and_db
    cat = _seed_category(db, "dividend")
    # Seed a mapping that will match on description.
    mapping = CategoryMapping(
        id=uuid.uuid4(),
        user_id=USER_ID,
        description_pattern="manual income abcdef",
        category_id=cat.id,
        match_count=0,
        last_used=datetime.now(timezone.utc),
    )
    db.add(mapping)
    raw = RawTransaction(
        id=uuid.uuid4(),
        user_id=USER_ID,
        txn_date=datetime(2026, 5, 27, tzinfo=timezone.utc),
        description="manual income abcdef",
        amount=2500.0,
        status="pending",
        txn_type="income",
    )
    db.add(raw)
    db.commit()

    r = client.post(
        "/transactions/auto-categorise", json={"raw_txn_ids": [str(raw.id)]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["auto_categorised"] == 1

    processed = (
        db.query(ProcessedTransaction)
        .filter(ProcessedTransaction.raw_txn_id == raw.id)
        .one()
    )
    assert processed.txn_type == "income"
    assert Decimal(str(processed.effective_amount)) == Decimal("-2500")
    assert Decimal(str(processed.amount)) == Decimal("-2500")
