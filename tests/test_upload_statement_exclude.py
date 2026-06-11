"""Tests for the ``exclude_indices`` parameter on ``POST /uploads/statement``.

Verifies that atomically excluding rows at import time results in only the
non-excluded rows being persisted — eliminating the non-atomic
import-all-then-delete-each post-processing pattern.
"""

import io
import os
import uuid
from decimal import Decimal
from typing import Optional
from unittest.mock import MagicMock, patch

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
from app.models import RawTransaction  # noqa: E402

# Random uuid4 — never all-digit-hex; see backend_sqlite_uuid_affinity_trap.md
USER_ID = uuid.uuid4()


# ── Fake parsed rows returned by the PDF parser mock ─────────────────────────


def _make_parse_result(num_rows: int = 3):
    """Return a mock parse result with ``num_rows`` synthetic rows."""
    from datetime import date

    rows = []
    for i in range(num_rows):
        row = MagicMock()
        row.txn_date = date(2026, 5, i + 1)
        row.description = f"Vendor {i}"
        row.amount = Decimal(str((i + 1) * 100))
        rows.append(row)

    result = MagicMock()
    result.rows = rows
    result.skipped = 0
    result.skipped_rows = []
    result.warnings = []
    return result


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
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


def _post_statement(client, *, exclude_indices: Optional[str] = None):
    """Upload a minimal fake PDF to ``/uploads/statement``."""
    fake_pdf = io.BytesIO(b"%PDF-1.4 fake content for testing")
    files = {"file": ("statement.pdf", fake_pdf, "application/pdf")}
    data = {}
    if exclude_indices is not None:
        data["exclude_indices"] = exclude_indices
    return client.post("/uploads/statement", files=files, data=data)


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_no_exclusions_inserts_all_rows(client_and_db):
    """Omitting exclude_indices must insert every parsed row (backward compat)."""
    client, session = client_and_db
    parse_result = _make_parse_result(num_rows=3)

    with patch("app.routers.uploads.parse_bank_statement", return_value=parse_result):
        resp = _post_statement(client)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["inserted"] == 3
    assert len(body["rows"]) == 3

    rows_in_db = session.query(RawTransaction).all()
    assert len(rows_in_db) == 3


def test_exclude_indices_skips_specified_rows(client_and_db):
    """Only non-excluded rows must be inserted into the database."""
    client, session = client_and_db
    parse_result = _make_parse_result(num_rows=3)

    with patch("app.routers.uploads.parse_bank_statement", return_value=parse_result):
        # Exclude index 0 (Vendor 0) and index 2 (Vendor 2) — only index 1 survives.
        resp = _post_statement(client, exclude_indices="0,2")

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["inserted"] == 1
    assert len(body["rows"]) == 1

    rows_in_db = session.query(RawTransaction).all()
    assert len(rows_in_db) == 1
    assert "Vendor 1" in rows_in_db[0].description


def test_exclude_all_rows_returns_empty_inserted(client_and_db):
    """Excluding every row must result in 0 inserted rows."""
    client, session = client_and_db
    parse_result = _make_parse_result(num_rows=3)

    with patch("app.routers.uploads.parse_bank_statement", return_value=parse_result):
        resp = _post_statement(client, exclude_indices="0,1,2")

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["inserted"] == 0
    assert body["rows"] == []

    rows_in_db = session.query(RawTransaction).all()
    assert len(rows_in_db) == 0


def test_exclude_indices_single_entry(client_and_db):
    """A single excluded index removes exactly that one row."""
    client, session = client_and_db
    parse_result = _make_parse_result(num_rows=3)

    with patch("app.routers.uploads.parse_bank_statement", return_value=parse_result):
        resp = _post_statement(client, exclude_indices="1")

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["inserted"] == 2

    descriptions = {r["description"] for r in body["rows"]}
    assert "Vendor 0" in descriptions
    assert "Vendor 2" in descriptions
    assert "Vendor 1" not in descriptions


def test_exclude_indices_empty_string_treated_as_no_exclusions(client_and_db):
    """An empty exclude_indices string must not exclude any rows."""
    client, session = client_and_db
    parse_result = _make_parse_result(num_rows=2)

    with patch("app.routers.uploads.parse_bank_statement", return_value=parse_result):
        resp = _post_statement(client, exclude_indices="")

    assert resp.status_code == 201, resp.text
    assert resp.json()["inserted"] == 2


def test_exclude_indices_invalid_value_returns_422(client_and_db):
    """Non-integer values in exclude_indices must be rejected with 422."""
    client, session = client_and_db
    parse_result = _make_parse_result(num_rows=2)

    with patch("app.routers.uploads.parse_bank_statement", return_value=parse_result):
        resp = _post_statement(client, exclude_indices="0,abc")

    assert resp.status_code == 422


def test_exclude_indices_out_of_range_are_silently_ignored(client_and_db):
    """An index beyond the row count must not raise — silently ignored."""
    client, session = client_and_db
    parse_result = _make_parse_result(num_rows=2)

    with patch("app.routers.uploads.parse_bank_statement", return_value=parse_result):
        # Index 99 is out of range but must not crash
        resp = _post_statement(client, exclude_indices="99")

    assert resp.status_code == 201, resp.text
    assert resp.json()["inserted"] == 2
