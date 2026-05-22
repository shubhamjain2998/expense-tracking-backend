"""Add txn_type to processed_transactions and reclassify negatives

Revision ID: a9b0c1d2e3f4
Revises: f8b9c0d1e2f3
Create Date: 2026-05-22

Migration adds txn_type (expense|income|refund|transfer) to processed_transactions.
Existing rows are backfilled:
  - positive effective_amount → expense
  - negative effective_amount with transfer keywords → transfer
  - other negative effective_amount → refund

No data is lost; txn_type can be changed by the user at any time via PATCH.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9b0c1d2e3f4"
down_revision: Union[str, None] = "f8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TRANSFER_KEYWORDS = [
    "CREDIT CARD PAYMENT",
    "PAYMENT THANK",
    "NEFT",
    "IMPS TFR",
    "RTGS",
]


def upgrade() -> None:
    op.add_column(
        "processed_transactions",
        sa.Column("txn_type", sa.String(), nullable=True),
    )

    conn = op.get_bind()

    # Backfill: positive → expense
    conn.execute(
        sa.text(
            "UPDATE processed_transactions SET txn_type = 'expense'"
            " WHERE effective_amount >= 0"
        )
    )

    # Backfill: negative + transfer keyword → transfer
    for keyword in _TRANSFER_KEYWORDS:
        conn.execute(
            sa.text(
                "UPDATE processed_transactions SET txn_type = 'transfer'"
                " WHERE effective_amount < 0"
                " AND UPPER(description) LIKE :pat"
            ),
            {"pat": f"%{keyword}%"},
        )

    # Backfill: remaining negatives → refund
    conn.execute(
        sa.text(
            "UPDATE processed_transactions SET txn_type = 'refund'"
            " WHERE effective_amount < 0 AND txn_type IS NULL"
        )
    )

    # Make non-nullable now that all rows are filled
    op.alter_column("processed_transactions", "txn_type", nullable=False)


def downgrade() -> None:
    op.drop_column("processed_transactions", "txn_type")
