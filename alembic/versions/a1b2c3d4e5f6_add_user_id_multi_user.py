"""Add user_id to all core tables for multi-user isolation

Each table gains a user_id UUID column (NOT NULL).  Unique constraints that were
previously per-table are widened to be per-user:

  categories:         UNIQUE(name)                → UNIQUE(user_id, name)
  tags:               UNIQUE(name)                → UNIQUE(user_id, name)
  persons:            UNIQUE(name)                → UNIQUE(user_id, name)
  category_mappings:  UNIQUE(description_pattern) → UNIQUE(user_id, description_pattern)

Tables that have no natural uniqueness constraint just get the plain user_id column:
  budget_plans, raw_transactions, processed_transactions

Backfill strategy for existing rows:
  A sentinel UUID (00000000-0000-0000-0000-000000000001) is assigned to all
  pre-existing rows so that the NOT NULL constraint can be applied immediately.
  On a fresh development database, drop & recreate instead.

Revision ID: a1b2c3d4e5f6
Revises: f7a8b9c0d1e2
Create Date: 2026-04-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Sentinel used to back-fill pre-existing rows. All real users will have a
# non-zero Supabase UUID assigned at the application layer.
_SENTINEL = "00000000-0000-0000-0000-000000000001"


def _add_user_id(table: str) -> None:
    """Add nullable user_id, backfill, then make it NOT NULL."""
    op.add_column(table, sa.Column("user_id", sa.UUID(as_uuid=True), nullable=True))
    op.execute(f"UPDATE {table} SET user_id = '{_SENTINEL}'::uuid")
    op.alter_column(table, "user_id", nullable=False)


def upgrade() -> None:
    # ── categories ────────────────────────────────────────────────────────────
    _add_user_id("categories")
    op.drop_constraint("categories_name_key", "categories", type_="unique")
    op.create_unique_constraint(
        "uq_categories_user_name", "categories", ["user_id", "name"]
    )

    # ── tags ──────────────────────────────────────────────────────────────────
    _add_user_id("tags")
    op.drop_constraint("tags_name_key", "tags", type_="unique")
    op.create_unique_constraint("uq_tags_user_name", "tags", ["user_id", "name"])

    # ── persons ───────────────────────────────────────────────────────────────
    _add_user_id("persons")
    op.drop_constraint("persons_name_key", "persons", type_="unique")
    op.create_unique_constraint("uq_persons_user_name", "persons", ["user_id", "name"])

    # ── category_mappings ─────────────────────────────────────────────────────
    _add_user_id("category_mappings")
    op.drop_constraint(
        "category_mappings_description_pattern_key", "category_mappings", type_="unique"
    )
    op.create_unique_constraint(
        "uq_category_mappings_user_pattern",
        "category_mappings",
        ["user_id", "description_pattern"],
    )

    # ── budget_plans ──────────────────────────────────────────────────────────
    _add_user_id("budget_plans")

    # ── raw_transactions ──────────────────────────────────────────────────────
    _add_user_id("raw_transactions")

    # ── processed_transactions ────────────────────────────────────────────────
    _add_user_id("processed_transactions")


def downgrade() -> None:
    # ── processed_transactions ────────────────────────────────────────────────
    op.drop_column("processed_transactions", "user_id")

    # ── raw_transactions ──────────────────────────────────────────────────────
    op.drop_column("raw_transactions", "user_id")

    # ── budget_plans ──────────────────────────────────────────────────────────
    op.drop_column("budget_plans", "user_id")

    # ── category_mappings ─────────────────────────────────────────────────────
    op.drop_constraint(
        "uq_category_mappings_user_pattern", "category_mappings", type_="unique"
    )
    op.create_unique_constraint(
        "category_mappings_description_pattern_key",
        "category_mappings",
        ["description_pattern"],
    )
    op.drop_column("category_mappings", "user_id")

    # ── persons ───────────────────────────────────────────────────────────────
    op.drop_constraint("uq_persons_user_name", "persons", type_="unique")
    op.create_unique_constraint("persons_name_key", "persons", ["name"])
    op.drop_column("persons", "user_id")

    # ── tags ──────────────────────────────────────────────────────────────────
    op.drop_constraint("uq_tags_user_name", "tags", type_="unique")
    op.create_unique_constraint("tags_name_key", "tags", ["name"])
    op.drop_column("tags", "user_id")

    # ── categories ────────────────────────────────────────────────────────────
    op.drop_constraint("uq_categories_user_name", "categories", type_="unique")
    op.create_unique_constraint("categories_name_key", "categories", ["name"])
    op.drop_column("categories", "user_id")
