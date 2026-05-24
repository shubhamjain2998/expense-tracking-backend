"""Add txn_type to raw_transactions for manual-entry type hinting

Revision ID: b0c1d2e3f4a5
Revises: a9b0c1d2e3f4
Create Date: 2026-05-24

Lets POST /transactions accept a user-chosen txn_type at creation time so
a row can be created as income/refund/transfer rather than always defaulting
to expense when it later gets processed. Nullable so PDF-imported and
text-pasted rows (where the user can't pick) stay untyped and fall back to
the existing classify_txn_type heuristic during processing.

No backfill — the column is purely a hint set at create time.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b0c1d2e3f4a5"
down_revision: Union[str, None] = "a9b0c1d2e3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "raw_transactions",
        sa.Column("txn_type", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("raw_transactions", "txn_type")
