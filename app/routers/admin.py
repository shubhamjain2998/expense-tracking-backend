"""Per-user bulk-wipe endpoints used during development and for self-reset."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import (
    BudgetPlan,
    Category,
    CategoryMapping,
    Person,
    ProcessedTransaction,
    RawTransaction,
    Tag,
    TransactionPersonShare,
    transaction_tags,
)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.delete("/transactions/raw", status_code=204)
def delete_all_raw_transactions(
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    db.execute(delete(RawTransaction).where(RawTransaction.user_id == user_id))
    db.commit()


@router.delete("/transactions/processed", status_code=204)
def delete_all_processed_transactions(
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    # Delete shares for this user's processed transactions, then the transactions
    # themselves. Cascade would handle shares but explicit is safer for bulk ops.
    user_txn_ids = (
        db.execute(
            delete(ProcessedTransaction)
            .where(ProcessedTransaction.user_id == user_id)
            .returning(ProcessedTransaction.id)
        )
        .scalars()
        .all()
    )
    if user_txn_ids:
        db.execute(
            delete(TransactionPersonShare).where(
                TransactionPersonShare.processed_txn_id.in_(user_txn_ids)
            )
        )
    db.execute(delete(RawTransaction).where(RawTransaction.user_id == user_id))
    db.commit()


@router.delete("/categories", status_code=204)
def delete_all_category_mappings(
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    db.execute(delete(CategoryMapping).where(CategoryMapping.user_id == user_id))
    db.commit()


@router.delete("/budget", status_code=204)
def delete_all_budget_plans(
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    db.execute(delete(BudgetPlan).where(BudgetPlan.user_id == user_id))
    db.commit()


@router.delete("/persons", status_code=204)
def delete_all_persons(
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    person_ids = (
        db.execute(delete(Person).where(Person.user_id == user_id).returning(Person.id))
        .scalars()
        .all()
    )
    if person_ids:
        db.execute(
            delete(TransactionPersonShare).where(
                TransactionPersonShare.person_id.in_(person_ids)
            )
        )
    db.commit()


@router.delete("/all", status_code=204)
def delete_everything(
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    """Delete all data belonging to the current user."""
    # transaction_tags rows cascade from processed_transactions; delete explicitly
    # to avoid FK violations when deleting tags
    user_txn_ids = (
        db.execute(
            delete(ProcessedTransaction)
            .where(ProcessedTransaction.user_id == user_id)
            .returning(ProcessedTransaction.id)
        )
        .scalars()
        .all()
    )
    if user_txn_ids:
        db.execute(
            transaction_tags.delete().where(
                transaction_tags.c.processed_txn_id.in_(user_txn_ids)
            )
        )
        db.execute(
            delete(TransactionPersonShare).where(
                TransactionPersonShare.processed_txn_id.in_(user_txn_ids)
            )
        )
    db.execute(delete(RawTransaction).where(RawTransaction.user_id == user_id))
    db.execute(delete(CategoryMapping).where(CategoryMapping.user_id == user_id))
    db.execute(delete(BudgetPlan).where(BudgetPlan.user_id == user_id))
    db.execute(delete(Person).where(Person.user_id == user_id))
    db.execute(delete(Tag).where(Tag.user_id == user_id))
    db.execute(delete(Category).where(Category.user_id == user_id))
    db.commit()
