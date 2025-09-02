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
    # Criar enum para tipos de verificação
    verification_type_enum = postgresql.ENUM('SMS', 'EMAIL', name='verificationtype')
    verification_type_enum.create(op.get_bind(), checkfirst=True)
    
    # Criar nova tabela verification_codes
    op.create_table('verification_codes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('identifier', sa.String(), nullable=False),
        sa.Column('code', sa.String(length=6), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('type', verification_type_enum, nullable=False),
        sa.Column('used', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_verification_codes_id'), 'verification_codes', ['id'], unique=False)
    op.create_index(op.f('ix_verification_codes_identifier'), 'verification_codes', ['identifier'], unique=False)

    # Modificar tabela users - adicionar novos campos
    op.add_column('users', sa.Column('phone_number', sa.String(), nullable=True))
    op.add_column('users', sa.Column('first_name', sa.String(), nullable=True))
    op.add_column('users', sa.Column('last_name', sa.String(), nullable=True))
    op.add_column('users', sa.Column('referral_code', sa.String(), nullable=True))
    op.add_column('users', sa.Column('referred_by_id', sa.Integer(), nullable=True))
    op.add_column('users', sa.Column('referral_credits_earned', sa.Integer(), nullable=False, server_default='0'))
    
    # Criar índices para novos campos
    op.create_index(op.f('ix_users_phone_number'), 'users', ['phone_number'], unique=True)
    op.create_index(op.f('ix_users_referral_code'), 'users', ['referral_code'], unique=True)
    
    # Criar foreign key para referência
    op.create_foreign_key('fk_users_referred_by', 'users', 'users', ['referred_by_id'], ['id'])
    
    # Modificar campo email para ser opcional (remover NOT NULL se existir)
    op.alter_column('users', 'email', nullable=True)
    
    # Modificar campo is_active para default False
    op.alter_column('users', 'is_active', server_default='false')
    
    # Modificar campo is_verified para default False
    op.alter_column('users', 'is_verified', server_default='false')
    
    # Adicionar campo expires_at na tabela credit_transactions
    op.add_column('credit_transactions', sa.Column('expires_at', sa.DateTime(), nullable=True))
    
    # Adicionar novos valores ao enum AuditAction
    # Primeiro, criar o novo enum
    new_audit_action_enum = postgresql.ENUM(
    'LOGIN', 'LOGOUT', 'CALCULATION', 'CREDIT_PURCHASE', 'PLAN_CHANGE', 
    'REGISTER', 'PASSWORD_CHANGE', 'VERIFICATION', 'PASSWORD_RESET', 
    name='auditaction_new'
    )
    new_audit_action_enum.create(op.get_bind(), checkfirst=True)
    
    # Migrar dados do enum antigo para o novo
    op.execute("ALTER TABLE audit_logs ALTER COLUMN action TYPE auditaction_new USING action::text::auditaction_new")
    
    # Remover enum antigo e renomear o novo
    op.execute("DROP TYPE IF EXISTS auditaction")
    op.execute("ALTER TYPE auditaction_new RENAME TO auditaction")
    
    # Remover tabela user_plans completamente
    op.drop_table('user_plans')


def downgrade() -> None:
    # Recriar tabela user_plans
    plantype_enum = postgresql.ENUM('FREE', 'PREMIUM', 'ENTERPRISE', name='plantype')
    plantype_enum.create(op.get_bind())
    
    op.create_table('user_plans',
        sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column('plan_type', plantype_enum, autoincrement=False, nullable=False),
        sa.Column('credits_per_month', sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column('max_calculations_per_day', sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column('expires_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
        sa.Column('is_active', sa.BOOLEAN(), autoincrement=False, nullable=False),
        sa.Column('created_at', postgresql.TIMESTAMP(), server_default=sa.text('now()'), autoincrement=False, nullable=False),
        sa.Column('updated_at', postgresql.TIMESTAMP(), server_default=sa.text('now()'), autoincrement=False, nullable=False),
        sa.PrimaryKeyConstraint('id', name='user_plans_pkey'),
        sa.UniqueConstraint('user_id', name='user_plans_user_id_key')
    )
    op.create_index('ix_user_plans_id', 'user_plans', ['id'], unique=False)
    op.create_index('ix_user_plans_user_id', 'user_plans', ['user_id'], unique=False)
    op.create_foreign_key('user_plans_user_id_fkey', 'user_plans', 'users', ['user_id'], ['id'])
    
    # Restaurar enum AuditAction original
    old_audit_action_enum = postgresql.ENUM(
        'LOGIN', 'LOGOUT', 'CALCULATION', 'CREDIT_PURCHASE', 'PLAN_CHANGE', 
        'REGISTER', 'PASSWORD_CHANGE', 
        name='auditaction_old'
    )
    old_audit_action_enum.create(op.get_bind())
    
    op.execute("ALTER TABLE audit_logs ALTER COLUMN action TYPE auditaction_old USING action::text::auditaction_old")
    op.execute("DROP TYPE IF EXISTS auditaction")
    op.execute("ALTER TYPE auditaction_old RENAME TO auditaction")
    
    # Remover campo expires_at da tabela credit_transactions
    op.drop_column('credit_transactions', 'expires_at')
    
    # Restaurar is_active para default True
    op.alter_column('users', 'is_active', server_default='true')
    
    # Restaurar is_verified para default False (mantém)
    op.alter_column('users', 'is_verified', server_default='false')
    
    # Restaurar email como obrigatório
    op.alter_column('users', 'email', nullable=False)
    
    # Remover foreign key de referência
    op.drop_constraint('fk_users_referred_by', 'users', type_='foreignkey')
    
    # Remover índices dos novos campos
    op.drop_index(op.f('ix_users_referral_code'), table_name='users')
    op.drop_index(op.f('ix_users_phone_number'), table_name='users')
    
    # Remover novos campos da tabela users
    op.drop_column('users', 'referral_credits_earned')
    op.drop_column('users', 'referred_by_id')
    op.drop_column('users', 'referral_code')
    op.drop_column('users', 'last_name')
    op.drop_column('users', 'first_name')
    op.drop_column('users', 'phone_number')
    
    # Remover tabela verification_codes
    op.drop_index(op.f('ix_verification_codes_identifier'), table_name='verification_codes')
    op.drop_index(op.f('ix_verification_codes_id'), table_name='verification_codes')
    op.drop_table('verification_codes')
    
    # Remover enum de tipos de verificação
    op.execute("DROP TYPE IF EXISTS verificationtype")