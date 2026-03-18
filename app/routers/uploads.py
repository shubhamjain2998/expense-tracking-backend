from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import RawTransaction
from app.schemas import (
    PreviewRow,
    PreviewStatementResponse,
    RawTransactionOut,
    UploadStatementResponse,
)
from app.services.pdf_parser import parse_bank_statement

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
) -> UploadStatementResponse:
    """
    Parse a bank-statement PDF and persist all extracted transactions as
    raw_transactions with status='pending'.

    - Reads the file entirely in memory — never written to disk.
    - Returns inserted count, skipped count, every inserted row, and any warnings.
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

    # ── Persist ───────────────────────────────────────────────────────────────
    db_rows: list[RawTransaction] = []
    for row in result.rows:
        txn = RawTransaction(
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
        rows=[RawTransactionOut.model_validate(txn) for txn in db_rows],
        warnings=result.warnings,
    )


# ── GET /uploads/preview ──────────────────────────────────────────────────────


@router.post("/preview", response_model=PreviewStatementResponse)
async def preview_statement(
    file: UploadFile = File(..., description="Bank statement PDF"),
) -> PreviewStatementResponse:
    """
    Dry-run: parse the PDF and return what *would* be inserted without
    touching the database.  Useful for frontend confirmation before commit.
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
