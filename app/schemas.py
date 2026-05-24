import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ─── Categories ────────────────────────────────────────────────────────────


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str


class CategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


class CategoryRename(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


# ─── Tags ────────────────────────────────────────────────────────────────────────


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str


class TagCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


# ─── Budget ────────────────────────────────────────────────────────────────────


class BudgetEntryCreate(BaseModel):
    category_id: uuid.UUID
    allocated_amount: Decimal


class BudgetPlanCreate(BaseModel):
    year: int
    entries: List[BudgetEntryCreate]


class BudgetPlanUpdate(BaseModel):
    category_id: Optional[uuid.UUID] = None
    allocated_amount: Optional[Decimal] = None


class BudgetPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    year: int
    category_id: uuid.UUID
    category: str  # derived from relationship
    allocated_amount: Decimal

    @classmethod
    def from_orm(cls, row: object) -> "BudgetPlanOut":
        return cls(
            id=row.id,
            year=row.year,
            category_id=row.category_id,
            category=row.category.name,
            allocated_amount=Decimal(str(row.allocated_amount)),
        )


# ─── Uploads ───────────────────────────────────────────────────────────────────


class RawTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    txn_date: datetime
    description: str
    amount: Decimal
    status: str
    deleted_at: Optional[datetime] = None
    txn_type: Optional[Literal["expense", "income", "refund", "transfer"]] = None


class UploadStatementResponse(BaseModel):
    inserted: int
    skipped: int
    skipped_rows: List[str] = []
    rows: List[RawTransactionOut]
    warnings: List[str] = []


class PreviewRow(BaseModel):
    txn_date: datetime
    description: str
    amount: Decimal


class PreviewStatementResponse(BaseModel):
    would_insert: int
    skipped: int
    skipped_rows: List[str] = []
    rows: List[PreviewRow]
    warnings: List[str] = []


class ParseTextRequest(BaseModel):
    text: str


class CreateRawTransactionRequest(BaseModel):
    txn_date: datetime
    description: str
    amount: Decimal
    # Optional user-chosen type. When supplied, it's stored on the raw row
    # and honored later during categorize so an "income" pick survives the
    # classify_txn_type heuristic (which has no rule that returns income).
    txn_type: Optional[Literal["expense", "income", "refund", "transfer"]] = None

    @field_validator("amount")
    @classmethod
    def amount_must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("amount must be greater than 0")
        return v


# ─── Transactions ───────────────────────────────────────────────────────────────────


class PersonShareIn(BaseModel):
    person_id: uuid.UUID
    share_type: Literal["percentage", "amount"]
    share_value: Decimal  # 0-100 if percentage, exact dollars if amount

    @field_validator("share_value")
    @classmethod
    def share_value_must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("share_value must be greater than 0")
        return v


class PersonShareOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    person_id: uuid.UUID
    person_name: str
    share_type: str
    share_value: Decimal
    share_amount: Decimal
    settled: bool

    @classmethod
    def from_orm_share(cls, share: object) -> "PersonShareOut":
        return cls(
            person_id=share.person_id,
            person_name=share.person.name,
            share_type=share.share_type,
            share_value=Decimal(str(share.share_value)),
            share_amount=Decimal(str(share.share_amount)),
            settled=share.settled,
        )


class PatchShareSettledRequest(BaseModel):
    settled: bool


class ProcessedTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    raw_txn_id: uuid.UUID
    mapping_id: Optional[uuid.UUID]
    category_id: uuid.UUID
    category: str  # derived from relationship
    txn_date: date
    description: str
    amount: Decimal
    effective_amount: Decimal
    month: int
    year: int
    notes: Optional[str]
    txn_type: str = "expense"  # expense | income | refund | transfer
    shares: List[PersonShareOut] = []
    tags: List[TagOut] = []

    @classmethod
    def from_orm(cls, txn: object) -> "ProcessedTransactionOut":
        return cls(
            id=txn.id,
            raw_txn_id=txn.raw_txn_id,
            mapping_id=txn.mapping_id,
            category_id=txn.category_id,
            category=txn.category.name,
            txn_date=txn.txn_date,
            description=txn.description,
            amount=Decimal(str(txn.amount)),
            effective_amount=Decimal(str(txn.effective_amount)),
            month=txn.month,
            year=txn.year,
            notes=txn.notes,
            txn_type=txn.txn_type or "expense",
            shares=[PersonShareOut.from_orm_share(s) for s in txn.shares],
            tags=[TagOut(id=t.id, name=t.name) for t in txn.tags],
        )


class ProcessTransactionRequest(BaseModel):
    raw_txn_id: uuid.UUID
    category_id: uuid.UUID
    save_mapping: bool = False
    shares: List[PersonShareIn] = []
    notes: Optional[str] = None
    # If set, becomes the processed txn's type. Otherwise falls back to the
    # raw row's txn_type, then to classify_txn_type. Lets the categorize UI
    # mark a statement-uploaded credit as income at the moment of processing.
    txn_type: Optional[Literal["expense", "income", "refund", "transfer"]] = None


class PatchProcessedTransactionRequest(BaseModel):
    category_id: Optional[uuid.UUID] = None
    txn_date: Optional[date] = None
    amount: Optional[Decimal] = None
    description: Optional[str] = None
    notes: Optional[str] = None
    shares: Optional[List[PersonShareIn]] = None
    tag_ids: Optional[List[uuid.UUID]] = None
    save_mapping: Optional[bool] = None
    txn_type: Optional[Literal["expense", "income", "refund", "transfer"]] = None


class AutoCategoriseResponse(BaseModel):
    auto_categorised: int
    pending_manual: int


class BulkTagRequest(BaseModel):
    transaction_ids: List[uuid.UUID]
    tag_ids: List[uuid.UUID]


# ─── Category mappings ────────────────────────────────────────────────────


class CategoryMappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    description_pattern: str
    category_id: uuid.UUID
    category: str  # derived from relationship
    match_count: int
    last_used: Optional[datetime]

    @classmethod
    def from_orm(cls, m: object) -> "CategoryMappingOut":
        return cls(
            id=m.id,
            description_pattern=m.description_pattern,
            category_id=m.category_id,
            category=m.category.name,
            match_count=m.match_count,
            last_used=m.last_used,
        )


# ─── Persons ───────────────────────────────────────────────────────────────────────


class PersonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str


class PersonCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


# ─── Dashboard ────────────────────────────────────────────────────────────────────


class SummaryRow(BaseModel):
    category: str
    allocated_monthly: Decimal
    actual: Decimal
    variance: Decimal
    pct_used: Optional[float]


class MonthlyTrendRow(BaseModel):
    month: int
    actual_amount: Decimal
    income_amount: Decimal = Decimal("0")
    txn_count: int = 0


class SplitLedgerRow(BaseModel):
    person_name: str
    total_split_amount: Decimal


class YTDRow(BaseModel):
    category: str
    allocated_ytd: Decimal
    actual_ytd: Decimal
    variance: Decimal
    pct_used: Optional[float]


class MultiMonthCategoryRow(BaseModel):
    category: str
    actual: Decimal


class MultiMonthSummaryItem(BaseModel):
    year: int
    month: int  # calendar month (1=Jan … 12=Dec)
    expense_total: Decimal
    income_total: Decimal
    category_breakdown: List[MultiMonthCategoryRow]


# ─── Backup / Import / Export ─────────────────────────────────────────────────────────


class BackupNamedEntity(BaseModel):
    name: str


class BackupBudgetPlan(BaseModel):
    year: int
    category: str
    allocated_amount: Decimal


class BackupCategoryMapping(BaseModel):
    description_pattern: str
    category: str


class BackupShare(BaseModel):
    person: str
    share_type: Literal["percentage", "amount"]
    share_value: Decimal
    settled: bool = False


class BackupTransaction(BaseModel):
    txn_date: date
    description: str
    amount: Decimal
    category: str
    notes: Optional[str] = None
    # Optional so backups produced by older versions (or hand-authored from
    # spreadsheets) still import cleanly — fallback is the amount-sign
    # heuristic the create-process flow already uses.
    txn_type: Optional[Literal["expense", "income", "refund", "transfer"]] = None
    tags: List[str] = []
    shares: List[BackupShare] = []


class BackupExport(BaseModel):
    version: str
    exported_at: datetime
    categories: List[BackupNamedEntity]
    tags: List[BackupNamedEntity]
    persons: List[BackupNamedEntity]
    budget_plans: List[BackupBudgetPlan]
    category_mappings: List[BackupCategoryMapping]
    transactions: List[BackupTransaction]


class BackupImport(BaseModel):
    version: str = "1"
    categories: List[BackupNamedEntity] = []
    tags: List[BackupNamedEntity] = []
    persons: List[BackupNamedEntity] = []
    budget_plans: List[BackupBudgetPlan] = []
    # If omitted, mappings are derived from (description, category) of imported txns.
    category_mappings: Optional[List[BackupCategoryMapping]] = None
    transactions: List[BackupTransaction] = []


class BackupImportResponse(BaseModel):
    categories_created: int
    tags_created: int
    persons_created: int
    budget_plans_created: int
    category_mappings_created: int
    transactions_imported: int
    transactions_skipped_duplicates: int
    skipped_rows: List[str] = []
