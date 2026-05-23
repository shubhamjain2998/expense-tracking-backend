"""Statement ingestion — parse PDF / pasted text and persist raw transactions.

Files are SHA-256 hashed and recorded in ``uploaded_files``; subsequent uploads
of the same body return 409 Conflict.
"""

import hashlib
import uuid
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import RawTransaction, UploadedFile
from app.schemas import (
    ParseTextRequest,
    PreviewRow,
    PreviewStatementResponse,
    RawTransactionOut,
    UploadStatementResponse,
)
from app.services.normalizer import clean_description
from app.services.pdf_parser import parse_bank_statement
from app.services.text_parser import parse_bank_statement_text

router = APIRouter(prefix="/uploads", tags=["uploads"])

_ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/octet-stream",  # some HTTP clients send this for PDFs
    "application/x-pdf",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_duplicate(content_hash: str, user_id: uuid.UUID, db: Session) -> None:
    """Raise 409 if this hash was already imported and still has active transactions.

    If the prior upload exists but all its raw transactions have been deleted,
    the UploadedFile record is removed so the same file can be re-imported.
    """
    from app.models import RawTransaction

    existing = db.execute(
        select(UploadedFile).where(
            UploadedFile.user_id == user_id,
            UploadedFile.content_hash == content_hash,
        )
    ).scalar_one_or_none()
    if existing is None:
        return

    # Check if any non-deleted raw transactions still reference this upload
    active = (
        db.execute(
            select(RawTransaction).where(
                RawTransaction.upload_id == existing.id,
                RawTransaction.status != "deleted",
            )
        )
        .scalars()
        .first()
    )

    if active is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This statement has already been imported. "
                "Delete the existing transactions first if you want to re-import."
            ),
        )

    # All transactions from this upload have been deleted — clean up and allow re-import
    db.delete(existing)
    db.flush()


def _record_upload(
    content_hash: str,
    user_id: uuid.UUID,
    source_type: str,
    filename: Optional[str],
    db: Session,
) -> UploadedFile:
    record = UploadedFile(
        user_id=user_id,
        content_hash=content_hash,
        source_type=source_type,
        filename=filename,
    )
    db.add(record)
    db.flush()  # populate record.id before linking raw transactions
    return record


def _require_pdf(file: UploadFile) -> None:
    """Raise 422 if the uploaded file does not look like a PDF."""
    filename = file.filename or ""
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=422,
            detail="Only PDF files are accepted. Please upload a .pdf file.",
        )


# ── POST /uploads/statement ───────────────────────────────────────────────────


@router.post("/statement", response_model=UploadStatementResponse, status_code=201)
async def upload_statement(
    file: UploadFile = File(..., description="Bank statement PDF"),
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
) -> UploadStatementResponse:
    """
    Parse a bank-statement PDF and persist all extracted transactions as
    raw_transactions with status='pending'.
    """
    _require_pdf(file)

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")

    content_hash = _sha256(pdf_bytes)
    _check_duplicate(content_hash, user_id, db)

    try:
        result = parse_bank_statement(pdf_bytes)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to parse PDF: {exc}",
        ) from exc

    if not result.rows:
        raise HTTPException(
            status_code=422,
            detail=(
                "No transactions could be extracted from this PDF. "
                f"Rows skipped: {result.skipped}. "
                "Ensure it is a supported bank statement format."
            ),
        )

    upload = _record_upload(content_hash, user_id, "pdf", file.filename, db)

    db_rows: list[RawTransaction] = []
    for row in result.rows:
        txn = RawTransaction(
            user_id=user_id,
            txn_date=row.txn_date,
            description=clean_description(row.description),
            amount=Decimal(str(row.amount)),
            status="pending",
            upload_id=upload.id,
        )
        db.add(txn)
        db_rows.append(txn)

    db.commit()
    for txn in db_rows:
        db.refresh(txn)

    return UploadStatementResponse(
        inserted=len(db_rows),
        skipped=result.skipped,
        skipped_rows=result.skipped_rows,
        rows=[RawTransactionOut.model_validate(txn) for txn in db_rows],
        warnings=result.warnings,
    )


# ── POST /uploads/preview ──────────────────────────────────────────────────────


@router.post("/preview", response_model=PreviewStatementResponse)
async def preview_statement(
    file: UploadFile = File(..., description="Bank statement PDF"),
    user_id: uuid.UUID = Depends(get_current_user),
) -> PreviewStatementResponse:
    """
    Dry-run: parse the PDF and return what *would* be inserted without
    touching the database.
    """
    _require_pdf(file)

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")

    try:
        result = parse_bank_statement(pdf_bytes)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to parse PDF: {exc}",
        ) from exc

    return PreviewStatementResponse(
        would_insert=len(result.rows),
        skipped=result.skipped,
        skipped_rows=result.skipped_rows,
        rows=[
            PreviewRow(
                txn_date=r.txn_date,
                description=clean_description(r.description),
                amount=Decimal(str(r.amount)),
            )
            for r in result.rows
        ],
        warnings=result.warnings,
    )


# ── POST /uploads/preview-text ────────────────────────────────────────────────


@router.post("/preview-text", response_model=PreviewStatementResponse)
def preview_text(
    body: ParseTextRequest,
    user_id: uuid.UUID = Depends(get_current_user),
) -> PreviewStatementResponse:
    """
    Dry-run: parse pasted bank statement text and return what *would* be
    inserted without touching the database.
    """
    result = parse_bank_statement_text(body.text)
    return PreviewStatementResponse(
        would_insert=len(result.rows),
        skipped=result.skipped,
        skipped_rows=result.skipped_rows,
        rows=[
            PreviewRow(
                txn_date=r.txn_date,
                description=clean_description(r.description),
                amount=Decimal(str(r.amount)),
            )
            for r in result.rows
        ],
        warnings=result.warnings,
    )


# ── POST /uploads/text-import ─────────────────────────────────────────────────


@router.post("/text-import", response_model=UploadStatementResponse, status_code=201)
def text_import(
    body: ParseTextRequest,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
) -> UploadStatementResponse:
    """
    Parse pasted bank statement text and persist all extracted transactions as
    raw_transactions with status='pending'.
    """
    content_hash = _sha256(body.text.encode())
    _check_duplicate(content_hash, user_id, db)

    result = parse_bank_statement_text(body.text)

    if not result.rows:
        raise HTTPException(
            status_code=422,
            detail=(
                "No transactions could be extracted from the provided text. "
                f"Rows skipped: {result.skipped}. "
                "Ensure it is a supported bank statement format."
            ),
        )

    upload = _record_upload(content_hash, user_id, "text", None, db)

    db_rows: list[RawTransaction] = []
    for row in result.rows:
        txn = RawTransaction(
            user_id=user_id,
            txn_date=row.txn_date,
            description=clean_description(row.description),
            amount=Decimal(str(row.amount)),
            status="pending",
            upload_id=upload.id,
        )
        db.add(txn)
        db_rows.append(txn)

    db.commit()
    for txn in db_rows:
        db.refresh(txn)

    return UploadStatementResponse(
        inserted=len(db_rows),
        skipped=result.skipped,
        skipped_rows=result.skipped_rows,
        rows=[RawTransactionOut.model_validate(txn) for txn in db_rows],
        warnings=result.warnings,
    )
