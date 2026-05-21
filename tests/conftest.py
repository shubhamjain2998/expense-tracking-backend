"""Pytest fixtures for Expense Tracker backend.

Routine-friendly scaffold seeded 2026-05-22 alongside the first QA fixture.
Add app/client fixtures as the suite grows.
"""

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def april_regalia_pdf() -> Path:
    """HDFC Regalia April 2026 statement used in the 2026-05-22 QA session.

    Parses to 61 valid + 20 skipped rows. Use this to write regression tests
    for the upload pipeline (findings 2.4, 2.5, 2.6, 2.8).
    """
    path = FIXTURES_DIR / "april_regalia.pdf"
    assert path.exists(), f"Fixture missing: {path}"
    return path
