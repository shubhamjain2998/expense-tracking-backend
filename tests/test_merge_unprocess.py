"""Tests for merging rows, sending a row back to review, and the mapping race.

Three fixes live here:

* ``POST /transactions/merge`` — club the statement lines of one real-world
  payment into a single row, sources soft-deleted so the merge is undoable.
* ``POST /transactions/processed/{id}/unprocess`` — return a categorised row
  to Needs review without throwing the statement line away.
* ``_upsert_category_mapping`` — categorising a multi-row selection fires one
  /process per row in parallel; rows sharing a description used to race on
  ``uq_category_mappings_user_pattern``, so one row categorised and the other
  failed.
"""

import os
import uuid
from datetime import date, datetime
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
    CategoryMapping,
    Person,
    ProcessedTransaction,
    RawTransaction,
    Tag,
    TransactionPersonShare,
)
from app.routers.transactions import _upsert_category_mapping  # noqa: E402

# Letters in the hex on purpose: an all-digit-hex UUID gets coerced to an int
# by SQLite's NUMERIC affinity and breaks the UUID result processor.
USER_ID = uuid.UUID("aaaaaaaa-0000-4000-8000-00000000000a")


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


def _category(db, name: str) -> Category:
    cat = Category(id=uuid.uuid4(), user_id=USER_ID, name=name)
    db.add(cat)
    db.flush()
    return cat


def _raw(db, *, description: str, amount: Decimal, day: int = 10) -> RawTransaction:
    raw = RawTransaction(
        id=uuid.uuid4(),
        user_id=USER_ID,
        txn_date=datetime(2026, 9, day),
        description=description,
        amount=float(amount),
        status="pending",
    )
    db.add(raw)
    db.flush()
    return raw


def _processed(
    db,
    *,
    category: Category,
    description: str,
    amount: Decimal,
    txn_type: str = "expense",
    day: int = 10,
) -> ProcessedTransaction:
    raw = _raw(db, description=description, amount=amount, day=day)
    raw.status = "processed"
    txn = ProcessedTransaction(
        id=uuid.uuid4(),
        user_id=USER_ID,
        raw_txn_id=raw.id,
        category_id=category.id,
        txn_date=date(2026, 9, day),
        description=description,
        amount=float(amount),
        effective_amount=float(amount),
        month=9,
        year=2026,
        txn_type=txn_type,
    )
    db.add(txn)
    db.flush()
    return txn


# ── merge ─────────────────────────────────────────────────────────────────────


