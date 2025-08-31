"""Initial migration

Revision ID: 001_initial
Revises: 
Create Date: 2024-01-01 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enum types
    plantype = postgresql.ENUM('free', 'premium', 'enterprise', name='plantype')
    plantype.create(op.get_bind())
    
    auditaction = postgresql.ENUM(
        'login', 'logout', 'calculation', 'credit_purchase', 
        'plan_change', 'register', 'password_change', 
        name='auditaction'
    )
    auditaction.create(op.get_bind())
    
    # Create users table
    op.create_table('users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('hashed_password', sa.String(), nullable=False),
        sa.Column('credits', sa.Integer(), nullable=False, default=0),
        sa.Column('is_verified', sa.Boolean(), nullable=False, default=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, default=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_users_email', 'users', ['email'])
    op.create_index('ix_users_created_at', 'users', ['created_at'])
    op.create_index('ix_users_id', 'users', ['id'])

    # Create user_plans table
    op.create_table('user_plans',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('plan_type', plantype, nullable=False, default='free'),
        sa.Column('credits_per_month', sa.Integer(), nullable=False, default=3),
        sa.Column('max_calculations_per_day', sa.Integer(), nullable=False, default=10),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, default=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id')
    )
    op.create_index('ix_user_plans_user_id', 'user_plans', ['user_id'])
    op.create_index('ix_user_plans_expires_at', 'user_plans', ['expires_at'])
    op.create_index('ix_user_plans_id', 'user_plans', ['id'])

    # Create query_histories table
    op.create_table('query_histories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('icms_value', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('months', sa.Integer(), nullable=False),
        sa.Column('calculated_value', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('calculation_time_ms', sa.Integer(), nullable=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_query_histories_user_id', 'query_histories', ['user_id'])
    op.create_index('ix_query_histories_created_at', 'query_histories', ['created_at'])
    op.create_index('ix_query_histories_user_created', 'query_histories', ['user_id', 'created_at'])
    op.create_index('ix_query_histories_id', 'query_histories', ['id'])

    # Create audit_logs table
    op.create_table('audit_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('action', auditaction, nullable=False),
        sa.Column('resource_type', sa.String(length=50), nullable=True),
        sa.Column('resource_id', sa.Integer(), nullable=True),
        sa.Column('old_values', sa.Text(), nullable=True),
        sa.Column('new_values', sa.Text(), nullable=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('request_id', sa.String(length=36), nullable=True),
        sa.Column('success', sa.Boolean(), nullable=False, default=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_audit_logs_user_id', 'audit_logs', ['user_id'])
    op.create_index('ix_audit_logs_action', 'audit_logs', ['action'])
    op.create_index('ix_audit_logs_created_at', 'audit_logs', ['created_at'])
    op.create_index('ix_audit_logs_request_id', 'audit_logs', ['request_id'])
    op.create_index('ix_audit_logs_user_action_date', 'audit_logs', ['user_id', 'action', 'created_at'])
    op.create_index('ix_audit_logs_id', 'audit_logs', ['id'])

    # Create credit_transactions table
    op.create_table('credit_transactions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('transaction_type', sa.String(length=20), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('balance_before', sa.Integer(), nullable=False),
        sa.Column('balance_after', sa.Integer(), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('reference_id', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_credit_transactions_user_id', 'credit_transactions', ['user_id'])
    op.create_index('ix_credit_transactions_type', 'credit_transactions', ['transaction_type'])
    op.create_index('ix_credit_transactions_created_at', 'credit_transactions', ['created_at'])
    op.create_index('ix_credit_transactions_id', 'credit_transactions', ['id'])


def downgrade() -> None:
    # Drop tables in reverse order
    op.drop_table('credit_transactions')
    op.drop_table('audit_logs')
    op.drop_table('query_histories')
    op.drop_table('user_plans')
    op.drop_table('users')
    
    # Drop enum types
    sa.Enum(name='auditaction').drop(op.get_bind())
    sa.Enum(name='plantype').drop(op.get_bind())