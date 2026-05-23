"""JSON export / import of a user's full data set (see services/backup.py)."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.schemas import BackupExport, BackupImport, BackupImportResponse
from app.services.backup import export_user_data, import_user_data

router = APIRouter(prefix="/backup", tags=["backup"])


@router.get("/export", response_model=BackupExport)
def export_backup(
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
) -> BackupExport:
    """Return a single JSON document containing every piece of user-owned data.

    The shape of the document is the same one accepted by ``POST /backup/import``,
    so an export can be round-tripped back into the system.
    """
    return export_user_data(user_id, db)


@router.post("/import", response_model=BackupImportResponse, status_code=201)
def import_backup(
    payload: BackupImport,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
) -> BackupImportResponse:
    """Populate the database from a backup JSON document.

    Behaviour:
    - Categories / tags / persons are upserted by name (case-insensitive).
    - Budget plans are keyed by (year, category) — existing rows are updated.
    - Transactions are skipped when (date, description, amount) already exists.
    - If ``category_mappings`` is omitted, mappings are auto-generated from each
      unique (description, category) pair in the imported transactions.
    """
    return import_user_data(payload, user_id, db)
