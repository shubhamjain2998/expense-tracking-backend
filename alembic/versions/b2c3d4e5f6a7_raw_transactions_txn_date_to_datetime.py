"""raw_transactions txn_date date -> datetime

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-18

"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "c38e45ddb6bd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "raw_transactions",
        "txn_date",
        existing_type=sa.Date(),
        type_=sa.DateTime(),
        existing_nullable=False,
        postgresql_using="txn_date::timestamp",
    )


def downgrade() -> None:
    op.alter_column(
        "raw_transactions",
        "txn_date",
        existing_type=sa.DateTime(),
        type_=sa.Date(),
        existing_nullable=False,
        postgresql_using="txn_date::date",
    )
