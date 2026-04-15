import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import RawTransaction
from app.schemas import (
    ParseTextRequest,
    PreviewRow,
    PreviewStatementResponse,
    RawTransactionOut,
    UploadStatementResponse,
)
from app.services.pdf_parser import parse_bank_statement
from app.services.text_parser import parse_bank_statement_text

router = APIRouter(prefix="/uploads", tags=["uploads"])

_ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/octet-stream",  # some HTTP clients send this for PDFs
    "application/x-pdf",
}


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

    db_rows: list[RawTransaction] = []
    for row in result.rows:
        txn = RawTransaction(
            user_id=user_id,
            txn_date=row.txn_date,
            description=row.description,
            amount=Decimal(str(row.amount)),
            status="pending",
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
                description=r.description,
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
                description=r.description,
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

    db_rows: list[RawTransaction] = []
    for row in result.rows:
        txn = RawTransaction(
            user_id=user_id,
            txn_date=row.txn_date,
            description=row.description,
            amount=Decimal(str(row.amount)),
            status="pending",
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
