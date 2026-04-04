import uuid
from datetime import date, datetime
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


# ─── Transactions ─────────────────────────────────────────────────────────────


class PersonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str


class ProcessedTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    raw_txn_id: uuid.UUID
    mapping_id: Optional[uuid.UUID]
    category: str
    txn_date: date
    description: str
    amount: Decimal
    effective_amount: Decimal
    split_count: int
    month: int
    year: int
    persons: List[PersonOut] = []


class ProcessTransactionRequest(BaseModel):
    raw_txn_id: uuid.UUID
    category: str
    save_mapping: bool = False
    split_count: int = 1
    person_ids: List[uuid.UUID] = []


class PatchProcessedTransactionRequest(BaseModel):
    category: Optional[str] = None
    split_count: Optional[int] = None
    person_ids: Optional[List[uuid.UUID]] = None
    save_mapping: Optional[bool] = None


class AutoCategoriseResponse(BaseModel):
    auto_categorised: int
    pending_manual: int


# ─── Category mappings ────────────────────────────────────────────────────────


class CategoryMappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    description_pattern: str
    category: str
    match_count: int
    last_used: Optional[datetime]


# ─── Persons ──────────────────────────────────────────────────────────────────


class PersonCreate(BaseModel):
    name: str


# ─── Dashboard ────────────────────────────────────────────────────────────────


class SummaryRow(BaseModel):
    category: str
    allocated_monthly: Decimal
    actual: Decimal
    variance: Decimal
    pct_used: Optional[float]


class MonthlyTrendRow(BaseModel):
    month: int
    actual_amount: Decimal


class SplitLedgerRow(BaseModel):
    person_name: str
    total_split_amount: Decimal


class YTDRow(BaseModel):
    category: str
    allocated_ytd: Decimal
    actual_ytd: Decimal
    variance: Decimal
    pct_used: Optional[float]
