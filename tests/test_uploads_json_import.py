"""Tests for ``POST /uploads/json-import`` — the bulk-paste endpoint.

The endpoint must mirror ``/uploads/text-import`` so the bulk-paste flow has
strict parity with the PDF/text paths: signed amounts preserved, content-hash
409 on re-import, ``UploadedFile`` audit row recorded, ``status='pending'``,
``clean_description`` applied.
"""

import os
import uuid
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
from app.models import RawTransaction, UploadedFile  # noqa: E402

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


SAMPLE_ROWS = [
    {
        "txn_date": "2026-05-25T00:00:00",
        "description": "Raz*swiggy Bangalore I",
        "amount": "1092.00",
    },
    {
        "txn_date": "2026-05-21T00:00:00",
        "description": "Credit Card Payment Net Banking",
        "amount": "-17773.00",
    },
    {
        "txn_date": "2026-05-20T00:00:00",
        "description": "Blinkit Bangalore I",
        "amount": "386.00",
    },
]


def test_happy_path_inserts_rows_with_signed_amounts(client_and_db):
    client, session = client_and_db

    resp = client.post("/uploads/json-import", json={"rows": SAMPLE_ROWS})

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["inserted"] == 3
    assert body["skipped"] == 0
    assert body["skipped_rows"] == []
    assert len(body["rows"]) == 3

    raws = session.query(RawTransaction).order_by(RawTransaction.amount).all()
    amounts = sorted(Decimal(str(r.amount)) for r in raws)
    assert amounts == [Decimal("-17773.00"), Decimal("386.00"), Decimal("1092.00")]
    assert all(r.status == "pending" for r in raws)
    assert all(r.upload_id is not None for r in raws)


def test_creates_uploaded_file_audit_row(client_and_db):
    client, session = client_and_db
    resp = client.post("/uploads/json-import", json={"rows": SAMPLE_ROWS})
    assert resp.status_code == 201

    uploads = session.query(UploadedFile).all()
    assert len(uploads) == 1
    assert uploads[0].source_type == "bulk_paste"
    assert uploads[0].filename is None
    assert uploads[0].content_hash  # non-empty


def test_duplicate_repaste_returns_409(client_and_db):
    client, _ = client_and_db
    first = client.post("/uploads/json-import", json={"rows": SAMPLE_ROWS})
    assert first.status_code == 201

    second = client.post("/uploads/json-import", json={"rows": SAMPLE_ROWS})
    assert second.status_code == 409


def test_reorder_still_collides_via_canonical_hash(client_and_db):
    """Row ordering must not let a user re-import the same data."""
    client, _ = client_and_db
    first = client.post("/uploads/json-import", json={"rows": SAMPLE_ROWS})
    assert first.status_code == 201

    reversed_rows = list(reversed(SAMPLE_ROWS))
    second = client.post("/uploads/json-import", json={"rows": reversed_rows})
    assert second.status_code == 409


def test_empty_rows_rejected(client_and_db):
    client, _ = client_and_db
    resp = client.post("/uploads/json-import", json={"rows": []})
    assert resp.status_code == 422


def test_zero_amount_rejected(client_and_db):
    client, _ = client_and_db
    resp = client.post(
        "/uploads/json-import",
        json={
            "rows": [
                {
                    "txn_date": "2026-05-20T00:00:00",
                    "description": "bogus",
                    "amount": "0",
                }
            ]
        },
    )
    assert resp.status_code == 422


def test_description_is_cleaned(client_and_db):
    """``clean_description`` should run server-side — matches PDF flow."""
    client, session = client_and_db
    resp = client.post(
        "/uploads/json-import",
        json={
            "rows": [
                {
                    "txn_date": "2026-05-20T00:00:00",
                    "description": "  Blinkit   Bangalore  ",
                    "amount": "386.00",
                }
            ]
        },
    )
    assert resp.status_code == 201
    raw = session.query(RawTransaction).one()
    # Whitespace collapsed by clean_description
    assert "  " not in raw.description
