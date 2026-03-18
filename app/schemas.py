import uuid
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
