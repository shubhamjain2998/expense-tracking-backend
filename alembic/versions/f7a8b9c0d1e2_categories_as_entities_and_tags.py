"""Promote categories to entities; add tags

Replaces the bare `category` string columns on processed_transactions,
budget_plans, and category_mappings with a proper `categories` table and
FK references.  Also adds the `tags` / `transaction_tags` tables.

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-04-07

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Create categories table ────────────────────────────────────────────
    op.create_table(
        "categories",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
    )

    # ── 2. Seed categories from all existing string values ────────────────────
    # Collect distinct lowercase category strings from all three tables and
    # insert one row per unique name.
    op.execute(
        """
        INSERT INTO categories (id, name)
        SELECT gen_random_uuid(), name
        FROM (
            SELECT LOWER(category) AS name FROM processed_transactions
            UNION
            SELECT LOWER(category) FROM budget_plans
            UNION
            SELECT LOWER(category) FROM category_mappings
        ) AS all_cats
        WHERE name IS NOT NULL AND name <> ''
        """
    )

    # ── 3. Add nullable category_id FK columns ────────────────────────────────
    op.add_column(
        "processed_transactions",
        sa.Column("category_id", sa.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "budget_plans",
        sa.Column("category_id", sa.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "category_mappings",
        sa.Column("category_id", sa.UUID(as_uuid=True), nullable=True),
    )

    # ── 4. Populate FK columns by matching the old string values ──────────────
    op.execute(
        """
        UPDATE processed_transactions pt
        SET category_id = c.id
        FROM categories c
        WHERE LOWER(pt.category) = c.name
        """
    )
    op.execute(
        """
        UPDATE budget_plans bp
        SET category_id = c.id
        FROM categories c
        WHERE LOWER(bp.category) = c.name
        """
    )
    op.execute(
        """
        UPDATE category_mappings cm
        SET category_id = c.id
        FROM categories c
        WHERE LOWER(cm.category) = c.name
        """
    )

    # ── 5. Make FK columns non-nullable and add FK constraints ────────────────
    op.alter_column("processed_transactions", "category_id", nullable=False)
    op.create_foreign_key(
        "fk_processed_transactions_category_id",
        "processed_transactions",
        "categories",
        ["category_id"],
        ["id"],
    )

    op.alter_column("budget_plans", "category_id", nullable=False)
    op.create_foreign_key(
        "fk_budget_plans_category_id",
        "budget_plans",
        "categories",
        ["category_id"],
        ["id"],
    )

    op.alter_column("category_mappings", "category_id", nullable=False)
    op.create_foreign_key(
        "fk_category_mappings_category_id",
        "category_mappings",
        "categories",
        ["category_id"],
        ["id"],
    )

    # ── 6. Drop the old string columns ────────────────────────────────────────
    op.drop_column("processed_transactions", "category")
    op.drop_column("budget_plans", "category")
    op.drop_column("category_mappings", "category")

    # ── 7. Create tags and transaction_tags tables ────────────────────────────
    op.create_table(
        "tags",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
    )

    op.create_table(
        "transaction_tags",
        sa.Column(
            "processed_txn_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("processed_transactions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )


def downgrade() -> None:
    # ── Restore string columns ────────────────────────────────────────────────
    op.drop_table("transaction_tags")
    op.drop_table("tags")

    op.add_column(
        "processed_transactions",
        sa.Column("category", sa.String(), nullable=True),
    )
    op.add_column(
        "budget_plans",
        sa.Column("category", sa.String(), nullable=True),
    )
    op.add_column(
        "category_mappings",
        sa.Column("category", sa.String(), nullable=True),
    )

    op.execute(
        """
        UPDATE processed_transactions pt
        SET category = c.name
        FROM categories c WHERE c.id = pt.category_id
        """
    )
    op.execute(
        """
        UPDATE budget_plans bp
        SET category = c.name
        FROM categories c WHERE c.id = bp.category_id
        """
    )
    op.execute(
        """
        UPDATE category_mappings cm
        SET category = c.name
        FROM categories c WHERE c.id = cm.category_id
        """
    )

    op.alter_column("processed_transactions", "category", nullable=False)
    op.alter_column("budget_plans", "category", nullable=False)
    op.alter_column("category_mappings", "category", nullable=False)

    op.drop_constraint(
        "fk_processed_transactions_category_id",
        "processed_transactions",
        type_="foreignkey",
    )
    op.drop_column("processed_transactions", "category_id")

    op.drop_constraint(
        "fk_budget_plans_category_id", "budget_plans", type_="foreignkey"
    )
    op.drop_column("budget_plans", "category_id")

    op.drop_constraint(
        "fk_category_mappings_category_id", "category_mappings", type_="foreignkey"
    )
    op.drop_column("category_mappings", "category_id")

    op.drop_table("categories")
