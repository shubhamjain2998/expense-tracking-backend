"""Encrypted-PDF handling: parse_bank_statement maps pdfminer encryption
errors into typed exceptions the router can turn into a 422 with a
machine-readable `code`, so the frontend can pop a password prompt
instead of a generic toast.

pdfminer's behavior with `pdfplumber.open(stream, password="")` against
an encrypted PDF is: it raises PDFPasswordIncorrect (not PDFEncryptionError)
in current versions — so the parser distinguishes "no password supplied"
from "wrong password" by checking whether the caller passed one.
"""

import os
from unittest.mock import patch

import pytest

# Minimal env so app.config loads without a live database
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SUPABASE_JWT_SECRET", "pytest-placeholder-secret")

from pdfminer.pdfdocument import (  # noqa: E402
    PDFEncryptionError,
    PDFPasswordIncorrect,
)

from app.services.pdf_parser import (  # noqa: E402
    PdfPasswordIncorrect,
    PdfPasswordRequired,
    parse_bank_statement,
)


# ── Unit tests on parse_bank_statement ────────────────────────────────────────


def test_pdfminer_password_incorrect_no_password_is_required():
    """Empty/missing password against encrypted PDF → PdfPasswordRequired.

    pdfminer raises PDFPasswordIncorrect even for the empty-password case,
    so the parser uses the absence of a caller-supplied password to
    distinguish "ask the user" from "they typed the wrong one".
    """
    with patch(
        "app.services.pdf_parser.pdfplumber.open",
        side_effect=PDFPasswordIncorrect(),
    ):
        with pytest.raises(PdfPasswordRequired):
            parse_bank_statement(b"%PDF-1.4 dummy")


def test_pdfminer_password_incorrect_with_password_is_incorrect():
    """Wrong password supplied → PdfPasswordIncorrect (caller distinguishable)."""
    with patch(
        "app.services.pdf_parser.pdfplumber.open",
        side_effect=PDFPasswordIncorrect(),
    ):
        with pytest.raises(PdfPasswordIncorrect):
            parse_bank_statement(b"%PDF-1.4 dummy", password="wrong")


def test_pdfminer_encryption_error_is_password_required():
    """Some pdfminer versions raise PDFEncryptionError instead — same intent."""
    with patch(
        "app.services.pdf_parser.pdfplumber.open",
        side_effect=PDFEncryptionError("encrypted"),
    ):
        with pytest.raises(PdfPasswordRequired):
            parse_bank_statement(b"%PDF-1.4 dummy")


# ── Router-level mapping to 422 with code ─────────────────────────────────────


def test_router_helper_returns_typed_422_for_required():
    """_parse_pdf_or_422 must surface code=pdf_password_required as a 422."""
    from fastapi import HTTPException

    from app.routers.uploads import _parse_pdf_or_422

    with patch(
        "app.routers.uploads.parse_bank_statement",
        side_effect=PdfPasswordRequired(),
    ):
        with pytest.raises(HTTPException) as exc_info:
            _parse_pdf_or_422(b"x", password=None)
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail["code"] == "pdf_password_required"


def test_router_helper_returns_typed_422_for_incorrect():
    """Same shape but code=pdf_password_incorrect so the UI inlines the error."""
    from fastapi import HTTPException

    from app.routers.uploads import _parse_pdf_or_422

    with patch(
        "app.routers.uploads.parse_bank_statement",
        side_effect=PdfPasswordIncorrect(),
    ):
        with pytest.raises(HTTPException) as exc_info:
            _parse_pdf_or_422(b"x", password="bad")
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail["code"] == "pdf_password_incorrect"
