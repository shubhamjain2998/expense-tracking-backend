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
            "schema_version": 2,
            "verdict": "You spent 12% more than usual on dining this quarter.",
            "metrics": [
                {
                    "id": "savings-rate",
                    "label": "Savings rate, last 3 months",
                    "value": 14,
                    "unit": "%",
                    "direction": "down",
                    "tone": "negative",
                    "detail": "Down from 23% over the 12 months before that.",
                }
            ],
            "findings": [
                {
                    "id": "dining-up",
                    "title": "Dining is trending up",
                    "severity": "warning",
                    "detail": "Dining rose from a median of ₹8,200/mo to ₹9,900/mo.",
                    "so_what": "At this rate dining costs ₹20,400 more over a year.",
                    "action": "Check whether delivery fees explain the jump.",
                    "annual_impact": 20400,
                    "confidence": "high",
                    "figure": {
                        "label": "Median dining/mo",
                        "value": 9900,
                        "unit": "INR",
                    },
                }
            ],
            "patterns": [
                {
                    "id": "post-credit-burst",
                    "title": "The days after salary lands cost the most",
                    "detail": "Discretionary spend runs 2.4x the daily average.",
                    "evidence": "₹3,180/day vs ₹1,320/day.",
                }
            ],
            "projection": {
                "label": "Projected spend, next month",
                "value": 84500,
                "unit": "INR",
                "basis": "Median of the last 6 months plus repriced commitments.",
            },
            "questions": ["Was the travel charge a one-off? It moves the projection."],
            "charts": [
                {
                    "id": "dining-trend",
                    "title": "Dining by month",
                    "type": "bar",
                    "unit": "INR",
                    "takeaway": "The rise starts in July, not in August.",
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
        assert body["schema_version"] == 2
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

    def test_rejects_finding_without_so_what(self, client_and_db):
        """v2's whole point: a finding must say what it costs or changes, not
        only what happened. Without ``so_what`` it is the observation the app
        could already make on its own."""
        client, session = client_and_db
        session.commit()

        bad = valid_payload()
        del bad["payload"]["findings"][0]["so_what"]
        r = client.post("/insights/runs", json=bad)
        assert r.status_code == 422

    def test_rejects_unknown_metric_tone(self, client_and_db):
        client, session = client_and_db
        session.commit()

        bad = valid_payload()
        bad["payload"]["metrics"][0]["tone"] = "catastrophic"
        r = client.post("/insights/runs", json=bad)
        assert r.status_code == 422

    def test_accepts_a_reply_with_no_optional_sections(self, client_and_db):
        """metrics/patterns/projection/questions are all optional — an LLM
        that returns only a verdict and findings still saves."""
        client, session = client_and_db
        session.commit()

        body = valid_payload()
        for key in ("metrics", "patterns", "projection", "questions", "charts"):
            del body["payload"][key]
        r = client.post("/insights/runs", json=body)
        assert r.status_code == 201
        stored = r.json()["payload"]
        assert stored["metrics"] == []
        assert stored["patterns"] == []
        assert stored["questions"] == []
        assert stored["projection"] is None

    def test_round_trips_the_interpretation_fields(self, client_and_db):
        client, session = client_and_db
        session.commit()

        client.post("/insights/runs", json=valid_payload())
        stored = client.get("/insights/runs/latest").json()["payload"]
        finding = stored["findings"][0]
        assert finding["so_what"].startswith("At this rate")
        assert finding["annual_impact"] == 20400
        assert finding["confidence"] == "high"
        assert stored["metrics"][0]["tone"] == "negative"
        assert stored["patterns"][0]["evidence"] == "₹3,180/day vs ₹1,320/day."
        assert stored["projection"]["value"] == 84500
        assert stored["charts"][0]["takeaway"].startswith("The rise starts")
        assert len(stored["questions"]) == 1

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

    def test_404_when_stored_run_predates_the_current_schema(self, client_and_db):
        """A v1 row can no longer be serialized through ``InsightsRunOut``.
        Reading it must read as "no run yet" so the user regenerates, never as
        a 500."""
        client, session = client_and_db
        stale = InsightsRun(
            id=uuid.uuid4(),
            user_id=USER_ID,
            schema_version=1,
            payload={
                "schema_version": 1,
                "verdict": "An older run, from before so_what existed.",
                "findings": [
                    {
                        "id": "old",
                        "title": "Old finding",
                        "severity": "info",
                        "detail": "No so_what on this one.",
                    }
                ],
                "charts": [],
            },
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
        )
        session.add(stale)
        session.commit()

        r = client.get("/insights/runs/latest")
        assert r.status_code == 404

        # …and saving a fresh run overwrites the stale row rather than adding one.
        assert client.post("/insights/runs", json=valid_payload()).status_code == 201
        rows = session.query(InsightsRun).filter(InsightsRun.user_id == USER_ID).all()
        assert len(rows) == 1

    def test_does_not_leak_across_users(self, client_and_db):
        client, session = client_and_db
        other_run = InsightsRun(
            id=uuid.uuid4(),
            user_id=OTHER_USER_ID,
            schema_version=2,
            payload={
                "schema_version": 2,
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
            schema_version=2,
            payload={
                "schema_version": 2,
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
