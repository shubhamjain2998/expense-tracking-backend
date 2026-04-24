"""Add upload_id FK to raw_transactions for re-import support

Revision ID: f8b9c0d1e2f3
Revises: e1f2a3b4c5d6
Create Date: 2026-04-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f8b9c0d1e2f3"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "raw_transactions",
        sa.Column("upload_id", sa.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_raw_transactions_upload_id",
        "raw_transactions",
        "uploaded_files",
        ["upload_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_raw_transactions_upload_id", "raw_transactions", type_="foreignkey"
    )
    op.drop_column("raw_transactions", "upload_id")
