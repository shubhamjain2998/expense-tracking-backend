"""GET /budget/{year} returns rows in a stable order.

Postgres has no implicit row order: an UPDATE writes a new tuple, so without
an ORDER BY an edited budget row came back last and jumped to the end of the
Budget page. The endpoint now orders by creation time, then id.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

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
from app.models import BudgetPlan, Category  # noqa: E402

# Never all-digit-hex UUIDs in SQLite fixtures; uuid4() always has letters.
USER_ID = uuid.uuid4()


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


def test_budget_rows_come_back_in_creation_order_after_an_edit(client_and_db):
    client, session = client_and_db
    base = datetime(2026, 4, 1, tzinfo=timezone.utc)
    plans = []
    for i, name in enumerate(["housing", "bike", "family"]):
        cat = Category(id=uuid.uuid4(), user_id=USER_ID, name=name)
        session.add(cat)
        plan = BudgetPlan(
            id=uuid.uuid4(),
            user_id=USER_ID,
            year=2026,
            category_id=cat.id,
            allocated_amount=1200,
            created_at=base + timedelta(minutes=i),
            updated_at=base + timedelta(minutes=i),
        )
        session.add(plan)
        plans.append(plan)
    session.commit()

    # Edit the first row; it must not move.
    resp = client.put(f"/budget/{plans[0].id}", json={"allocated_amount": 2400})
    assert resp.status_code == 200

    rows = client.get("/budget/2026").json()
    assert [r["id"] for r in rows] == [str(p.id) for p in plans]
    assert float(rows[0]["allocated_amount"]) == 2400
