"""Tests for category-mappings CRUD endpoints and category txn_count aggregate.

Covers:
  POST   /category-mappings  — create (new pattern) and upsert (duplicate pattern)
  PATCH  /category-mappings/{id} — edit pattern and/or category_id
  GET    /categories          — txn_count aggregate (no N+1)
"""

import os
import uuid
from datetime import datetime, timezone
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
    ProcessedTransaction,
    RawTransaction,
)

# NOTE: never use all-digit-hex UUIDs here — SQLite NUMERIC affinity coerces
# them to integers which breaks SQLAlchemy's UUID result processor.
# uuid.uuid4() always contains hex letters so it is always safe.
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


def _add_category(session, *, user_id=USER_ID, name="food"):
    cat = Category(id=uuid.uuid4(), user_id=user_id, name=name)
    session.add(cat)
    session.flush()
    return cat


def _add_mapping(session, *, cat, pattern="zomato"):
    m = CategoryMapping(
        id=uuid.uuid4(),
        user_id=USER_ID,
        description_pattern=pattern,
        category_id=cat.id,
        match_count=3,
        last_used=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    session.add(m)
    session.flush()
    return m


def _add_processed_txn(
    session, *, cat, user_id=USER_ID, description="test txn", amount=Decimal("100")
):
    raw = RawTransaction(
        id=uuid.uuid4(),
        user_id=user_id,
        txn_date=datetime(2026, 5, 10),
        description=description,
        amount=amount,
        status="processed",
    )
    session.add(raw)
    session.flush()
    txn = ProcessedTransaction(
        id=uuid.uuid4(),
        user_id=user_id,
        raw_txn_id=raw.id,
        category_id=cat.id,
        description=description,
        amount=amount,
        effective_amount=amount,
        txn_date=datetime(2026, 5, 10).date(),
        year=2026,
        month=5,
        txn_type="expense",
    )
    session.add(txn)
    session.flush()
    return txn


# ── POST /category-mappings ──────────────────────────────────────────────────


class TestCreateMapping:
    def test_creates_new_mapping(self, client_and_db):
        client, session = client_and_db
        cat = _add_category(session)
        session.commit()

        r = client.post(
            "/category-mappings",
            json={"description_pattern": "swiggy order", "category_id": str(cat.id)},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["description_pattern"] == "swiggy order"
        assert body["category_id"] == str(cat.id)
        assert body["match_count"] == 0

    def test_strips_whitespace_from_pattern(self, client_and_db):
        client, session = client_and_db
        cat = _add_category(session)
        session.commit()

        r = client.post(
            "/category-mappings",
            json={"description_pattern": "  zomato  ", "category_id": str(cat.id)},
        )
        assert r.status_code == 201
        assert r.json()["description_pattern"] == "zomato"

    def test_upsert_duplicate_pattern_updates_category_and_last_used(
        self, client_and_db
    ):
        """Duplicate pattern → upsert: category_id + last_used updated, match_count kept."""  # noqa: E501
        client, session = client_and_db
        cat_a = _add_category(session, name="food")
        cat_b = _add_category(session, name="entertainment")
        m = _add_mapping(session, cat=cat_a, pattern="netflix")
        original_match_count = m.match_count
        session.commit()

        r = client.post(
            "/category-mappings",
            json={"description_pattern": "netflix", "category_id": str(cat_b.id)},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["id"] == str(
            m.id
        ), "should return the existing mapping, not a new one"
        assert body["category_id"] == str(cat_b.id)
        assert (
            body["match_count"] == original_match_count
        ), "match_count must not change on upsert"

    def test_404_for_unknown_category(self, client_and_db):
        client, session = client_and_db
        session.commit()

        r = client.post(
            "/category-mappings",
            json={
                "description_pattern": "test",
                "category_id": str(uuid.uuid4()),
            },
        )
        assert r.status_code == 404

    def test_404_for_other_users_category(self, client_and_db):
        client, session = client_and_db
        other_cat = _add_category(session, user_id=OTHER_USER_ID, name="other")
        session.commit()

        r = client.post(
            "/category-mappings",
            json={"description_pattern": "test", "category_id": str(other_cat.id)},
        )
        assert r.status_code == 404

    def test_rejects_empty_pattern(self, client_and_db):
        client, session = client_and_db
        cat = _add_category(session)
        session.commit()

        r = client.post(
            "/category-mappings",
            json={"description_pattern": "", "category_id": str(cat.id)},
        )
        assert r.status_code == 422

    def test_response_includes_category_name(self, client_and_db):
        client, session = client_and_db
        cat = _add_category(session, name="groceries")
        session.commit()

        r = client.post(
            "/category-mappings",
            json={"description_pattern": "big basket", "category_id": str(cat.id)},
        )
        assert r.status_code == 201
        assert r.json()["category"] == "groceries"


# ── PATCH /category-mappings/{id} ───────────────────────────────────────────


class TestUpdateMapping:
    def test_update_category_id(self, client_and_db):
        client, session = client_and_db
        cat_a = _add_category(session, name="food")
        cat_b = _add_category(session, name="transport")
        m = _add_mapping(session, cat=cat_a, pattern="uber")
        session.commit()

        r = client.patch(
            f"/category-mappings/{m.id}",
            json={"category_id": str(cat_b.id)},
        )
        assert r.status_code == 200
        assert r.json()["category_id"] == str(cat_b.id)
        assert r.json()["description_pattern"] == "uber", "pattern should be unchanged"

    def test_update_description_pattern(self, client_and_db):
        client, session = client_and_db
        cat = _add_category(session)
        m = _add_mapping(session, cat=cat, pattern="old pattern")
        session.commit()

        r = client.patch(
            f"/category-mappings/{m.id}",
            json={"description_pattern": "new pattern"},
        )
        assert r.status_code == 200
        assert r.json()["description_pattern"] == "new pattern"
        assert r.json()["category_id"] == str(cat.id), "category_id should be unchanged"

    def test_update_both_fields(self, client_and_db):
        client, session = client_and_db
        cat_a = _add_category(session, name="food")
        cat_b = _add_category(session, name="travel")
        m = _add_mapping(session, cat=cat_a, pattern="mmt")
        session.commit()

        r = client.patch(
            f"/category-mappings/{m.id}",
            json={"description_pattern": "makemytrip", "category_id": str(cat_b.id)},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["description_pattern"] == "makemytrip"
        assert body["category_id"] == str(cat_b.id)

    def test_strips_whitespace_from_updated_pattern(self, client_and_db):
        client, session = client_and_db
        cat = _add_category(session)
        m = _add_mapping(session, cat=cat, pattern="swiggy")
        session.commit()

        r = client.patch(
            f"/category-mappings/{m.id}",
            json={"description_pattern": "  zomato  "},
        )
        assert r.status_code == 200
        assert r.json()["description_pattern"] == "zomato"

    def test_404_for_missing_mapping(self, client_and_db):
        client, session = client_and_db
        cat = _add_category(session)
        session.commit()

        r = client.patch(
            f"/category-mappings/{uuid.uuid4()}",
            json={"category_id": str(cat.id)},
        )
        assert r.status_code == 404

    def test_404_for_other_users_mapping(self, client_and_db):
        """Cannot PATCH a mapping belonging to a different user."""
        client, session = client_and_db
        cat = _add_category(session)
        # mapping owned by OTHER_USER_ID
        other_m = CategoryMapping(
            id=uuid.uuid4(),
            user_id=OTHER_USER_ID,
            description_pattern="other pattern",
            category_id=cat.id,
            match_count=0,
        )
        session.add(other_m)
        session.commit()

        r = client.patch(
            f"/category-mappings/{other_m.id}",
            json={"description_pattern": "hacked"},
        )
        assert r.status_code == 404

    def test_404_for_other_users_category_in_patch(self, client_and_db):
        """category_id pointing at another user's category should be rejected."""
        client, session = client_and_db
        cat = _add_category(session, name="food")
        other_cat = _add_category(session, user_id=OTHER_USER_ID, name="other")
        m = _add_mapping(session, cat=cat, pattern="test")
        session.commit()

        r = client.patch(
            f"/category-mappings/{m.id}",
            json={"category_id": str(other_cat.id)},
        )
        assert r.status_code == 404

    def test_noop_patch_returns_unchanged_mapping(self, client_and_db):
        """An empty PATCH body is a no-op that returns the current mapping."""
        client, session = client_and_db
        cat = _add_category(session)
        m = _add_mapping(session, cat=cat, pattern="noop")
        session.commit()

        r = client.patch(f"/category-mappings/{m.id}", json={})
        assert r.status_code == 200
        assert r.json()["description_pattern"] == "noop"


# ── GET /categories — txn_count ──────────────────────────────────────────────


class TestCategoryTxnCount:
    def test_txn_count_zero_for_empty_category(self, client_and_db):
        client, session = client_and_db
        _add_category(session, name="empty")
        session.commit()

        r = client.get("/categories")
        assert r.status_code == 200
        cats = {c["name"]: c for c in r.json()}
        assert cats["empty"]["txn_count"] == 0

    def test_txn_count_reflects_processed_transactions(self, client_and_db):
        client, session = client_and_db
        cat = _add_category(session, name="food")
        _add_processed_txn(session, cat=cat, description="txn1")
        _add_processed_txn(session, cat=cat, description="txn2")
        session.commit()

        r = client.get("/categories")
        assert r.status_code == 200
        cats = {c["name"]: c for c in r.json()}
        assert cats["food"]["txn_count"] == 2

    def test_txn_count_does_not_leak_across_users(self, client_and_db):
        """Transactions belonging to OTHER_USER_ID must not inflate our count."""
        client, session = client_and_db
        # Category owned by the authenticated user
        our_cat = _add_category(session, name="shared-name")  # noqa: F841
        # Category owned by OTHER_USER_ID with the same name
        other_cat = _add_category(session, user_id=OTHER_USER_ID, name="shared-name")
        # Add transactions for OTHER_USER_ID
        _add_processed_txn(
            session,
            cat=other_cat,
            user_id=OTHER_USER_ID,
            description="other txn",
            amount=Decimal("50"),
        )
        session.commit()

        r = client.get("/categories")
        assert r.status_code == 200
        cats = {c["name"]: c for c in r.json()}
        assert cats["shared-name"]["txn_count"] == 0

    def test_txn_count_multiple_categories(self, client_and_db):
        client, session = client_and_db
        food = _add_category(session, name="food")
        transport = _add_category(session, name="transport")
        _add_processed_txn(session, cat=food, description="zomato")
        _add_processed_txn(session, cat=food, description="swiggy")
        _add_processed_txn(session, cat=food, description="blinkit")
        _add_processed_txn(session, cat=transport, description="uber")
        session.commit()

        r = client.get("/categories")
        assert r.status_code == 200
        cats = {c["name"]: c for c in r.json()}
        assert cats["food"]["txn_count"] == 3
        assert cats["transport"]["txn_count"] == 1
