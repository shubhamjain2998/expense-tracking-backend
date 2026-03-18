import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


# ─── Budget ───────────────────────────────────────────────────────────────────


class BudgetEntryCreate(BaseModel):
    category: str
    allocated_amount: Decimal


class BudgetPlanCreate(BaseModel):
    year: int
    entries: List[BudgetEntryCreate]


class BudgetPlanUpdate(BaseModel):
    category: Optional[str] = None
    allocated_amount: Optional[Decimal] = None


class BudgetPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    year: int
    category: str
    allocated_amount: Decimal


# ─── Uploads ──────────────────────────────────────────────────────────────────


class RawTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    txn_date: datetime
    description: str
    amount: Decimal
    status: str


class UploadStatementResponse(BaseModel):
    inserted: int
    skipped: int
    rows: List[RawTransactionOut]
    warnings: List[str] = []


class PreviewRow(BaseModel):
    txn_date: datetime
    description: str
    amount: Decimal


class PreviewStatementResponse(BaseModel):
    would_insert: int
    skipped: int
    rows: List[PreviewRow]
    warnings: List[str] = []
