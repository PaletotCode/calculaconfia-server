"""Remove campo phone_number e preparar base para código de indicação uso único

Revision ID: 002_rm_phone_single_ref
Revises: 7679fde5796d
Create Date: 2025-09-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '002_rm_phone_single_ref'
down_revision = '7679fde5796d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Remover índice de phone_number se existir e a coluna
    try:
        op.drop_index(op.f('ix_users_phone_number'), table_name='users')
    except Exception:
        pass
    with op.batch_alter_table('users') as batch_op:
        try:
            batch_op.drop_column('phone_number')
        except Exception:
            pass


def downgrade() -> None:
    # Recriar coluna phone_number e índice (nullable=True como antes)
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('phone_number', sa.String(), nullable=True))
    op.create_index(op.f('ix_users_phone_number'), 'users', ['phone_number'], unique=True)
