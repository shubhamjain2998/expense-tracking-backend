"""remove uploads table

Revision ID: a1b2c3d4e5f6
Revises: ebc343f1c9e6
Create Date: 2026-03-16

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'ebc343f1c9e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('raw_transactions_upload_id_fkey', 'raw_transactions', type_='foreignkey')
    op.drop_column('raw_transactions', 'upload_id')
    op.drop_table('uploads')


def downgrade() -> None:
    op.create_table('uploads',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('filename', sa.String(), nullable=False),
        sa.Column('source_type', sa.String(), nullable=False),
        sa.Column('month', sa.Integer(), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('row_count', sa.Integer(), nullable=False),
        sa.Column('uploaded_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.add_column('raw_transactions', sa.Column('upload_id', sa.UUID(), nullable=False))
    op.create_foreign_key('raw_transactions_upload_id_fkey', 'raw_transactions', 'uploads', ['upload_id'], ['id'])
