"""Normalize sign for income/refund/transfer rows

Storage convention: expense has positive amount/effective_amount; income,
refund, and transfer rows store negative values. The PDF parser already
follows this; manual entries did not, which caused the YTD income tile and
similar aggregations to subtract manual income instead of adding it.

This migration backfills any non-expense row whose amount or effective_amount
is positive. Idempotent (re-running is a no-op). Share rows attached to the
affected processed transactions are flipped to match.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-05-27
"""

from typing import Sequence, Union

from alembic import op

revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Flip share_amount on shares whose parent txn will be re-signed below.
    # Do this first while the parent is still identifiable by positive
    # effective_amount.
    op.execute(
        """
        UPDATE transaction_person_shares
        SET share_amount = -ABS(share_amount)
        WHERE processed_txn_id IN (
            SELECT id FROM processed_transactions
            WHERE txn_type IN ('income', 'refund', 'transfer')
              AND effective_amount > 0
        )
        """
    )
    op.execute(
        """
        UPDATE processed_transactions
        SET amount = -ABS(amount),
            effective_amount = -ABS(effective_amount)
        WHERE txn_type IN ('income', 'refund', 'transfer')
          AND (amount > 0 OR effective_amount > 0)
        """
    )


def downgrade() -> None:
    # No faithful reverse — we can't tell which rows were originally positive
    # vs. negative pre-migration. Leaving as a no-op rather than blindly
    # flipping signs back, which would re-introduce the bug.
    pass
