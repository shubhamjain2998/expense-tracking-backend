import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CategoryMapping, Person, ProcessedTransaction, RawTransaction
from app.schemas import (
    AutoCategoriseResponse,
    PatchProcessedTransactionRequest,
    ProcessedTransactionOut,
    ProcessTransactionRequest,
    RawTransactionOut,
)

router = APIRouter(prefix="/transactions", tags=["transactions"])


# ─── Review raw table ─────────────────────────────────────────────────────────


@router.get("/raw", response_model=List[RawTransactionOut])
def get_raw_transactions(
    month: Optional[int] = None,
    year: Optional[int] = None,
    db: Session = Depends(get_db),
):
    query = select(RawTransaction).where(RawTransaction.status == "pending")
    if year is not None:
        query = query.where(
            RawTransaction.txn_date.between(f"{year}-01-01", f"{year}-12-31 23:59:59")
        )
    if month is not None and year is not None:
        import calendar

        last_day = calendar.monthrange(year, month)[1]
        query = query.where(
            RawTransaction.txn_date.between(
                f"{year}-{month:02d}-01",
                f"{year}-{month:02d}-{last_day} 23:59:59",
            )
        )
    rows = db.execute(query).scalars().all()
    return rows


@router.delete("/raw/{id}", status_code=204)
def delete_raw_transaction(id: uuid.UUID, db: Session = Depends(get_db)):
    txn = db.get(RawTransaction, id)
    if txn is None:
        raise HTTPException(status_code=404, detail="Raw transaction not found")
    txn.status = "deleted"
    db.commit()


@router.patch("/raw/{id}/restore", response_model=RawTransactionOut)
def restore_raw_transaction(id: uuid.UUID, db: Session = Depends(get_db)):
    txn = db.get(RawTransaction, id)
    if txn is None:
        raise HTTPException(status_code=404, detail="Raw transaction not found")
    txn.status = "pending"
    db.commit()
    db.refresh(txn)
    return txn


# ─── Auto-categorise ─────────────────────────────────────────────────────────


@router.post("/auto-categorise", response_model=AutoCategoriseResponse)
def auto_categorise(db: Session = Depends(get_db)):
    pending = (
        db.execute(select(RawTransaction).where(RawTransaction.status == "pending"))
        .scalars()
        .all()
    )

    mappings = db.execute(select(CategoryMapping)).scalars().all()

    auto_categorised = 0

    for txn in pending:
        if not mappings:
            break

        best_score = 0
        best_mapping = None
        for mapping in mappings:
            score = fuzz.token_sort_ratio(txn.description, mapping.description_pattern)
            if score > best_score:
                best_score = score
                best_mapping = mapping

        if best_score >= 80 and best_mapping is not None:
            processed = ProcessedTransaction(
                raw_txn_id=txn.id,
                mapping_id=best_mapping.id,
                category=best_mapping.category,
                txn_date=(
                    txn.txn_date.date()
                    if hasattr(txn.txn_date, "date")
                    else txn.txn_date
                ),
                description=txn.description,
                amount=txn.amount,
                effective_amount=txn.amount,
                split_count=1,
                month=txn.txn_date.month,
                year=txn.txn_date.year,
            )
            db.add(processed)
            txn.status = "processed"
            best_mapping.match_count += 1
            best_mapping.last_used = datetime.now(timezone.utc)
            auto_categorised += 1

    db.commit()

    pending_manual = (
        db.execute(select(RawTransaction).where(RawTransaction.status == "pending"))
        .scalars()
        .count()
        if False
        else db.execute(
            select(RawTransaction).where(RawTransaction.status == "pending")
        )
        .scalars()
        .all()
        .__len__()
    )

    return AutoCategoriseResponse(
        auto_categorised=auto_categorised,
        pending_manual=pending_manual,
    )


# ─── Manual processing ────────────────────────────────────────────────────────


@router.get("/pending-manual", response_model=List[RawTransactionOut])
def get_pending_manual(db: Session = Depends(get_db)):
    rows = (
        db.execute(select(RawTransaction).where(RawTransaction.status == "pending"))
        .scalars()
        .all()
    )
    return rows


@router.post("/process", response_model=ProcessedTransactionOut)
def process_transaction(body: ProcessTransactionRequest, db: Session = Depends(get_db)):
    txn = db.get(RawTransaction, body.raw_txn_id)
    if txn is None:
        raise HTTPException(status_code=404, detail="Raw transaction not found")
    if txn.status == "processed":
        raise HTTPException(status_code=409, detail="Transaction already processed")

    split_count = max(body.split_count, 1)
    effective_amount = float(txn.amount) / split_count
    txn_date = txn.txn_date.date() if hasattr(txn.txn_date, "date") else txn.txn_date

    mapping_id = None
    if body.save_mapping:
        pattern = txn.description.strip()
        existing = db.execute(
            select(CategoryMapping).where(
                CategoryMapping.description_pattern == pattern
            )
        ).scalar_one_or_none()
        if existing:
            existing.category = body.category
            existing.last_used = datetime.now(timezone.utc)
            mapping_id = existing.id
        else:
            new_mapping = CategoryMapping(
                description_pattern=pattern,
                category=body.category,
                match_count=0,
                last_used=datetime.now(timezone.utc),
            )
            db.add(new_mapping)
            db.flush()
            mapping_id = new_mapping.id

    persons = []
    for pid in body.person_ids:
        person = db.get(Person, pid)
        if person is None:
            raise HTTPException(status_code=404, detail=f"Person {pid} not found")
        persons.append(person)

    processed = ProcessedTransaction(
        raw_txn_id=txn.id,
        mapping_id=mapping_id,
        category=body.category,
        txn_date=txn_date,
        description=txn.description,
        amount=txn.amount,
        effective_amount=effective_amount,
        split_count=split_count,
        month=txn_date.month,
        year=txn_date.year,
        persons=persons,
    )
    db.add(processed)
    txn.status = "processed"
    db.commit()
    db.refresh(processed)
    return processed


@router.patch("/processed/{id}", response_model=ProcessedTransactionOut)
def patch_processed_transaction(
    id: uuid.UUID,
    body: PatchProcessedTransactionRequest,
    db: Session = Depends(get_db),
):
    processed = db.get(ProcessedTransaction, id)
    if processed is None:
        raise HTTPException(status_code=404, detail="Processed transaction not found")

    if body.category is not None:
        processed.category = body.category

    if body.split_count is not None:
        split_count = max(body.split_count, 1)
        processed.split_count = split_count
        processed.effective_amount = float(processed.amount) / split_count

    if body.person_ids is not None:
        persons = []
        for pid in body.person_ids:
            person = db.get(Person, pid)
            if person is None:
                raise HTTPException(status_code=404, detail=f"Person {pid} not found")
            persons.append(person)
        processed.persons = persons

    if body.save_mapping and processed.mapping_id is not None:
        mapping = db.get(CategoryMapping, processed.mapping_id)
        if mapping:
            mapping.category = processed.category
            mapping.last_used = datetime.now(timezone.utc)

    db.commit()
    db.refresh(processed)
    return processed
