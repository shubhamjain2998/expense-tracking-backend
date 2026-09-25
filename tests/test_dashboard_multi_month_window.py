"""/dashboard/multi-month-summary must accept the Home trend's 15-month window.

The cap was ``le=12`` while the frontend offered 6/12/15. A 15-month request
got a 422, the query had no data, and the trend chart drew flat zeros.
"""

import os
import uuid

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

USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: USER_ID
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    session.close()
    engine.dispose()


@pytest.mark.parametrize("months", [6, 12, 15])
def test_trend_windows_are_accepted(client, months):
    r = client.get(
        "/dashboard/multi-month-summary",
        params={"end_year": 2026, "end_month": 9, "months": months},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == months
    assert (rows[-1]["year"], rows[-1]["month"]) == (2026, 9)


def test_fifteen_month_window_starts_fourteen_months_back(client):
    r = client.get(
        "/dashboard/multi-month-summary",
        params={"end_year": 2026, "end_month": 9, "months": 15},
    )
    rows = r.json()
    assert (rows[0]["year"], rows[0]["month"]) == (2025, 7)
