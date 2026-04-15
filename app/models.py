import uuid
from datetime import date, datetime
from typing import List, Optional
from sqlalchemy import (
    UUID,
    Boolean,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Table,
    Column,
    DateTime,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


# ─── Category ─────────────────────────────────────────────────────────────────


class Category(Base):
    """A small, stable, user-defined category used strictly for budgeting.

    Renaming updates the name in-place; all FK references stay valid automatically.
    """

    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_categories_user_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)

    budget_plans: Mapped[List["BudgetPlan"]] = relationship(back_populates="category")
    processed_transactions: Mapped[List["ProcessedTransaction"]] = relationship(
        back_populates="category"
    )
    category_mappings: Mapped[List["CategoryMapping"]] = relationship(
        back_populates="category"
    )


# ─── Tags ─────────────────────────────────────────────────────────────────────


transaction_tags = Table(
    "transaction_tags",
    Base.metadata,
    Column(
        "processed_txn_id",
        UUID(as_uuid=True),
        ForeignKey("processed_transactions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "tag_id",
        UUID(as_uuid=True),
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Tag(Base):
    """Flexible labels for additional context.

    Independent of categories and budgeting.
    """

    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_tags_user_name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)

    processed_transactions: Mapped[List["ProcessedTransaction"]] = relationship(
        secondary=transaction_tags, back_populates="tags"
    )


# ─── Person shares ────────────────────────────────────────────────────────────


class TransactionPersonShare(Base):
    """Per-person share of a split transaction.

    share_type = "percentage": share_value is 0-100 (percent of total)
    share_type = "amount":     share_value is the exact dollar amount

    share_amount is always the computed dollar amount (denormalised for easy querying).
    """

    __tablename__ = "transaction_person_shares"

    processed_txn_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("processed_transactions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="CASCADE"),
        primary_key=True,
    )
    share_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # percentage | amount
    share_value: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    share_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    settled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    processed_transaction: Mapped["ProcessedTransaction"] = relationship(
        back_populates="shares"
    )
    person: Mapped["Person"] = relationship(back_populates="shares")


# ─── Budget ───────────────────────────────────────────────────────────────────


class BudgetPlan(Base):
    __tablename__ = "budget_plans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id"), nullable=False
    )
    allocated_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    category: Mapped["Category"] = relationship(back_populates="budget_plans")


# ─── Raw transactions ─────────────────────────────────────────────────────────


class RawTransaction(Base):
    __tablename__ = "raw_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    txn_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(
        String, default="pending"
    )  # pending | deleted | processed

    processed: Mapped[Optional["ProcessedTransaction"]] = relationship(
        back_populates="raw_transaction"
    )


# ─── Category mappings ────────────────────────────────────────────────────────


class CategoryMapping(Base):
    __tablename__ = "category_mappings"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "description_pattern", name="uq_category_mappings_user_pattern"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    description_pattern: Mapped[str] = mapped_column(String, nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id"), nullable=False
    )
    match_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    category: Mapped["Category"] = relationship(back_populates="category_mappings")
    processed_transactions: Mapped[List["ProcessedTransaction"]] = relationship(
        back_populates="mapping"
    )


# ─── Persons ──────────────────────────────────────────────────────────────────


class Person(Base):
    __tablename__ = "persons"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_persons_user_name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)

    shares: Mapped[List["TransactionPersonShare"]] = relationship(
        back_populates="person"
    )


# ─── Processed transactions ───────────────────────────────────────────────────


class ProcessedTransaction(Base):
    __tablename__ = "processed_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    raw_txn_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_transactions.id"), nullable=False
    )
    mapping_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("category_mappings.id"), nullable=True
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id"), nullable=False
    )
    txn_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    effective_amount: Mapped[float] = mapped_column(
        Numeric(12, 2), nullable=False
    )  # amount - sum(person share amounts)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    category: Mapped["Category"] = relationship(back_populates="processed_transactions")
    raw_transaction: Mapped["RawTransaction"] = relationship(back_populates="processed")
    mapping: Mapped[Optional["CategoryMapping"]] = relationship(
        back_populates="processed_transactions"
    )
    shares: Mapped[List["TransactionPersonShare"]] = relationship(
        back_populates="processed_transaction",
        cascade="all, delete-orphan",
    )
    tags: Mapped[List["Tag"]] = relationship(
        secondary=transaction_tags, back_populates="processed_transactions"
    )
