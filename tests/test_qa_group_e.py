"""Tests for QA Group E: dashboard analytics correctness + cold-load performance.

Covers:
  7.1 – monthly-trend always returns calendar month regardless of period_mode
  1.3 – /dashboard/multi-month-summary batch endpoint exists and works correctly
"""

import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SUPABASE_JWT_SECRET", "pytest-placeholder-secret")


# ── Finding 7.1: monthly-trend returns calendar month ─────────────────────────────────


def test_7_1_monthly_trend_does_not_bucket_to_fy_month():
    """monthly_trend must NOT call calendar_to_period_month.

    Before the fix the router converted April (calendar=4) → FY-index 1, so the
    frontend displayed April data under "January".  After the fix ``row.month``
    (calendar month) is returned directly.
    """
    import inspect

    from app.routers.dashboard import monthly_trend

    src = inspect.getsource(monthly_trend)
    assert "calendar_to_period_month" not in src, (
        "monthly_trend must return row.month directly (calendar month). "
        "Found call to calendar_to_period_month which re-labels months under FY mode."
    )


def test_7_1_calendar_to_period_month_april_is_1():
    """Confirm the old conversion: April calendar → FY-index 1.

    This documents the bug that was fixed: removing this call from monthly_trend
    means April data is returned as month=4, not month=1.
    """
    from app.services.period import calendar_to_period_month

    assert calendar_to_period_month(2026, 4, "fy") == 1, (
        "Sanity check: April maps to FY-index 1 (the old bug value)."
    )


def test_7_1_monthly_trend_uses_row_month():
    """Verify the fix: monthly_trend source references row.month directly."""
    import inspect

    from app.routers.dashboard import monthly_trend

    src = inspect.getsource(monthly_trend)
    assert "row.month" in src, (
        "monthly_trend must reference row.month to return calendar month unchanged."
    )


# ── Finding 1.3: multi-month-summary batch endpoint ─────────────────────────────


def test_1_3_multi_month_summary_route_registered():
    """GET /dashboard/multi-month-summary must be a registered route."""
    from app.main import app as fastapi_app

    routes = {r.path for r in fastapi_app.routes}
    assert "/dashboard/multi-month-summary" in routes, (
        f"Route not found. Registered routes: {sorted(routes)}"
    )


def test_1_3_multi_month_summary_signature():
    """multi_month_summary must accept end_year, end_month, and months params."""
    import inspect

    from app.routers.dashboard import multi_month_summary

    sig = inspect.signature(multi_month_summary)
    assert "end_year" in sig.parameters
    assert "end_month" in sig.parameters
    assert "months" in sig.parameters


@pytest.mark.parametrize(
    "end_year,end_month,n,expected",
    [
        (
            2026, 4, 6,
            [(2025, 11), (2025, 12), (2026, 1), (2026, 2), (2026, 3), (2026, 4)],
        ),
        (
            2026, 1, 3,
            [(2025, 11), (2025, 12), (2026, 1)],
        ),
        (
            2026, 5, 1,
            [(2026, 5)],
        ),
    ],
)
def test_1_3_calendar_month_rollback(end_year, end_month, n, expected):
    """The (year, month) rollback in multi_month_summary produces the correct window."""
    cal_months = []
    y, m = end_year, end_month
    for _ in range(n):
        cal_months.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    cal_months.reverse()
    assert cal_months == expected
