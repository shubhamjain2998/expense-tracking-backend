"""Regression tests for QA-B upload pipeline findings (2026-05-22 session).

Parser-level tests use the real april_regalia.pdf fixture for strong signal.
The duplicate-upload test (2.8) mocks the DB session directly to stay portable.
"""

import os
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

# Minimal env vars so app.config loads without a live database
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SUPABASE_JWT_SECRET", "pytest-placeholder-secret")

from app.services.normalizer import clean_description  # noqa: E402
from app.services.pdf_parser import parse_bank_statement  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def parsed_regalia(april_regalia_pdf):
    """Parse the fixture PDF once per module; all tests share the result."""
    return parse_bank_statement(april_regalia_pdf.read_bytes())


# ── Finding 2.4: skipped_rows length must equal skipped count ─────────────────


def test_preview_returns_all_skipped_rows(parsed_regalia):
    """Every skipped row must have a corresponding entry in skipped_rows."""
    result = parsed_regalia
    assert len(result.skipped_rows) == result.skipped, (
        f"skipped={result.skipped} but skipped_rows has {len(result.skipped_rows)} entries"
    )


# ── Finding 2.5: no description should end with "(Ref#" ──────────────────────


def test_no_description_ends_with_ref_hash(parsed_regalia):
    """Incomplete trailing parens must be stripped by clean_description."""
    for row in parsed_regalia.rows:
        desc = clean_description(row.description)
        assert not desc.rstrip().endswith("(Ref#"), (
            f"Truncated description still present: {desc!r}"
        )


# ── Finding 2.6: preview and import descriptions must be identical ────────────


def test_preview_import_description_parity(parsed_regalia):
    """clean_description must be idempotent so one-time application is stable."""
    for row in parsed_regalia.rows:
        once = clean_description(row.description)
        twice = clean_description(once)
        assert once == twice, (
            f"clean_description not idempotent: {row.description!r} → {once!r} → {twice!r}"
        )


# ── Finding 2.8: duplicate upload must return 409, not 500 ───────────────────


def _make_db_mock(has_active_transaction: bool) -> MagicMock:
    """Return a mock SQLAlchemy session for _check_duplicate unit tests."""
    db = MagicMock()

    existing_upload = MagicMock()
    existing_upload.id = uuid.uuid4()

    # First db.execute → select(UploadedFile) → .scalar_one_or_none()
    first_result = MagicMock()
    first_result.scalar_one_or_none.return_value = existing_upload

    # Second db.execute → select(RawTransaction) → .scalars().first()
    active_row = MagicMock() if has_active_transaction else None
    second_scalars = MagicMock()
    second_scalars.first.return_value = active_row
    second_result = MagicMock()
    second_result.scalars.return_value = second_scalars

    db.execute.side_effect = [first_result, second_result]
    return db


def test_duplicate_upload_raises_409():
    """Re-uploading a PDF with active transactions must raise HTTP 409."""
    from app.routers.uploads import _check_duplicate

    db = _make_db_mock(has_active_transaction=True)
    with pytest.raises(HTTPException) as exc_info:
        _check_duplicate("abc123hash", uuid.uuid4(), db)
    assert exc_info.value.status_code == 409


def test_duplicate_upload_allows_reimport_when_deleted():
    """Re-uploading after all transactions were deleted must NOT raise."""
    from app.routers.uploads import _check_duplicate

    db = _make_db_mock(has_active_transaction=False)
    _check_duplicate("abc123hash", uuid.uuid4(), db)  # must not raise
    db.delete.assert_called_once()