def test_merge_two_pending_rows_sums_amounts_and_keeps_base_identity(client_and_db):
    client, db = client_and_db
    base = _raw(db, description="UPI-DOMINOS", amount=Decimal("368.82"), day=18)
    other = _raw(db, description="Upi-hungerbox", amount=Decimal("50.00"), day=17)
    db.commit()

    r = client.post(
        "/transactions/merge",
        json={
            "base": {"kind": "pending", "id": str(base.id)},
            "sources": [{"kind": "pending", "id": str(other.id)}],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "pending"
    assert body["merged_count"] == 2
    assert Decimal(body["amount"]) == Decimal("418.82")

    db.expire_all()
    assert Decimal(str(db.get(RawTransaction, base.id).amount)) == Decimal("418.82")
    assert db.get(RawTransaction, base.id).description == "UPI-DOMINOS"
    # The absorbed row is recoverable, not gone.
    absorbed = db.get(RawTransaction, other.id)
    assert absorbed.status == "deleted"
    assert absorbed.deleted_at is not None


def test_merge_processed_base_keeps_category_and_updates_totals(client_and_db):
    client, db = client_and_db
    cat = _category(db, "sports & fitness")
    base = _processed(
        db, category=cat, description="TECHMASH SOLUTIONS", amount=Decimal("160.35")
    )
    source = _processed(
        db, category=cat, description="Fcy Conversion Markup", amount=Decimal("44.79")
    )
    db.commit()

    r = client.post(
        "/transactions/merge",
        json={
            "base": {"kind": "processed", "id": str(base.id)},
            "sources": [{"kind": "processed", "id": str(source.id)}],
        },
    )
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["amount"]) == Decimal("205.14")

    db.expire_all()
    merged = db.get(ProcessedTransaction, base.id)
    assert Decimal(str(merged.amount)) == Decimal("205.14")
    assert Decimal(str(merged.effective_amount)) == Decimal("205.14")
    assert merged.category_id == cat.id
    assert merged.description == "TECHMASH SOLUTIONS"
    # Source processed row is gone; its statement line is in the deleted bucket.
    assert db.get(ProcessedTransaction, source.id) is None
    assert db.get(RawTransaction, source.raw_txn_id).status == "deleted"


def test_merge_recomputes_percentage_splits_against_the_new_total(client_and_db):
    client, db = client_and_db
    cat = _category(db, "food")
    person = Person(id=uuid.uuid4(), user_id=USER_ID, name="A")
    db.add(person)
    db.flush()
    base = _processed(db, category=cat, description="Dinner", amount=Decimal("100.00"))
    db.add(
        TransactionPersonShare(
            processed_txn_id=base.id,
            person_id=person.id,
            share_type="percentage",
            share_value=50.0,
            share_amount=50.0,
        )
    )
    base.effective_amount = 50.0
    source = _raw(db, description="Dinner tip", amount=Decimal("40.00"))
    db.commit()

    r = client.post(
        "/transactions/merge",
        json={
            "base": {"kind": "processed", "id": str(base.id)},
            "sources": [{"kind": "pending", "id": str(source.id)}],
        },
    )
    assert r.status_code == 200, r.text

    db.expire_all()
    merged = db.get(ProcessedTransaction, base.id)
    assert Decimal(str(merged.amount)) == Decimal("140.00")
    assert Decimal(str(merged.shares[0].share_amount)) == Decimal("70.00")
    assert Decimal(str(merged.effective_amount)) == Decimal("70.00")


def test_merge_rejects_mixing_money_in_with_money_out(client_and_db):
    client, db = client_and_db
    cat = _category(db, "salary")
    spend = _raw(db, description="Swiggy", amount=Decimal("400.00"))
    refund = _processed(
        db,
        category=cat,
        description="Swiggy refund",
        amount=Decimal("-400.00"),
        txn_type="refund",
    )
    db.commit()

    r = client.post(
        "/transactions/merge",
        json={
            "base": {"kind": "pending", "id": str(spend.id)},
            "sources": [{"kind": "processed", "id": str(refund.id)}],
        },
    )
    assert r.status_code == 400
    assert "money-in" in r.json()["detail"]
    db.expire_all()
    assert Decimal(str(db.get(RawTransaction, spend.id).amount)) == Decimal("400.00")


def test_merge_rejects_a_row_merged_into_itself(client_and_db):
    client, db = client_and_db
    base = _raw(db, description="Swiggy", amount=Decimal("400.00"))
    db.commit()

    r = client.post(
        "/transactions/merge",
        json={
            "base": {"kind": "pending", "id": str(base.id)},
            "sources": [{"kind": "pending", "id": str(base.id)}],
        },
    )
    assert r.status_code == 400


def test_merge_rejects_an_already_processed_row_addressed_as_pending(client_and_db):
    client, db = client_and_db
    cat = _category(db, "food")
    processed = _processed(db, category=cat, description="Swiggy", amount=Decimal("1"))
    base = _raw(db, description="Swiggy 2", amount=Decimal("2"))
    db.commit()

    r = client.post(
        "/transactions/merge",
        json={
            "base": {"kind": "pending", "id": str(base.id)},
            "sources": [{"kind": "pending", "id": str(processed.raw_txn_id)}],
        },
    )
    assert r.status_code == 409


# ── unprocess ─────────────────────────────────────────────────────────────────


def test_unprocess_returns_the_row_to_pending_and_keeps_the_statement_line(
    client_and_db,
):
    client, db = client_and_db
    cat = _category(db, "food")
    txn = _processed(
        db, category=cat, description="Swiggy IND", amount=Decimal("481.65")
    )
    tag = Tag(id=uuid.uuid4(), user_id=USER_ID, name="office meal")
    db.add(tag)
    db.flush()
    txn.tags = [tag]
    db.commit()
    raw_id = txn.raw_txn_id

    r = client.post(f"/transactions/processed/{txn.id}/unprocess")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"
    assert Decimal(r.json()["amount"]) == Decimal("481.65")

    db.expire_all()
    assert db.get(ProcessedTransaction, txn.id) is None
    raw = db.get(RawTransaction, raw_id)
    assert raw.status == "pending"
    assert raw.deleted_at is None
    assert raw.description == "Swiggy IND"


def test_unprocess_leaves_the_learned_mapping_in_place(client_and_db):
    client, db = client_and_db
    cat = _category(db, "food")
    txn = _processed(db, category=cat, description="Swiggy IND", amount=Decimal("100"))
    mapping = CategoryMapping(
        id=uuid.uuid4(),
        user_id=USER_ID,
        description_pattern="Swiggy IND",
        category_id=cat.id,
        match_count=0,
    )
    db.add(mapping)
    db.flush()
    txn.mapping_id = mapping.id
    db.commit()

    r = client.post(f"/transactions/processed/{txn.id}/unprocess")
    assert r.status_code == 200
    db.expire_all()
    assert db.get(CategoryMapping, mapping.id) is not None


def test_unprocess_404s_for_an_unknown_row(client_and_db):
    client, _ = client_and_db
    r = client.post(f"/transactions/processed/{uuid.uuid4()}/unprocess")
    assert r.status_code == 404


# ── parallel categorise: the mapping race ─────────────────────────────────────


def test_process_is_race_safe_when_two_rows_share_a_description(tmp_path):
    """The loser of a concurrent mapping insert reuses the winner's row.

    Two connections on a file-backed SQLite database reproduce what two
    parallel /process requests do in Postgres: both read "no mapping", both
    insert, one hits the unique constraint.
    """
    db_path = tmp_path / "race.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    setup = SessionFactory()
    cat = Category(id=uuid.uuid4(), user_id=USER_ID, name="food")
    setup.add(cat)
    setup.commit()
    cat_id = cat.id
    setup.close()

    loser = SessionFactory()
    # The loser reads first — no mapping exists yet.
    assert (
        loser.execute(
            select(CategoryMapping).where(
                CategoryMapping.user_id == USER_ID,
                CategoryMapping.description_pattern == "Urbanclap Technologi",
            )
        ).scalar_one_or_none()
        is None
    )

    winner = SessionFactory()
    winner.add(
        CategoryMapping(
            id=uuid.uuid4(),
            user_id=USER_ID,
            description_pattern="Urbanclap Technologi",
            category_id=cat_id,
            match_count=0,
        )
    )
    winner.commit()
    winner.close()

    mapping_id = _upsert_category_mapping(
        loser, USER_ID, "Urbanclap Technologi", cat_id
    )
    loser.commit()
    assert mapping_id is not None

    check = SessionFactory()
    rows = (
        check.execute(
            select(CategoryMapping).where(
                CategoryMapping.user_id == USER_ID,
                CategoryMapping.description_pattern == "Urbanclap Technologi",
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].id == mapping_id
    check.close()
    loser.close()
    engine.dispose()
