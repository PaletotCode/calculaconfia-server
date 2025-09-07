import enum
import sqlalchemy as sa
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Numeric, Text,
    ForeignKey, Enum
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..core.database import Base


class PlanType(enum.Enum):
    FREE = "free"
    PREMIUM = "premium"
    ENTERPRISE = "enterprise"

class AuditAction(enum.Enum):
    LOGIN = "login"
    LOGOUT = "logout"
    CALCULATION = "calculation"
    CREDIT_PURCHASE = "credit_purchase"
    PLAN_CHANGE = "plan_change"
    REGISTER = "register"
    PASSWORD_CHANGE = "password_change"
    VERIFICATION = "verification"
    PASSWORD_RESET = "password_reset"

class VerificationType(enum.Enum):
    SMS = "sms"
    EMAIL = "email"


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    first_name = Column(String, nullable=True)  # Novo campo
    last_name = Column(String, nullable=True)   # Novo campo
    referral_code = Column(String, unique=True, index=True, nullable=True)  # Novo campo
    referred_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # Novo campo
    referral_credits_earned = Column(Integer, default=0, nullable=False)  # Novo campo
    credits = Column(Integer, nullable=False, default=0)
    is_verified = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=False, nullable=False)  # Agora False por padrão
    is_admin = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relacionamentos
    history = relationship("QueryHistory", back_populates="user")
    referrer = relationship("User", remote_side=[id], backref="referred_users")
    credit_transactions = relationship("CreditTransaction", back_populates="user")


class VerificationCode(Base):
    __tablename__ = "verification_codes"
    id = Column(Integer, primary_key=True, index=True)
    identifier = Column(String, index=True, nullable=False)  # Email ou telefone
    code = Column(String(6), nullable=False)  # Código de 6 dígitos
    expires_at = Column(DateTime, nullable=False)
    type = Column(Enum(VerificationType), nullable=False)
    used = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class QueryHistory(Base):
    __tablename__ = "query_histories"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    icms_value = Column(Numeric(12, 2), nullable=False)
    months = Column(Integer, nullable=False)
    calculated_value = Column(Numeric(12, 2), nullable=False)
    calculation_time_ms = Column(Integer, nullable=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="history")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action = Column(Enum(AuditAction), nullable=False, index=True)
    resource_type = Column(String(50), nullable=True)
    resource_id = Column(Integer, nullable=True)
    old_values = Column(Text, nullable=True)
    new_values = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    request_id = Column(String(36), nullable=True, index=True)
    success = Column(Boolean, default=True, nullable=False)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)


class CreditTransaction(Base):
    __tablename__ = "credit_transactions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    transaction_type = Column(String(20), nullable=False, index=True)  # usage, purchase, bonus, referral_bonus
    amount = Column(Integer, nullable=False)
    balance_before = Column(Integer, nullable=False)
    balance_after = Column(Integer, nullable=False)
    description = Column(String(255), nullable=True)
    reference_id = Column(String(100), nullable=True)
    expires_at = Column(DateTime, nullable=True)  # Novo campo para validade
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="credit_transactions")


class SelicRate(Base):
    __tablename__ = "selic_rates"
    id = Column(Integer, primary_key=True, index=True)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    # Armazena a taxa como um decimal. Ex: 1.16% será 0.0116
    rate = Column(Numeric(10, 5), nullable=False)
    
    __table_args__ = (
        sa.UniqueConstraint('year', 'month', name='_year_month_uc'),
    )


class IPCARate(Base):
    __tablename__ = "ipca_rates"
    id = Column(Integer, primary_key=True, index=True)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    # Armazena a taxa como um decimal fracionário. Ex: 0,40% será 0.0040
    rate = Column(Numeric(10, 6), nullable=False)

    __table_args__ = (
        sa.UniqueConstraint('year', 'month', name='_ipca_year_month_uc'),
    )


# REMOVIDO: Modelo UserPlan foi completamente removido conforme solicitado
