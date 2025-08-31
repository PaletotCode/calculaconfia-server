from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime
from decimal import Decimal
from .models import PlanType, AuditAction


# ===== User Schemas =====
class UserCreate(BaseModel):
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    id: int
    email: EmailStr
    credits: int
    is_verified: bool
    is_active: bool
    created_at: datetime
    plan_type: PlanType
    plan_expires_at: Optional[datetime] = None

    class Config:
        orm_mode = True

# ===== Token Schemas =====
class Token(BaseModel):
    access_token: str
    token_type: str
    expires_in: int
    user_info: UserResponse

class TokenData(BaseModel):
    email: Optional[str] = None

# ===== Calculation Schemas =====
class CalculationRequest(BaseModel):
    valor_icms: float
    numero_meses: int

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
        orm_mode = True

# ===== Plan Schemas =====
class UserPlanCreate(BaseModel):
    plan_type: PlanType
    credits_per_month: int
    max_calculations_per_day: int
    expires_at: Optional[datetime] = None

class UserPlanResponse(BaseModel):
    id: int
    plan_type: PlanType
    credits_per_month: int
    max_calculations_per_day: int
    expires_at: Optional[datetime]
    is_active: bool
    created_at: datetime

    class Config:
        orm_mode = True

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
        orm_mode = True

class CreditTransactionResponse(BaseModel):
    id: int
    transaction_type: str
    amount: int
    balance_after: int
    description: Optional[str] = None
    created_at: datetime

    class Config:
        orm_mode = True

class DashboardStats(BaseModel):
    total_calculations: int
    total_users: int
    total_credits_used: int
    calculations_today: int
    avg_calculation_time_ms: Optional[float] = None