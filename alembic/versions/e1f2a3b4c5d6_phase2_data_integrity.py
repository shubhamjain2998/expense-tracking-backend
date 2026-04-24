"""Phase 2: data integrity — timestamps, indexes, duplicate upload protection

Revision ID: e1f2a3b4c5d6
Revises: c3d4e5f6a7b8
Create Date: 2026-04-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables that get created_at + updated_at
_TIMESTAMPED_TABLES = [
    "users",
    "categories",
    "tags",
    "budget_plans",
    "raw_transactions",
    "category_mappings",
    "persons",
    "processed_transactions",
    "transaction_person_shares",
]


def upgrade() -> None:
    now = sa.text("now()")

    # ── Timestamps ────────────────────────────────────────────────────────────
    for table in _TIMESTAMPED_TABLES:
        op.add_column(
            table,
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=now,
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=now,
            ),
        )

    # deleted_at for raw_transactions (soft-delete timestamp)
    op.add_column(
        "raw_transactions",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── Indexes ───────────────────────────────────────────────────────────────
    op.create_index("ix_raw_txn_user_status", "raw_transactions", ["user_id", "status"])
    op.create_index("ix_raw_txn_description", "raw_transactions", ["description"])
    op.create_index(
        "ix_cat_mappings_pattern", "category_mappings", ["description_pattern"]
    )
    op.create_index(
        "ix_processed_txn_user_year_month",
        "processed_transactions",
        ["user_id", "year", "month"],
    )
    op.create_index(
        "ix_processed_txn_cat_date",
        "processed_transactions",
        ["category_id", "txn_date"],
    )

    # ── uploaded_files table ──────────────────────────────────────────────────
    op.create_table(
        "uploaded_files",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=True),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=now,
        ),
        sa.UniqueConstraint(
            "user_id", "content_hash", name="uq_uploaded_files_user_hash"
        ),
    )


def downgrade() -> None:
    op.drop_table("uploaded_files")

    op.drop_index("ix_processed_txn_cat_date", table_name="processed_transactions")
    op.drop_index(
        "ix_processed_txn_user_year_month", table_name="processed_transactions"
    )
    op.drop_index("ix_cat_mappings_pattern", table_name="category_mappings")
    op.drop_index("ix_raw_txn_description", table_name="raw_transactions")
    op.drop_index("ix_raw_txn_user_status", table_name="raw_transactions")

    op.drop_column("raw_transactions", "deleted_at")

    for table in reversed(_TIMESTAMPED_TABLES):
        op.drop_column(table, "updated_at")
        op.drop_column(table, "created_at")
