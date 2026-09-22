"""A category mapping is a whole rule: category + tags + split.

Before this, a mapping stored only ``category_id``. Tags and splits were
copied on the client, out of whatever the React Query cache happened to hold,
so a transaction picked them up only if a sibling row was already loaded in
the browser. These tests pin the behaviour to the server: whatever the rule
carries, every transaction it categorises gets — through auto-categorise,
through POST /process, and through a "save as rule" edit.
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
    CategoryMappingShare,
    Person,
    ProcessedTransaction,
    RawTransaction,
    Tag,
)

# Random uuid4 — never all-digit-hex; see backend_sqlite_uuid_affinity_trap.md
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


@pytest.fixture
def world(client_and_db):
    """A food category, two tags, one flatmate, and a 'swiggy order' rule
    carrying one tag and a 50% split."""
    client, session = client_and_db
    food = Category(id=uuid.uuid4(), user_id=USER_ID, name="food")
    eating_out = Tag(id=uuid.uuid4(), user_id=USER_ID, name="eating out")
    weekend = Tag(id=uuid.uuid4(), user_id=USER_ID, name="weekend")
    flatmate = Person(id=uuid.uuid4(), user_id=USER_ID, name="flatmate")
    session.add_all([food, eating_out, weekend, flatmate])
    session.flush()

    mapping = CategoryMapping(
        id=uuid.uuid4(),
        user_id=USER_ID,
        category_id=food.id,
        description_pattern="swiggy order",
        match_count=0,
    )
    session.add(mapping)
    session.flush()
    mapping.tags = [eating_out]
    session.add(
        CategoryMappingShare(
            mapping_id=mapping.id,
            person_id=flatmate.id,
            share_type="percentage",
            share_value=50,
        )
    )
    session.commit()
    return {
        "client": client,
        "session": session,
        "food": food,
        "eating_out": eating_out,
        "weekend": weekend,
        "flatmate": flatmate,
        "mapping": mapping,
    }


def _pending(session, description, amount="400.00", day=10):
    raw = RawTransaction(
        id=uuid.uuid4(),
        user_id=USER_ID,
        txn_date=datetime(2026, 5, day),
        description=description,
        amount=Decimal(amount),
        status="pending",
    )
    session.add(raw)
    session.commit()
    return raw


# ─── auto-categorise ─────────────────────────────────────────────────────────


def test_auto_categorise_applies_rule_tags_and_split(world):
    client, session = world["client"], world["session"]
    _pending(session, "swiggy order")

    r = client.post("/transactions/auto-categorise")
    assert r.status_code == 200
    assert r.json()["auto_categorised"] == 1

    processed = session.query(ProcessedTransaction).one()
    assert [t.name for t in processed.tags] == ["eating out"]
    assert len(processed.shares) == 1
    share = processed.shares[0]
    assert share.person_id == world["flatmate"].id
    assert Decimal(str(share.share_value)) == Decimal("50")
    # 50% of 400 is owed by the flatmate, so half the amount is the user's.
    assert Decimal(str(share.share_amount)) == Decimal("200")
    assert Decimal(str(processed.effective_amount)) == Decimal("200")
    assert Decimal(str(processed.amount)) == Decimal("400")


def test_auto_categorise_signs_rule_shares_for_income(world):
    """The split comes from the rule, so it must still pass through the sign
    convention — a refund's share is negative like its amount."""
    client, session = world["client"], world["session"]
    raw = _pending(session, "swiggy order")
    raw.txn_type = "refund"
    session.commit()

    client.post("/transactions/auto-categorise")

    processed = session.query(ProcessedTransaction).one()
    assert Decimal(str(processed.amount)) < 0
    assert Decimal(str(processed.effective_amount)) < 0
    assert Decimal(str(processed.shares[0].share_amount)) < 0


def test_auto_categorise_without_rule_context_is_unchanged(world):
    """A category-only rule still produces a plain row: no tags, no split,
    effective_amount equal to the amount."""
    client, session = world["client"], world["session"]
    plain = CategoryMapping(
        id=uuid.uuid4(),
        user_id=USER_ID,
        category_id=world["food"].id,
        description_pattern="metro recharge",
        match_count=0,
    )
    session.add(plain)
    session.commit()
    _pending(session, "metro recharge", amount="100.00", day=12)

    client.post("/transactions/auto-categorise")

    processed = (
        session.query(ProcessedTransaction)
        .filter(ProcessedTransaction.description == "metro recharge")
        .one()
    )
    assert processed.tags == []
    assert processed.shares == []
    assert Decimal(str(processed.effective_amount)) == Decimal("100")


def test_auto_categorise_links_mapping_id(world):
    client, session = world["client"], world["session"]
    _pending(session, "swiggy order")
    client.post("/transactions/auto-categorise")
    processed = session.query(ProcessedTransaction).one()
    assert processed.mapping_id == world["mapping"].id


# ─── save_mapping teaches the rule ───────────────────────────────────────────


