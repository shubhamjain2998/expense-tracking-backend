"""category mappings carry tags and splits

Additive only: two new association tables. No existing table is altered, so a
downgrade is a pair of DROP TABLEs and every processed transaction is
untouched either way.

Revision ID: a1c2e3f4b5d6
Revises: f1a2b3c4d5e6
Create Date: 2026-09-22
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "a1c2e3f4b5d6"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "category_mapping_tags",
        sa.Column(
            "mapping_id",
            UUID(as_uuid=True),
            sa.ForeignKey("category_mappings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_table(
        "category_mapping_shares",
        sa.Column(
            "mapping_id",
            UUID(as_uuid=True),
            sa.ForeignKey("category_mappings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "person_id",
            UUID(as_uuid=True),
            sa.ForeignKey("persons.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("share_type", sa.String(), nullable=False),
        sa.Column("share_value", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_check_constraint(
        "ck_category_mapping_shares_type",
        "category_mapping_shares",
        "share_type IN ('percentage', 'amount')",
    )


def downgrade() -> None:
    op.drop_table("category_mapping_shares")
    op.drop_table("category_mapping_tags")
