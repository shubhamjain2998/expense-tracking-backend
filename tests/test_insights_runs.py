"""Tests for the LLM-generated insights run endpoints.

Covers:
  POST   /insights/runs         — create (first run) and upsert (replace)
  GET    /insights/runs/latest  — 404 with no run, else the stored run
  DELETE /insights/runs/latest  — 404 with no run, else deletes it

The payload validated here is untrusted, hand-pasted JSON from an LLM, so
malformed shapes (wrong severity, wrong chart type, wrong schema_version,
missing fields) must be rejected with 422 rather than stored.
"""

import os
import uuid
from datetime import date

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
from app.models import InsightsRun  # noqa: E402

USER_ID = uuid.uuid4()
OTHER_USER_ID = uuid.uuid4()


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


def valid_payload(**overrides):
    body = {
        "period_start": "2025-06-01",
        "period_end": "2026-08-31",
        "payload": {
            "schema_version": 1,
            "verdict": "You spent 12% more than usual on dining this quarter.",
            "findings": [
                {
                    "id": "dining-up",
                    "title": "Dining is trending up",
                    "severity": "warning",
                    "detail": "Dining rose from a median of ₹8,200/mo to ₹9,900/mo.",
                    "figure": {
                        "label": "Median dining/mo",
                        "value": 9900,
                        "unit": "INR",
                    },
                }
            ],
            "charts": [
                {
                    "id": "dining-trend",
                    "title": "Dining by month",
                    "type": "bar",
                    "unit": "INR",
                    "series": [
                        {
                            "name": "Dining",
                            "data": [
                                {"label": "Jun", "value": 8200},
                                {"label": "Jul", "value": 8600},
                                {"label": "Aug", "value": 9900},
                            ],
                        }
                    ],
                }
            ],
        },
    }
    body.update(overrides)
    return body


# ── POST /insights/runs ──────────────────────────────────────────────────────


class TestCreateRun:
    def test_creates_first_run(self, client_and_db):
        client, session = client_and_db
        session.commit()

        r = client.post("/insights/runs", json=valid_payload())
        assert r.status_code == 201
        body = r.json()
        assert body["schema_version"] == 1
        assert body["payload"]["verdict"].startswith("You spent")
        assert body["period_start"] == "2025-06-01"
        assert "ran_at" in body and body["ran_at"]

    def test_second_run_replaces_the_first_not_appends(self, client_and_db):
        client, session = client_and_db
        session.commit()

        client.post("/insights/runs", json=valid_payload())
        r2 = client.post(
            "/insights/runs",
            json=valid_payload(
                payload={
                    **valid_payload()["payload"],
                    "verdict": "Second run verdict.",
                }
            ),
        )
        assert r2.status_code == 201
        assert r2.json()["payload"]["verdict"] == "Second run verdict."

        rows = session.query(InsightsRun).filter(InsightsRun.user_id == USER_ID).all()
        assert len(rows) == 1, "a re-run must upsert, never append a second row"

    def test_rejects_wrong_schema_version(self, client_and_db):
        client, session = client_and_db
        session.commit()

        bad = valid_payload()
        bad["payload"]["schema_version"] = 99
        r = client.post("/insights/runs", json=bad)
        assert r.status_code == 422

    def test_rejects_unknown_severity(self, client_and_db):
        client, session = client_and_db
        session.commit()

        bad = valid_payload()
        bad["payload"]["findings"][0]["severity"] = "catastrophic"
        r = client.post("/insights/runs", json=bad)
        assert r.status_code == 422

    def test_rejects_unknown_chart_type(self, client_and_db):
        client, session = client_and_db
        session.commit()

        bad = valid_payload()
        bad["payload"]["charts"][0]["type"] = "donut"
        r = client.post("/insights/runs", json=bad)
        assert r.status_code == 422

    def test_rejects_empty_findings(self, client_and_db):
        client, session = client_and_db
        session.commit()

        bad = valid_payload()
        bad["payload"]["findings"] = []
        r = client.post("/insights/runs", json=bad)
        assert r.status_code == 422

    def test_rejects_missing_verdict(self, client_and_db):
        client, session = client_and_db
        session.commit()

        bad = valid_payload()
        del bad["payload"]["verdict"]
        r = client.post("/insights/runs", json=bad)
        assert r.status_code == 422

    def test_rejects_period_end_before_period_start(self, client_and_db):
        client, session = client_and_db
        session.commit()

        bad = valid_payload(period_start="2026-08-01", period_end="2026-01-01")
        r = client.post("/insights/runs", json=bad)
        assert r.status_code == 422

    def test_charts_are_optional(self, client_and_db):
        client, session = client_and_db
        session.commit()

        body = valid_payload()
        body["payload"]["charts"] = []
        r = client.post("/insights/runs", json=body)
        assert r.status_code == 201
        assert r.json()["payload"]["charts"] == []


# ── GET /insights/runs/latest ────────────────────────────────────────────────


class TestGetLatestRun:
    def test_404_when_no_run(self, client_and_db):
        client, session = client_and_db
        session.commit()

        r = client.get("/insights/runs/latest")
        assert r.status_code == 404

    def test_returns_stored_run(self, client_and_db):
        client, session = client_and_db
        session.commit()

        client.post("/insights/runs", json=valid_payload())
        r = client.get("/insights/runs/latest")
        assert r.status_code == 200
        assert r.json()["payload"]["verdict"].startswith("You spent")

    def test_does_not_leak_across_users(self, client_and_db):
        client, session = client_and_db
        other_run = InsightsRun(
            id=uuid.uuid4(),
            user_id=OTHER_USER_ID,
            schema_version=1,
            payload={
                "schema_version": 1,
                "verdict": "other",
                "findings": [],
                "charts": [],
            },
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
        )
        session.add(other_run)
        session.commit()

        r = client.get("/insights/runs/latest")
        assert r.status_code == 404


# ── DELETE /insights/runs/latest ─────────────────────────────────────────────


class TestDeleteLatestRun:
    def test_404_when_no_run(self, client_and_db):
        client, session = client_and_db
        session.commit()

        r = client.delete("/insights/runs/latest")
        assert r.status_code == 404

    def test_deletes_stored_run(self, client_and_db):
        client, session = client_and_db
        session.commit()

        client.post("/insights/runs", json=valid_payload())
        r = client.delete("/insights/runs/latest")
        assert r.status_code == 204

        r2 = client.get("/insights/runs/latest")
        assert r2.status_code == 404

    def test_does_not_delete_other_users_run(self, client_and_db):
        client, session = client_and_db
        other_run = InsightsRun(
            id=uuid.uuid4(),
            user_id=OTHER_USER_ID,
            schema_version=1,
            payload={
                "schema_version": 1,
                "verdict": "other",
                "findings": [],
                "charts": [],
            },
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
        )
        session.add(other_run)
        session.commit()

        r = client.delete("/insights/runs/latest")
        assert r.status_code == 404

        remaining = (
            session.query(InsightsRun)
            .filter(InsightsRun.user_id == OTHER_USER_ID)
            .all()
        )
        assert len(remaining) == 1
