"""Replace transaction_persons with transaction_person_shares; drop split_count

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-04-06

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create the new per-person share table
    op.create_table(
        "transaction_person_shares",
        sa.Column("processed_txn_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("share_type", sa.String(), nullable=False),
        sa.Column("share_value", sa.Numeric(12, 2), nullable=False),
        sa.Column("share_amount", sa.Numeric(12, 2), nullable=False),
        sa.ForeignKeyConstraint(
            ["processed_txn_id"],
            ["processed_transactions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["persons.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("processed_txn_id", "person_id"),
    )

    # Migrate existing equal-split associations.
    # Each person in transaction_persons gets an equal percentage share
    # based on the transaction's split_count (1/split_count each).
    op.execute(
        """
        INSERT INTO transaction_person_shares
            (processed_txn_id, person_id, share_type, share_value, share_amount)
        SELECT
            tp.processed_txn_id,
            tp.person_id,
            'percentage',
            ROUND(100.0 / pt.split_count, 2),
            ROUND(pt.amount / pt.split_count, 2)
        FROM transaction_persons tp
        JOIN processed_transactions pt ON pt.id = tp.processed_txn_id
        """
    )

    # Drop old junction table
    op.drop_table("transaction_persons")

    # Drop split_count column (superseded by shares)
    op.drop_column("processed_transactions", "split_count")


def downgrade() -> None:
    # Restore split_count (approximate: 1 + number of person shares per txn)
    op.add_column(
        "processed_transactions",
        sa.Column("split_count", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        UPDATE processed_transactions pt
        SET split_count = COALESCE(
            (SELECT COUNT(*) + 1 FROM transaction_person_shares
             WHERE processed_txn_id = pt.id),
            1
        )
        """
    )
    op.alter_column("processed_transactions", "split_count", nullable=False)

    # Recreate old junction table
    op.create_table(
        "transaction_persons",
        sa.Column("processed_txn_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", sa.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["processed_txn_id"], ["processed_transactions.id"]),
        sa.ForeignKeyConstraint(["person_id"], ["persons.id"]),
        sa.PrimaryKeyConstraint("processed_txn_id", "person_id"),
    )
    op.execute(
        """
        INSERT INTO transaction_persons (processed_txn_id, person_id)
        SELECT processed_txn_id, person_id FROM transaction_person_shares
        """
    )

    op.drop_table("transaction_person_shares")
