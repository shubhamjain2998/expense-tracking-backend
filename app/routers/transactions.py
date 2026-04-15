import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import (
    Category,
    CategoryMapping,
    Person,
    ProcessedTransaction,
    RawTransaction,
    Tag,
    TransactionPersonShare,
)
from app.schemas import (
    AutoCategoriseResponse,
    CreateRawTransactionRequest,
    PatchProcessedTransactionRequest,
    PatchShareSettledRequest,
    PersonShareIn,
    ProcessedTransactionOut,
    ProcessTransactionRequest,
    RawTransactionOut,
)

router = APIRouter(prefix="/transactions", tags=["transactions"])


def _compute_effective_amount(total: Decimal, shares: List[PersonShareIn]) -> Decimal:
    """User's net cost = total minus what other people owe."""
    others_total = Decimal("0")
    for s in shares:
        if s.share_type == "percentage":
            others_total += total * s.share_value / Decimal("100")
        else:
            others_total += s.share_value
    return total - others_total


def _build_share_records(
    processed_txn_id: uuid.UUID,
    total: Decimal,
    shares: List[PersonShareIn],
    user_id: uuid.UUID,
    db: Session,
) -> List[TransactionPersonShare]:
    records = []
    for s in shares:
        person = db.execute(
            select(Person).where(Person.id == s.person_id, Person.user_id == user_id)
        ).scalar_one_or_none()
        if person is None:
            raise HTTPException(
                status_code=404, detail=f"Person {s.person_id} not found"
            )
        if s.share_type == "percentage":
            share_amount = total * s.share_value / Decimal("100")
        else:
            share_amount = s.share_value
        records.append(
            TransactionPersonShare(
                processed_txn_id=processed_txn_id,
                person_id=s.person_id,
                share_type=s.share_type,
                share_value=float(s.share_value),
                share_amount=float(share_amount),
            )
        )
    return records


def _resolve_tags(
    tag_ids: List[uuid.UUID], user_id: uuid.UUID, db: Session
) -> List[Tag]:
    tags = []
    for tid in tag_ids:
        tag = db.execute(
            select(Tag).where(Tag.id == tid, Tag.user_id == user_id)
        ).scalar_one_or_none()
        if tag is None:
            raise HTTPException(status_code=404, detail=f"Tag {tid} not found")
        tags.append(tag)
    return tags


# ─── Raw transactions ─────────────────────────────────────────────────────────


