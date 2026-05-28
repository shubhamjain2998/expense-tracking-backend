"""SQLAlchemy table definitions for every domain entity.

Kept intentionally in a single file so the full schema is greppable in one
place and the import graph stays trivial. Every domain table mixes in
``TimestampMixin`` for ``created_at`` / ``updated_at`` and carries a top-level
``user_id`` column so queries can scope by owner without joins.

Schema changes go through Alembic — see ``docs/DATABASE.md``.
"""

import uuid
from datetime import date, datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    UUID,
    Boolean,
    Date,
    ForeignKey,
    Index,
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─── Timestamp mixin ──────────────────────────────────────────────


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


# ─── User ─────────────────────────────────────────────────────────────────────────────


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    # Nullable: OAuth-only users (signed up via Google) have no local password.
    password_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Google `sub` claim — stable per-user identifier from Google Identity
    # Services. Populated on Google sign-in; null for password-only users.
    google_sub: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, unique=True
    )
    # "calendar" | "fy" | NULL. NULL means the user has not yet picked; the
    # frontend treats that as "show the chooser on the Budget empty state".
    period_mode: Mapped[Optional[str]] = mapped_column(String, nullable=True)


# ─── Category ─────────────────────────────────────────────────────────────────────


class Category(TimestampMixin, Base):
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


# ─── Tags ─────────────────────────────────────────────────────────────────────────────


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


class Tag(TimestampMixin, Base):
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


# ─── Person shares ────────────────────────────────────────────────────────────────


class TransactionPersonShare(TimestampMixin, Base):
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


# ─── Budget ────────────────────────────────────────────────────────────────────────


class BudgetPlan(TimestampMixin, Base):
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


# ─── Raw transactions ─────────────────────────────────────────────────────────────────


class RawTransaction(TimestampMixin, Base):
    __tablename__ = "raw_transactions"
    __table_args__ = (
        Index("ix_raw_txn_user_status", "user_id", "status"),
        Index("ix_raw_txn_description", "description"),
    )

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
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    upload_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("uploaded_files.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Optional hint set when the user creates the row manually (Quick Add /
    # Manual Entry) so the later categorize step can carry it through instead
    # of running classify_txn_type, which has no rule that returns "income".
    txn_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    processed: Mapped[Optional["ProcessedTransaction"]] = relationship(
        back_populates="raw_transaction"
    )
    uploaded_file: Mapped[Optional["UploadedFile"]] = relationship(
        back_populates="raw_transactions"
    )


# ─── Category mappings ────────────────────────────────────────────────────────────────


class CategoryMapping(TimestampMixin, Base):
    __tablename__ = "category_mappings"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "description_pattern", name="uq_category_mappings_user_pattern"
        ),
        Index("ix_cat_mappings_pattern", "description_pattern"),
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


# ─── Persons ──────────────────────────────────────────────────────────────────────────


class Person(TimestampMixin, Base):
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


# ─── Processed transactions ──────────────────────────────────────────────────


class ProcessedTransaction(TimestampMixin, Base):
    __tablename__ = "processed_transactions"
    __table_args__ = (
        Index("ix_processed_txn_user_year_month", "user_id", "year", "month"),
        Index("ix_processed_txn_cat_date", "category_id", "txn_date"),
    )

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
    txn_type: Mapped[str] = mapped_column(
        String, nullable=False, default="expense"
    )  # expense | income | refund | transfer

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


# ─── Uploaded files (duplicate detection) ─────────────────────────────────────────────


class UploadedFile(Base):
    """Records the SHA-256 hash of every successfully imported file or text blob.

    Before inserting raw transactions, the upload endpoints check whether the
    (user_id, content_hash) pair already exists here.  If it does the request
    is rejected with 409 Conflict to prevent duplicate imports.
    """

    __tablename__ = "uploaded_files"
    __table_args__ = (
        UniqueConstraint("user_id", "content_hash", name="uq_uploaded_files_user_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)  # "pdf" | "text"
    filename: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    raw_transactions: Mapped[List["RawTransaction"]] = relationship(
        back_populates="uploaded_file"
    )
