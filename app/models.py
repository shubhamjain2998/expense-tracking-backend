import uuid
from datetime import date, datetime
from typing import List, Optional
from sqlalchemy import (
    UUID,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Table,
    Column,
    DateTime,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


# Many-to-many: processed_transactions <-> persons
transaction_persons = Table(
    "transaction_persons",
    Base.metadata,
    Column(
        "processed_txn_id",
        UUID(as_uuid=True),
        ForeignKey("processed_transactions.id"),
        primary_key=True,
    ),
    Column("person_id", UUID(as_uuid=True), ForeignKey("persons.id"), primary_key=True),
)


class BudgetPlan(Base):
    __tablename__ = "budget_plans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    allocated_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)


class RawTransaction(Base):
    __tablename__ = "raw_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    txn_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(
        String, default="pending"
    )  # pending | deleted | processed

    processed: Mapped[Optional["ProcessedTransaction"]] = relationship(
        back_populates="raw_transaction"
    )


class CategoryMapping(Base):
    __tablename__ = "category_mappings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    description_pattern: Mapped[str] = mapped_column(
        String, nullable=False, unique=True
    )
    category: Mapped[str] = mapped_column(String, nullable=False)
    match_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    processed_transactions: Mapped[List["ProcessedTransaction"]] = relationship(
        back_populates="mapping"
    )


class Person(Base):
    __tablename__ = "persons"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)

    processed_transactions: Mapped[List["ProcessedTransaction"]] = relationship(
        secondary=transaction_persons, back_populates="persons"
    )


class ProcessedTransaction(Base):
    __tablename__ = "processed_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    raw_txn_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_transactions.id"), nullable=False
    )
    mapping_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("category_mappings.id"), nullable=True
    )
    category: Mapped[str] = mapped_column(String, nullable=False)
    txn_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    effective_amount: Mapped[float] = mapped_column(
        Numeric(12, 2), nullable=False
    )  # amount / split_count
    split_count: Mapped[int] = mapped_column(Integer, default=1)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)

    raw_transaction: Mapped["RawTransaction"] = relationship(back_populates="processed")
    mapping: Mapped[Optional["CategoryMapping"]] = relationship(
        back_populates="processed_transactions"
    )
    persons: Mapped[List["Person"]] = relationship(
        secondary=transaction_persons, back_populates="processed_transactions"
    )
