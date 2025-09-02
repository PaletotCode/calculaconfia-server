from pydantic import BaseModel, EmailStr, validator
from typing import Optional, List
from datetime import datetime
from decimal import Decimal
from .models import AuditAction, VerificationType


# ===== User Schemas =====
class UserCreate(BaseModel):
    phone_number: str  # Agora obrigatório
    password: str      # Obrigatório
    email: Optional[EmailStr] = None  # Opcional
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    applied_referral_code: Optional[str] = None

    @validator('phone_number')
    def validate_phone(cls, v):
        # Validação básica de telefone brasileiro
        import re
        phone_pattern = r'^(\+55|55)?[1-9]{2}9?[0-9]{8}$'
        if not re.match(phone_pattern, v.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')):
            raise ValueError('Formato de telefone inválido')
        return v


class UserResponse(BaseModel):
    id: int
    email: Optional[EmailStr]
    phone_number: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    referral_code: Optional[str]
    credits: int
    is_verified: bool
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


# ===== Token Schemas =====
class Token(BaseModel):
    access_token: str
    token_type: str
    expires_in: int
    user_info: UserResponse


class TokenData(BaseModel):
    identifier: Optional[str] = None  # Email ou telefone


# ===== Verification Schemas =====
class SendVerificationCodeRequest(BaseModel):
    identifier: str  # Phone number ou email
    
    @validator('identifier')
    def validate_identifier(cls, v):
        # Verificar se é email ou telefone
        import re
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        phone_pattern = r'^(\+55|55)?[1-9]{2}9?[0-9]{8}$'
        
        clean_identifier = v.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
        
        if not (re.match(email_pattern, v) or re.match(phone_pattern, clean_identifier)):
            raise ValueError('Deve ser um email válido ou telefone brasileiro válido')
        return v


class VerifyAccountRequest(BaseModel):
    identifier: str
    code: str
    
    @validator('code')
    def validate_code(cls, v):
        if len(v) != 6 or not v.isdigit():
            raise ValueError('Código deve conter exatamente 6 dígitos')
        return v


class RequestPasswordResetRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str
    new_password: str
    
    @validator('code')
    def validate_code(cls, v):
        if len(v) != 6 or not v.isdigit():
            raise ValueError('Código deve conter exatamente 6 dígitos')
        return v


class VerificationCodeResponse(BaseModel):
    message: str
    expires_in_minutes: int = 5


# ===== Calculation Schemas =====
class BillInput(BaseModel):
    icms_value: float
    issue_date: str  # Formato "YYYY-MM"


class CalculationRequest(BaseModel):
    bills: List[BillInput]


class CalculationResponse(BaseModel):
    valor_calculado: float
    creditos_restantes: int
    calculation_id: int
    processing_time_ms: int


# ===== History Schemas =====
class QueryHistoryResponse(BaseModel):
    id: int
    icms_value: Decimal
    months: int
    calculated_value: Decimal
    calculation_time_ms: Optional[int]
    created_at: datetime

    class Config:
        from_attributes = True


# ===== Admin/Audit Schemas =====
class AuditLogResponse(BaseModel):
    id: int
    action: AuditAction
    resource_type: Optional[str] = None
    resource_id: Optional[int] = None
    ip_address: Optional[str] = None
    success: bool
    error_message: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class CreditTransactionResponse(BaseModel):
    id: int
    transaction_type: str
    amount: int
    balance_after: int
    description: Optional[str] = None
    expires_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class DashboardStats(BaseModel):
    total_calculations: int
    total_users: int
    total_credits_used: int
    calculations_today: int
    avg_calculation_time_ms: Optional[float] = None


# ===== Referral Schemas =====
class ReferralStatsResponse(BaseModel):
    referral_code: str
    total_referrals: int
    referral_credits_earned: int
    referral_credits_remaining: int  # Máximo 3


# Schemas removidos: UserPlanCreate, UserPlanResponse (conforme solicitado)