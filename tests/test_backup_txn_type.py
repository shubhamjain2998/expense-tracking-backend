"""Tests for the txn_type round-trip across backup export/import and
through the create-raw → categorize flow.

Two surface areas, one bug class: the txn_type field used to silently drop
to "expense" on both paths. These tests pin the new behavior so a
regression — e.g., someone removing the field from BackupTransaction or
the create-raw schema — fails loudly.
"""

import os
import uuid
from datetime import date, datetime
from decimal import Decimal

# Minimal env for app.config so importing the app modules doesn't blow up
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SUPABASE_JWT_SECRET", "pytest-placeholder-secret")

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base  # noqa: E402
from app.models import (  # noqa: E402
    Category,
    ProcessedTransaction,
    RawTransaction,
)
from app.schemas import (  # noqa: E402
    BackupImport,
    BackupNamedEntity,
    BackupTransaction,
    CreateRawTransactionRequest,
    ProcessTransactionRequest,
)
from app.services.backup import (  # noqa: E402
    export_user_data,
    import_user_data,
)


# ── In-memory DB fixture ──────────────────────────────────────────────────────


@pytest.fixture
def db():
    """Fresh SQLite in-memory session with all ORM tables created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


# ── Schema-level guards ───────────────────────────────────────────────────────


def test_backup_transaction_accepts_txn_type():
    """BackupTransaction must accept all four txn_type literal values."""
    for t in ("expense", "income", "refund", "transfer"):
        BackupTransaction(
            txn_date=date(2026, 5, 1),
            description="x",
            amount=Decimal("100"),
            category="cat",
            txn_type=t,
        )


def test_backup_transaction_txn_type_optional():
    """Omitting txn_type must not raise — older backups stay parseable."""
    bt = BackupTransaction(
        txn_date=date(2026, 5, 1),
        description="x",
        amount=Decimal("100"),
        category="cat",
    )
    assert bt.txn_type is None


def test_create_raw_transaction_request_accepts_txn_type():
    """Manual entry endpoint accepts a user-chosen txn_type."""
    body = CreateRawTransactionRequest(
        txn_date=datetime(2026, 5, 1),
        description="Salary May",
        amount=Decimal("50000"),
        txn_type="income",
    )
    assert body.txn_type == "income"


def test_create_raw_transaction_request_txn_type_optional():
    """Omitting txn_type keeps current behavior — falls back to classify."""
    body = CreateRawTransactionRequest(
        txn_date=datetime(2026, 5, 1),
        description="Swiggy",
        amount=Decimal("449"),
    )
    assert body.txn_type is None


def test_process_transaction_request_accepts_txn_type():
    """Categorize-time type pick must round-trip through the schema."""
    body = ProcessTransactionRequest(
        raw_txn_id=uuid.uuid4(),
        category_id=uuid.uuid4(),
        txn_type="income",
    )
    assert body.txn_type == "income"


def test_process_transaction_request_txn_type_optional():
    """Default behavior preserved when txn_type omitted (falls back to
    raw.txn_type, then classifier — verified at the router level)."""
    body = ProcessTransactionRequest(
        raw_txn_id=uuid.uuid4(),
        category_id=uuid.uuid4(),
    )
    assert body.txn_type is None


# ── Full export → import round-trip ────────────────────────────────────────────


def _seed_user_with_one_income_txn(db) -> uuid.UUID:
    user_id = uuid.uuid4()
    cat = Category(user_id=user_id, name="salary")
    db.add(cat)
    db.flush()

    raw = RawTransaction(
        user_id=user_id,
        txn_date=datetime(2026, 5, 1),
        description="Acme Corp Salary",
        amount=50000,
        status="processed",
    )
    db.add(raw)
    db.flush()

    db.add(
        ProcessedTransaction(
            user_id=user_id,
            raw_txn_id=raw.id,
            mapping_id=None,
            category_id=cat.id,
            txn_date=date(2026, 5, 1),
            description="Acme Corp Salary",
            amount=50000,
            effective_amount=50000,
            month=5,
            year=2026,
            txn_type="income",
        )
    )
    db.commit()
    return user_id


def test_export_includes_txn_type(db):
    user_id = _seed_user_with_one_income_txn(db)
    payload = export_user_data(user_id, db)

    assert len(payload.transactions) == 1
    assert payload.transactions[0].txn_type == "income"


def test_import_honors_txn_type_from_payload(db):
    user_id = uuid.uuid4()

    payload = BackupImport(
        categories=[BackupNamedEntity(name="salary")],
        transactions=[
            BackupTransaction(
                txn_date=date(2026, 5, 1),
                description="Acme Corp Salary",
                amount=Decimal("50000"),
                category="salary",
                txn_type="income",
            )
        ],
    )
    result = import_user_data(payload, user_id, db)
    assert result.transactions_imported == 1

    processed = db.query(ProcessedTransaction).filter_by(user_id=user_id).one()
    assert processed.txn_type == "income"


def test_import_falls_back_to_classifier_when_txn_type_missing(db):
    """An older backup (no txn_type) must still produce a sensible default."""
    user_id = uuid.uuid4()

    payload = BackupImport(
        categories=[BackupNamedEntity(name="groceries")],
        transactions=[
            BackupTransaction(
                txn_date=date(2026, 5, 1),
                description="Blinkit",
                amount=Decimal("449"),  # positive → expense per classifier
                category="groceries",
            )
        ],
    )
    import_user_data(payload, user_id, db)

    processed = db.query(ProcessedTransaction).filter_by(user_id=user_id).one()
    assert processed.txn_type == "expense"


def test_round_trip_preserves_income(db):
    """Seed an income, export, import to a fresh user — type must be preserved."""
    src_user = _seed_user_with_one_income_txn(db)
    payload = export_user_data(src_user, db)

    # Re-import as a different user so we don't collide with the seeded row
    dst_user = uuid.uuid4()
    import_user_data(
        BackupImport(
            categories=payload.categories,
            tags=payload.tags,
            persons=payload.persons,
            budget_plans=payload.budget_plans,
            transactions=payload.transactions,
        ),
        dst_user,
        db,
    )

    dst_proc = db.query(ProcessedTransaction).filter_by(user_id=dst_user).one()
    assert dst_proc.txn_type == "income"
