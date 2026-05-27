"""Tests for the 30-day soft-delete retention policy on raw transactions.

The policy is enforced by ``_purge_expired_soft_deletes`` in
``app.routers.transactions``. It runs automatically after every soft-delete
(raw OR processed) and is exposed manually via ``POST /transactions/raw/purge-expired``.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SUPABASE_JWT_SECRET", "pytest-placeholder-secret")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.auth import get_current_user  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Category,
    ProcessedTransaction,
    RawTransaction,
)
from app.routers.transactions import SOFT_DELETE_RETENTION_DAYS  # noqa: E402

# NOTE: do NOT use all-numeric-hex UUIDs (e.g. 00000000-...-000001) — SQLite
# applies NUMERIC affinity to columns it doesn't recognise as TEXT/INT/REAL/
# BLOB, including "UUID". A hex string consisting only of digits gets coerced
# to an integer on write and SQLAlchemy's UUID result processor then chokes
# trying to convert that int back to a UUID. Random uuid4 values always
# contain hex letters, sidestepping the affinity trap.
USER_ID = uuid.uuid4()
OTHER_USER_ID = uuid.uuid4()


@pytest.fixture
def client_and_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
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


def _make_raw(
    session,
    *,
    user_id=USER_ID,
    status="pending",
    deleted_at=None,
    description="test",
    amount=Decimal("100.00"),
):
    raw = RawTransaction(
        id=uuid.uuid4(),
        user_id=user_id,
        txn_date=datetime(2026, 5, 15),
        description=description,
        amount=amount,
        status=status,
        deleted_at=deleted_at,
    )
    session.add(raw)
    session.commit()
    return raw


def test_retention_constant_is_30_days():
    assert SOFT_DELETE_RETENTION_DAYS == 30


def test_expired_soft_deletes_are_purged_on_next_delete(client_and_db):
    """Soft-deleting any row triggers the retention purge of older rows."""
    client, session = client_and_db
    # An ancient soft-delete that should get purged
    ancient = _make_raw(
        session,
        status="deleted",
        deleted_at=datetime.now(timezone.utc) - timedelta(days=45),
        description="ancient",
    )
    # A fresh pending row we will soft-delete to trigger the purge
    fresh = _make_raw(session, description="fresh")

    r = client.delete(f"/transactions/raw/{fresh.id}")
    assert r.status_code == 204

    # Fresh row is now soft-deleted, ancient row is gone for good.
    ids = {r.id for r in session.execute(select(RawTransaction)).scalars().all()}
    assert fresh.id in ids
    assert ancient.id not in ids


def test_recent_soft_deletes_are_kept(client_and_db):
    """A row soft-deleted yesterday is well inside the retention window."""
    client, session = client_and_db
    recent = _make_raw(
        session,
        status="deleted",
        deleted_at=datetime.now(timezone.utc) - timedelta(days=1),
        description="recent",
    )
    fresh = _make_raw(session, description="trigger")

    r = client.delete(f"/transactions/raw/{fresh.id}")
    assert r.status_code == 204

    ids = {r.id for r in session.execute(select(RawTransaction)).scalars().all()}
    assert recent.id in ids


def test_other_users_soft_deletes_are_not_touched(client_and_db):
    """Purge is scoped to the acting user — never touches other users' data."""
    client, session = client_and_db
    foreign_ancient = _make_raw(
        session,
        user_id=OTHER_USER_ID,
        status="deleted",
        deleted_at=datetime.now(timezone.utc) - timedelta(days=90),
        description="someone-else-ancient",
    )
    fresh = _make_raw(session, description="trigger")

    r = client.delete(f"/transactions/raw/{fresh.id}")
    assert r.status_code == 204

    still_there = session.get(RawTransaction, foreign_ancient.id)
    assert still_there is not None, "purge leaked into another user's rows"


def test_restore_clears_deleted_at(client_and_db):
    """Restore returns the row to ``pending`` AND clears ``deleted_at`` so a
    later re-delete starts the retention clock fresh."""
    client, session = client_and_db
    raw = _make_raw(
        session,
        status="deleted",
        deleted_at=datetime.now(timezone.utc) - timedelta(days=10),
        description="round-trip",
    )

    r = client.patch(f"/transactions/raw/{raw.id}/restore")
    assert r.status_code == 200

    session.refresh(raw)
    assert raw.status == "pending"
    assert raw.deleted_at is None


def test_manual_purge_endpoint_returns_count(client_and_db):
    """The manual endpoint reports how many rows it purged."""
    client, session = client_and_db
    for i in range(3):
        _make_raw(
            session,
            status="deleted",
            deleted_at=datetime.now(timezone.utc) - timedelta(days=40 + i),
            description=f"expired-{i}",
        )
    # One row that should NOT be purged
    _make_raw(
        session,
        status="deleted",
        deleted_at=datetime.now(timezone.utc) - timedelta(days=5),
        description="recent",
    )

    r = client.post("/transactions/raw/purge-expired")
    assert r.status_code == 200
    body = r.json()
    assert body == {"purged": 3, "retention_days": SOFT_DELETE_RETENTION_DAYS}

    # Recent row is still there, ancient ones are gone.
    remaining = session.execute(select(RawTransaction)).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].description == "recent"


def test_purge_skips_rows_with_live_processed_reference(client_and_db):
    """Defensive invariant: a soft-deleted raw with a live processed row
    pointing at it is NOT purged. (This state shouldn't occur in practice
    because ``delete_processed_transaction`` hard-deletes the processed
    before soft-deleting the raw — but we test the guard anyway.)"""
    client, session = client_and_db
    cat = Category(
        id=uuid.uuid4(),
        user_id=USER_ID,
        name="cat",
    )
    session.add(cat)
    raw = _make_raw(
        session,
        status="deleted",
        deleted_at=datetime.now(timezone.utc) - timedelta(days=60),
        description="orphan-attempt",
    )
    proc = ProcessedTransaction(
        id=uuid.uuid4(),
        user_id=USER_ID,
        raw_txn_id=raw.id,
        category_id=cat.id,
        txn_date=raw.txn_date.date(),
        description=raw.description,
        amount=raw.amount,
        effective_amount=raw.amount,
        year=2026,
        month=5,
        txn_type="expense",
    )
    session.add(proc)
    session.commit()

    r = client.post("/transactions/raw/purge-expired")
    assert r.status_code == 200
    assert r.json()["purged"] == 0
    # Raw row was protected from the purge
    assert session.get(RawTransaction, raw.id) is not None
