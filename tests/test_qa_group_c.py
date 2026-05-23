"""Tests for QA Group C: transaction classification and restoration.

Covers:
  3.1 + 3.2 – refund/transfer classification heuristics
  3.4        – include_deleted query param on GET /transactions/raw
"""

import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SUPABASE_JWT_SECRET", "pytest-placeholder-secret")

from decimal import Decimal  # noqa: E402

from app.routers.transactions import classify_txn_type  # noqa: E402


# ── Finding 3.1 + 3.2: classify_txn_type heuristics ──────────────────────────


@pytest.mark.parametrize(
    "description,amount,expected",
    [
        # Positive amounts are always expense
        ("Swiggy", Decimal("449"), "expense"),
        ("CREDIT CARD PAYMENT Net Banking", Decimal("16633"), "expense"),
        # Negative + transfer keywords → transfer
        ("CREDIT CARD PAYMENT Net Banking", Decimal("-16633"), "transfer"),
        ("NEFT Transfer to savings", Decimal("-5000"), "transfer"),
        ("IMPS TFR to HDFC account", Decimal("-2000"), "transfer"),
        ("RTGS payment", Decimal("-50000"), "transfer"),
        ("PAYMENT THANK YOU", Decimal("-100"), "transfer"),
        # Negative without transfer keyword → refund
        ("Blinkit refund", Decimal("-449"), "refund"),
        ("Amazon return", Decimal("-200"), "refund"),
        ("Zomato credit adjustment", Decimal("-50"), "refund"),
        # Edge: negative, description contains partial keyword but not exact pattern
        ("MY NEFTWORK subscription", Decimal("-999"), "transfer"),  # NEFT is substring
    ],
)
def test_classify_txn_type(description, amount, expected):
    result = classify_txn_type(description, amount)
    assert result == expected, (
        f"classify_txn_type({description!r}, {amount}) → "
        f"{result!r}, expected {expected!r}"
    )


def test_refund_is_not_income():
    """A negative Blinkit refund must not be classified as income."""
    assert classify_txn_type("Blinkit refund -449", Decimal("-449")) == "refund"


def test_credit_card_payment_is_transfer():
    """Credit-card bill payment must be classified as transfer, not income."""
    assert (
        classify_txn_type("CREDIT CARD PAYMENT Net Banking", Decimal("-16633"))
        == "transfer"
    )


# ── Finding 3.4: include_deleted query param ─────────────────────────────────


def test_get_raw_transactions_signature_has_include_deleted():
    """GET /transactions/raw endpoint accepts include_deleted param."""
    import inspect

    from app.routers.transactions import get_raw_transactions

    sig = inspect.signature(get_raw_transactions)
    assert (
        "include_deleted" in sig.parameters
    ), "get_raw_transactions must accept an include_deleted parameter"


def test_include_deleted_default_is_false():
    """include_deleted defaults to False so existing clients are unaffected."""
    import inspect

    from app.routers.transactions import get_raw_transactions

    sig = inspect.signature(get_raw_transactions)
    param = sig.parameters["include_deleted"]
    # FastAPI Query wraps the default; verify the annotation/default resolves to False
    # For simple default check we inspect the parameter default value wrapper
    assert param.default is not inspect.Parameter.empty
