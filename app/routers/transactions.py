"""Raw-transaction review, auto-categorisation, and manual processing.

Raw rows are soft-deletable holding-area entries; processed rows are the
analytics-ready table read by ``dashboard``. Auto-categorise promotes raw rows
to processed when a learned category mapping matches their description at
``RapidFuzz.token_sort_ratio`` ≥ 80.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from rapidfuzz import fuzz
from sqlalchemy import delete, select

from app.services.normalizer import normalize_description
from app.services.period import (
    PeriodMode,
    period_year_range,
    resolve_period_month,
)
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
    transaction_tags,
)
from app.schemas import (
    AutoCategoriseRequest,
    AutoCategoriseResponse,
    BulkTagRequest,
    CreateRawTransactionRequest,
    PatchProcessedTransactionRequest,
    PatchShareSettledRequest,
    PersonShareIn,
    ProcessedTransactionOut,
    ProcessTransactionRequest,
    RawTransactionOut,
)

router = APIRouter(prefix="/transactions", tags=["transactions"])

# Soft-deleted raw transactions are permanently purged after this many days.
# Mirrors the trash-retention conventions in Gmail / iCloud — long enough to
# undo accidents, short enough to bound storage.
SOFT_DELETE_RETENTION_DAYS = 30


def _purge_expired_soft_deletes(db: Session, user_id: uuid.UUID) -> int:
    """Hard-delete raw transactions that have been soft-deleted past the
    retention window. Returns the count purged. Caller is responsible for
    committing.

    Safe because ``delete_processed_transaction`` always hard-deletes the
    processed row before soft-deleting its raw, so an expired soft-deleted
    raw has no live processed reference. The defensive subquery filter on
    ``raw_txn_id`` keeps that invariant explicit instead of implicit.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=SOFT_DELETE_RETENTION_DAYS)
    live_raw_ids = select(ProcessedTransaction.raw_txn_id).where(
        ProcessedTransaction.user_id == user_id
    )
    result = db.execute(
        delete(RawTransaction).where(
            RawTransaction.user_id == user_id,
            RawTransaction.status == "deleted",
            RawTransaction.deleted_at.is_not(None),
            RawTransaction.deleted_at < cutoff,
            RawTransaction.id.not_in(live_raw_ids),
        )
    )
    return result.rowcount or 0


_TRANSFER_PATTERNS = (
    "CREDIT CARD PAYMENT",
    "PAYMENT THANK",
    "NEFT",
    "IMPS TFR",
    "RTGS",
)


def classify_txn_type(description: str, amount: Decimal) -> str:
    """Classify into expense/income/refund/transfer from amount and description."""
    if amount > 0:
        return "expense"
    desc_upper = description.upper()
    if any(p in desc_upper for p in _TRANSFER_PATTERNS):
        return "transfer"
    return "refund"


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


# ─── Raw transactions ─────────────────────────────────────────────────────────────────


