"""Indian financial year ↔ calendar year period helpers.

The DB always stores calendar values (`ProcessedTransaction.year` is the calendar
year of `txn_date`, `month` is 1-12 calendar). Period mode is a pure API/display
concern — it determines how the (year, month) query parameters are interpreted
and how full-year aggregations bound their date range.

Conventions
-----------
``period_year``
    In ``calendar`` mode this is the calendar year (2025 = Jan-Dec 2025).
    In ``fy`` mode this is the **fiscal-year start year** (2025 = FY 25-26 =
    Apr 2025 to Mar 2026).

``period_month`` (1-12)
    In ``calendar`` mode: 1 = January, …, 12 = December.
    In ``fy`` mode:        1 = April,    …, 12 = March (of the next year).
"""

from __future__ import annotations

from datetime import date
from typing import Literal, Tuple

PeriodMode = Literal["calendar", "fy"]


def period_year_range(period_year: int, mode: PeriodMode) -> Tuple[date, date]:
    """Inclusive first and last date covered by ``period_year`` in the given mode."""
    if mode == "fy":
        return date(period_year, 4, 1), date(period_year + 1, 3, 31)
    return date(period_year, 1, 1), date(period_year, 12, 31)


def resolve_period_month(
    period_year: int, period_month: int, mode: PeriodMode
) -> Tuple[int, int]:
    """Translate a (period_year, period_month) tuple to its calendar equivalent.

    Returns ``(calendar_year, calendar_month)``.
    """
    if not 1 <= period_month <= 12:
        raise ValueError(f"period_month must be 1-12, got {period_month}")
    if mode == "fy":
        # 1 -> April (4), 2 -> May (5), …, 9 -> December (12), 10 -> January (1),
        # 11 -> February (2), 12 -> March (3)
        calendar_month = ((period_month - 1) + 3) % 12 + 1
        calendar_year = period_year if calendar_month >= 4 else period_year + 1
        return calendar_year, calendar_month
    return period_year, period_month


def calendar_to_period_year(
    calendar_year: int, calendar_month: int, mode: PeriodMode
) -> int:
    """Which ``BudgetPlan.year`` does a given calendar month belong to?

    In FY mode, Jan-Mar belong to the previous fiscal year's start.
    """
    if mode == "fy":
        return calendar_year if calendar_month >= 4 else calendar_year - 1
    return calendar_year


def calendar_to_period_month(
    calendar_year: int, calendar_month: int, mode: PeriodMode
) -> int:
    """Inverse of ``resolve_period_month`` — used when bucketing trend data into
    a stable per-period order (1-12) for charts."""
    if mode == "fy":
        # April -> 1, May -> 2, …, March -> 12
        return ((calendar_month - 4) % 12) + 1
    return calendar_month
