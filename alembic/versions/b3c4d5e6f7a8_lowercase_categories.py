"""lowercase existing categories

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-04-06

"""

from typing import Sequence, Union
from alembic import op


revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, None] = ("a1b2c3d4e5f6", "b2c3d4e5f6a7")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE processed_transactions SET category = LOWER(category)"
        " WHERE category != LOWER(category)"
    )
    op.execute(
        "UPDATE category_mappings SET category = LOWER(category)"
        " WHERE category != LOWER(category)"
    )


def downgrade() -> None:
    pass
