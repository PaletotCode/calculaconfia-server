"""Adiciona coluna is_admin à tabela users

Revision ID: 003_add_is_admin
Revises: 002_rm_phone_single_ref
Create Date: 2025-09-05 04:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '003_add_is_admin'
down_revision = '002_rm_phone_single_ref'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Adiciona coluna is_admin (boolean, not null, default false)
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('is_admin', sa.Boolean(), nullable=False, server_default=sa.text('false')))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('is_admin')

