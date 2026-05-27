"""Tests for selective auto-categorisation.

``POST /transactions/auto-categorise`` accepts an optional list of
``raw_txn_ids``. When omitted it processes every pending row for the user
(legacy behaviour). When provided, only those IDs are considered — used by
the bulk-selection "auto-categorise N" action in the transactions UI.

An empty list is treated as a no-op rather than "process everything" so an
accidental empty selection in the UI cannot trigger a full sweep.
"""

import os
import uuid
from datetime import datetime
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
    CategoryMapping,
    RawTransaction,
)

# Random uuid4 — never all-digit-hex; see backend_sqlite_uuid_affinity_trap.md
USER_ID = uuid.uuid4()


@pytest.fixture
def client_and_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SS = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    session = SS()

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


def _seed(session):
    """Two pending raws ('zomato lunch' and 'uber ride') with mappings that
    auto-categorise each cleanly via token_sort_ratio >= 80."""
    food = Category(id=uuid.uuid4(), user_id=USER_ID, name="food")
    transport = Category(id=uuid.uuid4(), user_id=USER_ID, name="transport")
    session.add_all([food, transport])

    session.add_all(
        [
            CategoryMapping(
                id=uuid.uuid4(),
                user_id=USER_ID,
                category_id=food.id,
                description_pattern="zomato lunch",
                match_count=0,
            ),
            CategoryMapping(
                id=uuid.uuid4(),
                user_id=USER_ID,
                category_id=transport.id,
                description_pattern="uber ride",
                match_count=0,
            ),
        ]
    )

    raw_zomato = RawTransaction(
        id=uuid.uuid4(),
        user_id=USER_ID,
        txn_date=datetime(2026, 5, 10),
        description="zomato lunch",
        amount=Decimal("250.00"),
        status="pending",
    )
    raw_uber = RawTransaction(
        id=uuid.uuid4(),
        user_id=USER_ID,
        txn_date=datetime(2026, 5, 11),
        description="uber ride",
        amount=Decimal("180.00"),
        status="pending",
    )
    session.add_all([raw_zomato, raw_uber])
    session.commit()
    return raw_zomato, raw_uber


def test_no_body_processes_all_pending(client_and_db):
    """Backwards-compatible: a POST with no body processes every pending row."""
    client, session = client_and_db
    raw_zomato, raw_uber = _seed(session)

    r = client.post("/transactions/auto-categorise")
    assert r.status_code == 200
    assert r.json() == {"auto_categorised": 2, "pending_manual": 0}

    session.refresh(raw_zomato)
    session.refresh(raw_uber)
    assert raw_zomato.status == "processed"
    assert raw_uber.status == "processed"


def test_selective_processes_only_listed_ids(client_and_db):
    """When raw_txn_ids is provided, only those IDs are processed; the others
    stay pending even if they would otherwise match a mapping."""
    client, session = client_and_db
    raw_zomato, raw_uber = _seed(session)

    r = client.post(
        "/transactions/auto-categorise",
        json={"raw_txn_ids": [str(raw_zomato.id)]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["auto_categorised"] == 1
    # pending_manual reflects everything still pending for the user, not just
    # the unselected subset of this call.
    assert body["pending_manual"] == 1

    session.refresh(raw_zomato)
    session.refresh(raw_uber)
    assert raw_zomato.status == "processed"
    assert raw_uber.status == "pending"


def test_empty_list_is_noop(client_and_db):
    """Empty list must NOT fall back to "process everything" — a stray empty
    selection in the UI should never trigger a full sweep."""
    client, session = client_and_db
    raw_zomato, raw_uber = _seed(session)

    r = client.post("/transactions/auto-categorise", json={"raw_txn_ids": []})
    assert r.status_code == 200
    assert r.json() == {"auto_categorised": 0, "pending_manual": 0}

    session.refresh(raw_zomato)
    session.refresh(raw_uber)
    assert raw_zomato.status == "pending"
    assert raw_uber.status == "pending"


def test_selective_does_not_leak_across_users(client_and_db):
    """A caller cannot process another user's row by passing its id."""
    client, session = client_and_db
    _, _ = _seed(session)

    other_user = uuid.uuid4()
    other_food = Category(id=uuid.uuid4(), user_id=other_user, name="food")
    other_mapping = CategoryMapping(
        id=uuid.uuid4(),
        user_id=other_user,
        category_id=other_food.id,
        description_pattern="zomato lunch",
        match_count=0,
    )
    other_raw = RawTransaction(
        id=uuid.uuid4(),
        user_id=other_user,
        txn_date=datetime(2026, 5, 10),
        description="zomato lunch",
        amount=Decimal("250.00"),
        status="pending",
    )
    session.add_all([other_food, other_mapping, other_raw])
    session.commit()

    r = client.post(
        "/transactions/auto-categorise",
        json={"raw_txn_ids": [str(other_raw.id)]},
    )
    assert r.status_code == 200
    assert r.json()["auto_categorised"] == 0

    session.refresh(other_raw)
    assert other_raw.status == "pending"
