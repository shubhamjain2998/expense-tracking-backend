"""Add nullable period_mode to users

A NULL value means the user has not yet picked between calendar and FY mode;
the frontend treats that as "ask them on the Budget empty state".

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-05-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e3f4a5b6c7d8"
down_revision: Union[str, None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("period_mode", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "period_mode")