@router.get("/processed", response_model=List[ProcessedTransactionOut])
def get_processed_transactions(
    year: int,
    month: Optional[int] = None,
    category_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    query = select(ProcessedTransaction).where(
        ProcessedTransaction.year == year,
        ProcessedTransaction.user_id == user_id,
    )
    if month is not None:
        query = query.where(ProcessedTransaction.month == month)
    if category_id is not None:
        query = query.where(ProcessedTransaction.category_id == category_id)
    query = query.order_by(ProcessedTransaction.txn_date)
    txns = db.execute(query).scalars().all()
    return [ProcessedTransactionOut.from_orm(t) for t in txns]


@router.get("/raw", response_model=List[RawTransactionOut])
def get_raw_transactions(
    month: Optional[int] = None,
    year: Optional[int] = None,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    query = select(RawTransaction).where(
        RawTransaction.status == "pending",
        RawTransaction.user_id == user_id,
    )
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


@router.post("/raw", response_model=RawTransactionOut, status_code=201)
def create_raw_transaction(
    body: CreateRawTransactionRequest,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
) -> RawTransactionOut:
    txn = RawTransaction(
        user_id=user_id,
        txn_date=body.txn_date,
        description=body.description,
        amount=body.amount,
        status="pending",
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return RawTransactionOut.model_validate(txn)


@router.delete("/raw/{id}", status_code=204)
def delete_raw_transaction(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    txn = db.execute(
        select(RawTransaction).where(
            RawTransaction.id == id, RawTransaction.user_id == user_id
        )
    ).scalar_one_or_none()
    if txn is None:
        raise HTTPException(status_code=404, detail="Raw transaction not found")
    txn.status = "deleted"
    db.commit()


@router.patch("/raw/{id}/restore", response_model=RawTransactionOut)
def restore_raw_transaction(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    txn = db.execute(
        select(RawTransaction).where(
            RawTransaction.id == id, RawTransaction.user_id == user_id
        )
    ).scalar_one_or_none()
    if txn is None:
        raise HTTPException(status_code=404, detail="Raw transaction not found")
    txn.status = "pending"
    db.commit()
    db.refresh(txn)
    return txn


# ─── Auto-categorise ─────────────────────────────────────────────────────────


@router.post("/auto-categorise", response_model=AutoCategoriseResponse)
def auto_categorise(
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    pending = (
        db.execute(
            select(RawTransaction).where(
                RawTransaction.status == "pending",
                RawTransaction.user_id == user_id,
            )
        )
        .scalars()
        .all()
    )

    mappings = (
        db.execute(select(CategoryMapping).where(CategoryMapping.user_id == user_id))
        .scalars()
        .all()
    )
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
                user_id=user_id,
                raw_txn_id=txn.id,
                mapping_id=best_mapping.id,
                category_id=best_mapping.category_id,
                txn_date=(
                    txn.txn_date.date()
                    if hasattr(txn.txn_date, "date")
                    else txn.txn_date
                ),
                description=txn.description,
                amount=txn.amount,
                effective_amount=txn.amount,
                month=txn.txn_date.month,
                year=txn.txn_date.year,
            )
            db.add(processed)
            txn.status = "processed"
            best_mapping.match_count += 1
            best_mapping.last_used = datetime.now(timezone.utc)
            auto_categorised += 1

    db.commit()

    pending_manual = len(
        db.execute(
            select(RawTransaction).where(
                RawTransaction.status == "pending",
                RawTransaction.user_id == user_id,
            )
        )
        .scalars()
        .all()
    )

    return AutoCategoriseResponse(
        auto_categorised=auto_categorised,
        pending_manual=pending_manual,
    )


# ─── Manual processing ────────────────────────────────────────────────────────


@router.get("/pending-manual", response_model=List[RawTransactionOut])
def get_pending_manual(
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    rows = (
        db.execute(
            select(RawTransaction).where(
                RawTransaction.status == "pending",
                RawTransaction.user_id == user_id,
            )
        )
        .scalars()
        .all()
    )
    return rows


@router.post("/process", response_model=ProcessedTransactionOut)
def process_transaction(
    body: ProcessTransactionRequest,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    txn = db.execute(
        select(RawTransaction).where(
            RawTransaction.id == body.raw_txn_id,
            RawTransaction.user_id == user_id,
        )
    ).scalar_one_or_none()
    if txn is None:
        raise HTTPException(status_code=404, detail="Raw transaction not found")
    if txn.status == "processed":
        raise HTTPException(status_code=409, detail="Transaction already processed")

    cat = db.execute(
        select(Category).where(
            Category.id == body.category_id, Category.user_id == user_id
        )
    ).scalar_one_or_none()
    if cat is None:
        raise HTTPException(
            status_code=404, detail=f"Category {body.category_id} not found"
        )

    total = Decimal(str(txn.amount))
    effective_amount = _compute_effective_amount(total, body.shares)
    txn_date = txn.txn_date.date() if hasattr(txn.txn_date, "date") else txn.txn_date

    mapping_id = None
    if body.save_mapping:
        pattern = txn.description.strip()
        existing = db.execute(
            select(CategoryMapping).where(
                CategoryMapping.description_pattern == pattern,
                CategoryMapping.user_id == user_id,
            )
        ).scalar_one_or_none()
        if existing:
            existing.category_id = body.category_id
            existing.last_used = datetime.now(timezone.utc)
            mapping_id = existing.id
        else:
            new_mapping = CategoryMapping(
                user_id=user_id,
                description_pattern=pattern,
                category_id=body.category_id,
                match_count=0,
                last_used=datetime.now(timezone.utc),
            )
            db.add(new_mapping)
            db.flush()
            mapping_id = new_mapping.id

    processed = ProcessedTransaction(
        user_id=user_id,
        raw_txn_id=txn.id,
        mapping_id=mapping_id,
        category_id=body.category_id,
        txn_date=txn_date,
        description=txn.description,
        amount=txn.amount,
        effective_amount=float(effective_amount),
        month=txn_date.month,
        year=txn_date.year,
        notes=body.notes,
    )
    db.add(processed)
    db.flush()

    for record in _build_share_records(processed.id, total, body.shares, user_id, db):
        db.add(record)

    txn.status = "processed"
    db.commit()
    db.refresh(processed)
    return ProcessedTransactionOut.from_orm(processed)


@router.patch(
    "/processed/{id}/shares/{person_id}", response_model=ProcessedTransactionOut
)
def patch_share_settled(
    id: uuid.UUID,
    person_id: uuid.UUID,
    body: PatchShareSettledRequest,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    # Verify the processed transaction belongs to this user
    processed = db.execute(
        select(ProcessedTransaction).where(
            ProcessedTransaction.id == id,
            ProcessedTransaction.user_id == user_id,
        )
    ).scalar_one_or_none()
    if processed is None:
        raise HTTPException(status_code=404, detail="Transaction not found")

    share = db.get(TransactionPersonShare, (id, person_id))
    if share is None:
        raise HTTPException(status_code=404, detail="Share not found")
    share.settled = body.settled
    db.commit()
    db.refresh(processed)
    return ProcessedTransactionOut.from_orm(processed)


@router.delete("/processed/{id}", status_code=204)
def delete_processed_transaction(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    processed = db.execute(
        select(ProcessedTransaction).where(
            ProcessedTransaction.id == id,
            ProcessedTransaction.user_id == user_id,
        )
    ).scalar_one_or_none()
    if processed is None:
        raise HTTPException(status_code=404, detail="Processed transaction not found")
    raw = db.get(RawTransaction, processed.raw_txn_id)
    if raw is not None:
        raw.status = "pending"
    db.delete(processed)
    db.commit()


@router.patch("/processed/{id}", response_model=ProcessedTransactionOut)
def patch_processed_transaction(
    id: uuid.UUID,
    body: PatchProcessedTransactionRequest,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    processed = db.execute(
        select(ProcessedTransaction).where(
            ProcessedTransaction.id == id,
            ProcessedTransaction.user_id == user_id,
        )
    ).scalar_one_or_none()
    if processed is None:
        raise HTTPException(status_code=404, detail="Processed transaction not found")

    if body.category_id is not None:
        cat = db.execute(
            select(Category).where(
                Category.id == body.category_id, Category.user_id == user_id
            )
        ).scalar_one_or_none()
        if cat is None:
            raise HTTPException(
                status_code=404, detail=f"Category {body.category_id} not found"
            )
        processed.category_id = body.category_id

    if body.description is not None:
        processed.description = body.description

    if body.notes is not None:
        processed.notes = body.notes

    if body.txn_date is not None:
        processed.txn_date = body.txn_date
        processed.month = body.txn_date.month
        processed.year = body.txn_date.year

    amount_changed = body.amount is not None and Decimal(str(body.amount)) != Decimal(
        str(processed.amount)
    )
    if amount_changed:
        processed.amount = float(body.amount)

    if body.shares is not None:
        processed.shares = []
        db.flush()
        total = Decimal(str(processed.amount))
        processed.effective_amount = float(
            _compute_effective_amount(total, body.shares)
        )
        for record in _build_share_records(
            processed.id, total, body.shares, user_id, db
        ):
            db.add(record)
    elif amount_changed:
        total = Decimal(str(processed.amount))
        for share in processed.shares:
            if share.share_type == "percentage":
                share.share_amount = float(
                    total * Decimal(str(share.share_value)) / Decimal("100")
                )
        others_total = sum(Decimal(str(s.share_amount)) for s in processed.shares)
        processed.effective_amount = float(total - others_total)

    if body.tag_ids is not None:
        processed.tags = _resolve_tags(body.tag_ids, user_id, db)

    if body.save_mapping and processed.mapping_id is not None:
        mapping = db.get(CategoryMapping, processed.mapping_id)
        if mapping:
            mapping.category_id = processed.category_id
            mapping.last_used = datetime.now(timezone.utc)

    db.commit()
    db.refresh(processed)
    return ProcessedTransactionOut.from_orm(processed)
