"""LLM-generated insights runs — one per user.

The Insights page no longer derives numbers from an in-app formula. The user
copies a prompt built from their own data, runs it through an LLM of their
choice, and pastes the JSON reply back. That reply is validated against
``InsightsPayload`` (see app/schemas.py) and stored here — never as raw or
unvalidated JSON, and never evaluated.

A run replaces whatever the user had before (upsert on ``user_id``), matching
"the LLM replaces the page": there is exactly one insights run on file at a
time, so "fetch the latest" and "delete" both operate on that single row.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import InsightsRun
from app.schemas import INSIGHTS_SCHEMA_VERSION, InsightsRunCreate, InsightsRunOut

router = APIRouter(prefix="/insights", tags=["insights"])


@router.get("/runs/latest", response_model=InsightsRunOut)
def get_latest_run(
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    run = db.execute(
        select(InsightsRun).where(InsightsRun.user_id == user_id)
    ).scalar_one_or_none()
    # A run stored under an older schema version can no longer be serialized
    # through ``InsightsRunOut`` — it would fail validation on the way out and
    # 500 the page. Treat it as "no run yet" so the user simply regenerates;
    # the stale row is overwritten by the next save (upsert on ``user_id``).
    if run is None or run.schema_version != INSIGHTS_SCHEMA_VERSION:
        raise HTTPException(status_code=404, detail="No insights run yet")
    return run


@router.post("/runs", response_model=InsightsRunOut, status_code=201)
def create_run(
    body: InsightsRunCreate,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    if body.period_end < body.period_start:
        raise HTTPException(
            status_code=422, detail="period_end must not be before period_start"
        )

    existing = db.execute(
        select(InsightsRun).where(InsightsRun.user_id == user_id)
    ).scalar_one_or_none()

    # model_dump(mode="json") so Decimal/date-free primitives land in the JSON
    # column exactly as validated — not Python objects the JSON codec can't
    # serialize.
    payload_json = body.payload.model_dump(mode="json")

    if existing is not None:
        existing.schema_version = INSIGHTS_SCHEMA_VERSION
        existing.payload = payload_json
        existing.period_start = body.period_start
        existing.period_end = body.period_end
        existing.ran_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing)
        return existing

    run = InsightsRun(
        id=uuid.uuid4(),
        user_id=user_id,
        schema_version=INSIGHTS_SCHEMA_VERSION,
        payload=payload_json,
        period_start=body.period_start,
        period_end=body.period_end,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


@router.delete("/runs/latest", status_code=204)
def delete_latest_run(
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    run = db.execute(
        select(InsightsRun).where(InsightsRun.user_id == user_id)
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="No insights run yet")
    db.delete(run)
    db.commit()
