"""Refatoração completa - Phone auth, referral system, credit expiration

Revision ID: 001_phone_auth_referral_system
Revises: 
Create Date: 2024-01-01 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001_phone_auth_referral_system'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Criar ENUMs necessários
    op.execute("CREATE TYPE auditaction AS ENUM ('LOGIN', 'LOGOUT', 'CALCULATION', 'CREDIT_PURCHASE', 'PLAN_CHANGE', 'REGISTER', 'PASSWORD_CHANGE', 'VERIFICATION', 'PASSWORD_RESET')")
    op.execute("CREATE TYPE verificationtype AS ENUM ('SMS', 'EMAIL')")
    
    # Criar tabela users com todos os campos
    op.create_table('users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(), nullable=True),  # Opcional
        sa.Column('phone_number', sa.String(), nullable=True),  # Novo campo
        sa.Column('hashed_password', sa.String(), nullable=False),
        sa.Column('first_name', sa.String(), nullable=True),  # Novo campo
        sa.Column('last_name', sa.String(), nullable=True),   # Novo campo
        sa.Column('referral_code', sa.String(), nullable=True),  # Novo campo
        sa.Column('referred_by_id', sa.Integer(), nullable=True),  # Novo campo
        sa.Column('referral_credits_earned', sa.Integer(), nullable=False, server_default='0'),  # Novo campo
        sa.Column('credits', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_verified', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='false'),  # False por padrão
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Índices para tabela users
    op.create_index(op.f('ix_users_id'), 'users', ['id'], unique=False)
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_index(op.f('ix_users_phone_number'), 'users', ['phone_number'], unique=True)
    op.create_index(op.f('ix_users_referral_code'), 'users', ['referral_code'], unique=True)
    
    # Foreign key para referência (auto-referência)
    op.create_foreign_key('fk_users_referred_by', 'users', 'users', ['referred_by_id'], ['id'])

    # Criar tabela verification_codes
    op.create_table('verification_codes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('identifier', sa.String(), nullable=False),
        sa.Column('code', sa.String(length=6), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('type', postgresql.ENUM('SMS', 'EMAIL', name='verificationtype', create_type=False), nullable=False),
        sa.Column('used', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_verification_codes_id'), 'verification_codes', ['id'], unique=False)
    op.create_index(op.f('ix_verification_codes_identifier'), 'verification_codes', ['identifier'], unique=False)

    # Criar tabela query_histories
    op.create_table('query_histories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('icms_value', sa.Numeric(12, 2), nullable=False),
        sa.Column('months', sa.Integer(), nullable=False),
        sa.Column('calculated_value', sa.Numeric(12, 2), nullable=False),
        sa.Column('calculation_time_ms', sa.Integer(), nullable=True),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_query_histories_id'), 'query_histories', ['id'], unique=False)
    op.create_index(op.f('ix_query_histories_user_id'), 'query_histories', ['user_id'], unique=False)

    # Criar tabela audit_logs
    op.create_table('audit_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('action', postgresql.ENUM('LOGIN', 'LOGOUT', 'CALCULATION', 'CREDIT_PURCHASE', 'PLAN_CHANGE', 'REGISTER', 'PASSWORD_CHANGE', 'VERIFICATION', 'PASSWORD_RESET', name='auditaction', create_type=False), nullable=False),
        sa.Column('resource_type', sa.String(50), nullable=True),
        sa.Column('resource_id', sa.Integer(), nullable=True),
        sa.Column('old_values', sa.Text(), nullable=True),
        sa.Column('new_values', sa.Text(), nullable=True),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('request_id', sa.String(36), nullable=True),
        sa.Column('success', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_logs_id'), 'audit_logs', ['id'], unique=False)
    op.create_index(op.f('ix_audit_logs_action'), 'audit_logs', ['action'], unique=False)
    op.create_index(op.f('ix_audit_logs_request_id'), 'audit_logs', ['request_id'], unique=False)
    op.create_index(op.f('ix_audit_logs_created_at'), 'audit_logs', ['created_at'], unique=False)

    # Criar tabela credit_transactions (com campo expires_at)
    op.create_table('credit_transactions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('transaction_type', sa.String(20), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('balance_before', sa.Integer(), nullable=False),
        sa.Column('balance_after', sa.Integer(), nullable=False),
        sa.Column('description', sa.String(255), nullable=True),
        sa.Column('reference_id', sa.String(100), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),  # Campo de validade
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_credit_transactions_id'), 'credit_transactions', ['id'], unique=False)
    op.create_index(op.f('ix_credit_transactions_user_id'), 'credit_transactions', ['user_id'], unique=False)
    op.create_index(op.f('ix_credit_transactions_transaction_type'), 'credit_transactions', ['transaction_type'], unique=False)

    # Criar tabela selic_rates
    op.create_table('selic_rates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('month', sa.Integer(), nullable=False),
        sa.Column('rate', sa.Numeric(10, 5), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('year', 'month', name='_year_month_uc')
    )
    op.create_index(op.f('ix_selic_rates_id'), 'selic_rates', ['id'], unique=False)


def downgrade() -> None:
    # Remover tabelas na ordem reversa (por causa das foreign keys)
    op.drop_index(op.f('ix_selic_rates_id'), table_name='selic_rates')
    op.drop_table('selic_rates')
    
    op.drop_index(op.f('ix_credit_transactions_transaction_type'), table_name='credit_transactions')
    op.drop_index(op.f('ix_credit_transactions_user_id'), table_name='credit_transactions')
    op.drop_index(op.f('ix_credit_transactions_id'), table_name='credit_transactions')
    op.drop_table('credit_transactions')
    
    op.drop_index(op.f('ix_audit_logs_created_at'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_request_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_action'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_id'), table_name='audit_logs')
    op.drop_table('audit_logs')
    
    op.drop_index(op.f('ix_query_histories_user_id'), table_name='query_histories')
    op.drop_index(op.f('ix_query_histories_id'), table_name='query_histories')
    op.drop_table('query_histories')
    
    op.drop_index(op.f('ix_verification_codes_identifier'), table_name='verification_codes')
    op.drop_index(op.f('ix_verification_codes_id'), table_name='verification_codes')
    op.drop_table('verification_codes')
    
    # Remover foreign key antes de dropar a tabela
    op.drop_constraint('fk_users_referred_by', 'users', type_='foreignkey')
    
    op.drop_index(op.f('ix_users_referral_code'), table_name='users')
    op.drop_index(op.f('ix_users_phone_number'), table_name='users')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_index(op.f('ix_users_id'), table_name='users')
    op.drop_table('users')
    
    # Remover ENUMs
    op.execute("DROP TYPE IF EXISTS verificationtype")
    op.execute("DROP TYPE IF EXISTS auditaction")