@router.get("/processed", response_model=List[ProcessedTransactionOut])
def get_processed_transactions(
    year: Optional[int] = None,
    month: Optional[int] = None,
    category_id: Optional[uuid.UUID] = None,
    tag_id: Optional[uuid.UUID] = None,
    period_mode: PeriodMode = Query("calendar"),
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    query = select(ProcessedTransaction).where(ProcessedTransaction.user_id == user_id)

    if year is not None and month is not None:
        cal_year, cal_month = resolve_period_month(year, month, period_mode)
        query = query.where(
            ProcessedTransaction.year == cal_year,
            ProcessedTransaction.month == cal_month,
        )
    elif year is not None:
        start, end = period_year_range(year, period_mode)
        query = query.where(
            ProcessedTransaction.txn_date >= start,
            ProcessedTransaction.txn_date <= end,
        )
    elif month is not None:
        # Month without year is ambiguous in fy mode; treat as calendar month.
        query = query.where(ProcessedTransaction.month == month)

    if category_id is not None:
        query = query.where(ProcessedTransaction.category_id == category_id)
    if tag_id is not None:
        query = query.where(
            ProcessedTransaction.id.in_(
                select(transaction_tags.c.processed_txn_id).where(
                    transaction_tags.c.tag_id == tag_id
                )
            )
        )
    query = query.order_by(ProcessedTransaction.txn_date)
    txns = db.execute(query).scalars().all()
    return [ProcessedTransactionOut.from_orm(t) for t in txns]


@router.get("/raw", response_model=List[RawTransactionOut])
def get_raw_transactions(
    month: Optional[int] = None,
    year: Optional[int] = None,
    period_mode: PeriodMode = Query("calendar"),
    include_deleted: bool = Query(False),
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    import calendar

    if include_deleted:
        query = select(RawTransaction).where(
            RawTransaction.user_id == user_id,
            RawTransaction.status.in_(["pending", "deleted"]),
        )
    else:
        query = select(RawTransaction).where(
            RawTransaction.status == "pending",
            RawTransaction.user_id == user_id,
        )
    if year is not None and month is not None:
        cal_year, cal_month = resolve_period_month(year, month, period_mode)
        last_day = calendar.monthrange(cal_year, cal_month)[1]
        query = query.where(
            RawTransaction.txn_date.between(
                f"{cal_year}-{cal_month:02d}-01",
                f"{cal_year}-{cal_month:02d}-{last_day} 23:59:59",
            )
        )
    elif year is not None:
        start, end = period_year_range(year, period_mode)
        query = query.where(
            RawTransaction.txn_date.between(
                f"{start.isoformat()} 00:00:00",
                f"{end.isoformat()} 23:59:59",
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
        txn_type=body.txn_type,
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
    txn.deleted_at = datetime.now(timezone.utc)
    _purge_expired_soft_deletes(db, user_id)
    db.commit()


@router.post("/raw/purge-expired", status_code=200)
def purge_expired_raw_transactions(
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
) -> dict:
    """Manually trigger the retention purge for the current user. Returns the
    count of rows hard-deleted. Useful for ad-hoc cleanup; the routine purge
    runs automatically on every soft-delete."""
    count = _purge_expired_soft_deletes(db, user_id)
    db.commit()
    return {"purged": count, "retention_days": SOFT_DELETE_RETENTION_DAYS}


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
    txn.deleted_at = None
    db.commit()
    db.refresh(txn)
    return txn


# ─── Auto-categorise ─────────────────────────────────────────────────────────────────


@router.post("/auto-categorise", response_model=AutoCategoriseResponse)
def auto_categorise(
    body: Optional[AutoCategoriseRequest] = None,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    # Selective auto-categorise: if the caller passed a list of raw_txn_ids,
    # only those IDs are considered. The user-scope filter still applies, so a
    # caller can't process another user's rows by guessing an id. An empty list
    # is treated as "process nothing" rather than "process everything" so an
    # accidental empty selection in the UI does not run a full sweep.
    pending_q = select(RawTransaction).where(
        RawTransaction.status == "pending",
        RawTransaction.user_id == user_id,
    )
    if body is not None and body.raw_txn_ids is not None:
        if not body.raw_txn_ids:
            return AutoCategoriseResponse(auto_categorised=0, pending_manual=0)
        pending_q = pending_q.where(RawTransaction.id.in_(body.raw_txn_ids))
    pending = db.execute(pending_q).scalars().all()

    mappings = (
        db.execute(select(CategoryMapping).where(CategoryMapping.user_id == user_id))
        .scalars()
        .all()
    )
    auto_categorised = 0

    for txn in pending:
        if not mappings:
            break

        normalised_desc = normalize_description(txn.description)
        best_score = 0
        best_mapping = None
        for mapping in mappings:
            score = fuzz.token_sort_ratio(
                normalised_desc,
                normalize_description(mapping.description_pattern),
            )
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
                txn_type=(
                    txn.txn_type
                    or classify_txn_type(txn.description, Decimal(str(txn.amount)))
                ),
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


# ─── Manual processing ───────────────────────────────────────────────────────


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
        # Precedence: user pick at categorize time → raw-row hint set at
        # create time → classifier heuristic (positive=expense,
        # negative=refund/transfer; never income).
        txn_type=(
            body.txn_type
            or txn.txn_type
            or classify_txn_type(txn.description, Decimal(str(txn.amount)))
        ),
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
    # Delete means delete. Soft-delete the raw row too instead of bubbling it
    # back to pending — that previously forced users to delete the same txn
    # twice. The raw is recoverable via /raw/{id}/restore if needed.
    raw = db.get(RawTransaction, processed.raw_txn_id)
    if raw is not None:
        raw.status = "deleted"
        raw.deleted_at = datetime.now(timezone.utc)
    db.delete(processed)
    db.flush()
    _purge_expired_soft_deletes(db, user_id)
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

    if body.txn_type is not None:
        processed.txn_type = body.txn_type

    if body.save_mapping and processed.mapping_id is not None:
        mapping = db.get(CategoryMapping, processed.mapping_id)
        if mapping:
            mapping.category_id = processed.category_id
            mapping.last_used = datetime.now(timezone.utc)

    db.commit()
    db.refresh(processed)
    return ProcessedTransactionOut.from_orm(processed)


@router.post("/processed/bulk-tag", status_code=204)
def bulk_tag_transactions(
    body: BulkTagRequest,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    """Add tags to multiple processed transactions at once.

    Tags are *added* (not replaced) — existing tags on each transaction are preserved.
    Transactions or tags not belonging to this user are silently skipped.
    """
    tags = _resolve_tags(body.tag_ids, user_id, db)

    for txn_id in body.transaction_ids:
        processed = db.execute(
            select(ProcessedTransaction).where(
                ProcessedTransaction.id == txn_id,
                ProcessedTransaction.user_id == user_id,
            )
        ).scalar_one_or_none()
        if processed is None:
            continue
        existing_ids = {t.id for t in processed.tags}
        for tag in tags:
            if tag.id not in existing_ids:
                processed.tags.append(tag)

    db.commit()