def test_process_with_save_mapping_stores_tags_and_split(world):
    client, session = world["client"], world["session"]
    raw = _pending(session, "dunzo delivery")

    r = client.post(
        "/transactions/process",
        json={
            "raw_txn_id": str(raw.id),
            "category_id": str(world["food"].id),
            "save_mapping": True,
            "tag_ids": [str(world["weekend"].id)],
            "shares": [
                {
                    "person_id": str(world["flatmate"].id),
                    "share_type": "percentage",
                    "share_value": "25",
                }
            ],
        },
    )
    assert r.status_code == 200
    assert [t["name"] for t in r.json()["tags"]] == ["weekend"]

    mapping = (
        session.query(CategoryMapping)
        .filter(CategoryMapping.description_pattern == "dunzo delivery")
        .one()
    )
    assert [t.name for t in mapping.tags] == ["weekend"]
    assert len(mapping.shares) == 1
    assert Decimal(str(mapping.shares[0].share_value)) == Decimal("25")

    # And the next matching row inherits all of it.
    _pending(session, "dunzo delivery", amount="200.00", day=15)
    client.post("/transactions/auto-categorise")
    auto = (
        session.query(ProcessedTransaction)
        .filter(ProcessedTransaction.txn_date == datetime(2026, 5, 15).date())
        .one()
    )
    assert [t.name for t in auto.tags] == ["weekend"]
    assert Decimal(str(auto.effective_amount)) == Decimal("150")


def test_save_mapping_on_patch_rewrites_the_rule(world):
    """Editing a row and ticking "save as rule" updates all three facets."""
    client, session = world["client"], world["session"]
    _pending(session, "swiggy order")
    client.post("/transactions/auto-categorise")
    processed = session.query(ProcessedTransaction).one()

    r = client.patch(
        f"/transactions/processed/{processed.id}",
        json={
            "tag_ids": [str(world["weekend"].id)],
            "shares": [],
            "save_mapping": True,
        },
    )
    assert r.status_code == 200

    session.refresh(world["mapping"])
    assert [t.name for t in world["mapping"].tags] == ["weekend"]
    assert world["mapping"].shares == []


def test_patch_without_save_mapping_leaves_the_rule_alone(world):
    """A one-off correction on a single row must not rewrite the rule for
    every future transaction."""
    client, session = world["client"], world["session"]
    _pending(session, "swiggy order")
    client.post("/transactions/auto-categorise")
    processed = session.query(ProcessedTransaction).one()

    client.patch(
        f"/transactions/processed/{processed.id}",
        json={"tag_ids": [str(world["weekend"].id)]},
    )

    session.refresh(world["mapping"])
    assert [t.name for t in world["mapping"].tags] == ["eating out"]


# ─── CRUD on the rule itself ─────────────────────────────────────────────────


def test_mapping_crud_round_trips_tags_and_shares(world):
    client = world["client"]

    r = client.post(
        "/category-mappings",
        json={
            "description_pattern": "blinkit",
            "category_id": str(world["food"].id),
            "tag_ids": [str(world["eating_out"].id), str(world["weekend"].id)],
            "shares": [
                {
                    "person_id": str(world["flatmate"].id),
                    "share_type": "amount",
                    "share_value": "120",
                }
            ],
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert sorted(t["name"] for t in body["tags"]) == ["eating out", "weekend"]
    assert body["shares"][0]["person_name"] == "flatmate"
    assert body["shares"][0]["share_type"] == "amount"

    mapping_id = body["id"]
    r = client.patch(f"/category-mappings/{mapping_id}", json={"tag_ids": []})
    assert r.status_code == 200
    assert r.json()["tags"] == []
    # shares omitted from the patch means "leave alone", not "clear"
    assert len(r.json()["shares"]) == 1

    r = client.patch(f"/category-mappings/{mapping_id}", json={"shares": []})
    assert r.status_code == 200
    assert r.json()["shares"] == []

    assert client.get("/category-mappings").status_code == 200


def test_mapping_rejects_another_users_tag(world):
    client, session = world["client"], world["session"]
    foreign = Tag(id=uuid.uuid4(), user_id=OTHER_USER_ID, name="not yours")
    session.add(foreign)
    session.commit()

    r = client.post(
        "/category-mappings",
        json={
            "description_pattern": "leaky",
            "category_id": str(world["food"].id),
            "tag_ids": [str(foreign.id)],
        },
    )
    assert r.status_code == 404
    assert (
        session.query(CategoryMapping)
        .filter(CategoryMapping.description_pattern == "leaky")
        .count()
        == 0
    )


def test_mapping_rejects_another_users_person(world):
    client, session = world["client"], world["session"]
    foreign = Person(id=uuid.uuid4(), user_id=OTHER_USER_ID, name="stranger")
    session.add(foreign)
    session.commit()

    r = client.post(
        "/category-mappings",
        json={
            "description_pattern": "leaky2",
            "category_id": str(world["food"].id),
            "shares": [
                {
                    "person_id": str(foreign.id),
                    "share_type": "percentage",
                    "share_value": "50",
                }
            ],
        },
    )
    assert r.status_code == 404


def test_deleting_a_mapping_leaves_its_transactions_intact(world):
    """Rules are advice, not ownership: dropping one must not touch the rows
    it produced."""
    client, session = world["client"], world["session"]
    _pending(session, "swiggy order")
    client.post("/transactions/auto-categorise")
    processed = session.query(ProcessedTransaction).one()

    r = client.delete(f"/category-mappings/{world['mapping'].id}")
    assert r.status_code == 204

    session.expire_all()
    still = session.query(ProcessedTransaction).one()
    assert still.id == processed.id
    assert [t.name for t in still.tags] == ["eating out"]
    assert len(still.shares) == 1
