from datetime import timedelta
from typing import List, Optional

from datetime import datetime
from sqlalchemy import and_, or_
from ..models_schemas.models import VerificationCode, CreditTransaction

from fastapi import APIRouter, Depends, HTTPException, status, Request, BackgroundTasks
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi_cache.decorator import cache

from ..core.database import get_db
from ..core.security import create_access_token, get_current_active_user
from ..core.config import settings
from ..core.background_tasks import send_calculation_email, send_welcome_email
from ..core.logging_config import get_logger, LogContext
from ..models_schemas.models import User
from ..models_schemas.schemas import (
    UserCreate,
    UserResponse,
    Token,
    CalculationRequest,
    CalculationResponse,
    QueryHistoryResponse,
    AuditLogResponse,
    CreditTransactionResponse,
    DashboardStats,
    SendVerificationCodeRequest,
    VerifyAccountRequest,
    RequestPasswordResetRequest,
    ResetPasswordRequest,
    VerificationCodeResponse,
    ReferralStatsResponse
)
from ..services.main_service import (
    UserService,
    CalculationService,
    AnalyticsService
)

router = APIRouter()
logger = get_logger(__name__)


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    user_data: UserCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Registra um novo usuário (inativo até verificação por SMS)
    """
    with LogContext(endpoint="register", phone_number=user_data.phone_number):
        logger.info("User registration request received")
        
        user = await UserService.register_new_user(db, user_data, request)
        
        # Enviar email de boas-vindas apenas se email fornecido
        if user.email:
            background_tasks.add_task(
                send_welcome_email.delay,
                user.email,
                user.first_name or user.phone_number
            )
        
        return UserResponse(
            id=user.id,
            email=user.email,
            phone_number=user.phone_number,
            first_name=user.first_name,
            last_name=user.last_name,
            referral_code=user.referral_code,
            credits=user.credits,
            is_verified=user.is_verified,
            is_active=user.is_active,
            created_at=user.created_at
        )


@router.post("/login", response_model=Token)
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db)
):
    """
    Autentica o usuário com telefone ou email e retorna um token JWT
    """
    with LogContext(endpoint="login", identifier=form_data.username):
        logger.info("User login request received")
        
        user = await UserService.authenticate_user(
            db, form_data.username, form_data.password, request
        )
        
        # Criar token
        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": user.phone_number or user.email},
            expires_delta=access_token_expires
        )
        
        user_info = UserResponse(
            id=user.id,
            email=user.email,
            phone_number=user.phone_number,
            first_name=user.first_name,
            last_name=user.last_name,
            referral_code=user.referral_code,
            credits=user.credits,
            is_verified=user.is_verified,
            is_active=user.is_active,
            created_at=user.created_at
        )
        
        return Token(
            access_token=access_token,
            token_type="bearer",
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user_info=user_info
        )


# ===== NOVOS ENDPOINTS DE VERIFICAÇÃO E SENHA =====

@router.post("/auth/send-verification-code", response_model=VerificationCodeResponse)
async def send_verification_code(
    request_data: SendVerificationCodeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Envia código de verificação por SMS ou email
    """
    with LogContext(endpoint="send_verification_code", identifier=request_data.identifier):
        logger.info("Verification code request received")
        
        response = await UserService.send_verification_code(db, request_data, request)
        
        return response


@router.post("/auth/verify-account", response_model=UserResponse)
async def verify_account(
    request_data: VerifyAccountRequest,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Verifica conta do usuário com código SMS/Email
    """
    with LogContext(endpoint="verify_account", identifier=request_data.identifier):
        logger.info("Account verification request received")
        
        user_response = await UserService.verify_account(db, request_data, request)
        
        return user_response


@router.post("/auth/request-password-reset", response_model=VerificationCodeResponse)
async def request_password_reset(
    request_data: RequestPasswordResetRequest,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Solicita reset de senha por email
    """
    with LogContext(endpoint="request_password_reset", email=request_data.email):
        logger.info("Password reset request received")
        
        response = await UserService.request_password_reset(db, request_data, request)
        
        return response


@router.post("/auth/reset-password")
async def reset_password(
    request_data: ResetPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Reseta senha do usuário com código de verificação
    """
    with LogContext(endpoint="reset_password", email=request_data.email):
        logger.info("Password reset request received")
        
        result = await UserService.reset_password(db, request_data, request)
        
        return result


# ===== ENDPOINTS EXISTENTES ATUALIZADOS =====

@router.post("/calcular", response_model=CalculationResponse)
async def calcular(
    calculation_data: CalculationRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Executa cálculo de ICMS com nova lógica de créditos válidos
    """
    avg_icms = sum(b.icms_value for b in calculation_data.bills) / len(calculation_data.bills)
    with LogContext(
        endpoint="calcular",
        user_id=current_user.id,
        average_icms=avg_icms,
        bill_count=len(calculation_data.bills)
    ):
        logger.info("Calculation request received")
        
        result = await CalculationService.execute_calculation_for_user(
            db, current_user, calculation_data, request
        )
        
        # Enviar email com resultado se email disponível
        if current_user.email:
            email_data = {
                "average_icms": avg_icms,
                "bill_count": len(calculation_data.bills),
                "valor_calculado": result.valor_calculado,
                "creditos_restantes": result.creditos_restantes
            }
            
            background_tasks.add_task(
                send_calculation_email.delay,
                current_user.email,
                email_data
            )
        
        logger.info("Calculation completed successfully",
                   calculation_id=result.calculation_id,
                   processing_time_ms=result.processing_time_ms)
        
        return result


@router.get("/historico", response_model=List[QueryHistoryResponse])
@cache(expire=300)  # Cache por 5 minutos
async def historico(
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Retorna o histórico de cálculos do usuário autenticado (paginado)
    """
    with LogContext(
        endpoint="historico",
        user_id=current_user.id,
        limit=limit,
        offset=offset
    ):
        logger.info("History request received")
        
        # Validar parâmetros de paginação
        if limit > 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Limit cannot exceed 200"
            )
        
        history = await CalculationService.get_user_history(
            db, current_user, limit, offset
        )
        
        return [
            QueryHistoryResponse(
                id=item.id,
                icms_value=item.icms_value,
                months=item.months,
                calculated_value=item.calculated_value,
                calculation_time_ms=item.calculation_time_ms,
                created_at=item.created_at
            )
            for item in history
        ]


@router.get("/me", response_model=UserResponse)
@cache(expire=60)  # Cache por 1 minuto
async def get_current_user_info(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Retorna informações do usuário atual com créditos válidos
    """
    # Atualizar créditos válidos em tempo real
    valid_credits = await CalculationService._get_valid_credits_balance(db, current_user.id)
    
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        phone_number=current_user.phone_number,
        first_name=current_user.first_name,
        last_name=current_user.last_name,
        referral_code=current_user.referral_code,
        credits=valid_credits,  # Mostrar créditos válidos
        is_verified=current_user.is_verified,
        is_active=current_user.is_active,
        created_at=current_user.created_at
    )


# ===== NOVOS ENDPOINTS DE REFERÊNCIA =====

@router.get("/referral/stats", response_model=ReferralStatsResponse)
async def get_referral_stats(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Retorna estatísticas de referência do usuário
    """
    from sqlalchemy import select, func
    
    with LogContext(endpoint="referral_stats", user_id=current_user.id):
        logger.info("Referral stats request received")
        
        # Contar total de referenciados
        stmt = select(func.count(User.id)).where(User.referred_by_id == current_user.id)
        result = await db.execute(stmt)
        total_referrals = result.scalar() or 0
        
        referral_credits_remaining = max(0, 3 - current_user.referral_credits_earned)
        
        return ReferralStatsResponse(
            referral_code=current_user.referral_code,
            total_referrals=total_referrals,
            referral_credits_earned=current_user.referral_credits_earned,
            referral_credits_remaining=referral_credits_remaining
        )


# ===== ENDPOINTS DE CRÉDITOS =====

@router.get("/credits/history", response_model=List[CreditTransactionResponse])
async def get_credit_history(
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Retorna histórico de transações de créditos do usuário
    """
    from sqlalchemy import select, desc
    from ..models_schemas.models import CreditTransaction
    
    with LogContext(
        endpoint="credit_history",
        user_id=current_user.id,
        limit=limit,
        offset=offset
    ):
        logger.info("Credit history request received")
        
        if limit > 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Limit cannot exceed 200"
            )
        
        stmt = select(CreditTransaction).where(
            CreditTransaction.user_id == current_user.id
        ).order_by(
            desc(CreditTransaction.created_at)
        ).limit(limit).offset(offset)
        
        result = await db.execute(stmt)
        transactions = result.scalars().all()
        
        return [
            CreditTransactionResponse(
                id=transaction.id,
                transaction_type=transaction.transaction_type,
                amount=transaction.amount,
                balance_after=transaction.balance_after,
                description=transaction.description,
                expires_at=transaction.expires_at,
                created_at=transaction.created_at
            )
            for transaction in transactions
        ]


@router.get("/credits/balance")
async def get_valid_credits_balance(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Retorna saldo atual de créditos válidos (não expirados)
    """
    with LogContext(endpoint="credits_balance", user_id=current_user.id):
        logger.info("Valid credits balance request received")
        
        valid_balance = await CalculationService._get_valid_credits_balance(db, current_user.id)
        
        return {
            "user_id": current_user.id,
            "valid_credits": valid_balance,
            "legacy_credits": current_user.credits,  # Campo legado para comparação
            "timestamp": datetime.utcnow().isoformat()
        }


# ===== ENDPOINTS ADMINISTRATIVOS ATUALIZADOS =====

@router.get("/admin/dashboard", response_model=DashboardStats)
async def admin_dashboard(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Dashboard administrativo com estatísticas gerais
    TODO: Implementar verificação de role admin
    """
    with LogContext(endpoint="admin_dashboard", user_id=current_user.id):
        logger.info("Admin dashboard request received")
        
        stats = await AnalyticsService.get_dashboard_stats(db)
        return stats


@router.get("/admin/users/{user_id}/audit", response_model=List[AuditLogResponse])
async def get_user_audit_logs(
    user_id: int,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Busca logs de auditoria de um usuário específico
    TODO: Implementar verificação de role admin
    """
    from sqlalchemy import select, desc
    from ..models_schemas.models import AuditLog
    
    with LogContext(
        endpoint="user_audit_logs",
        admin_user_id=current_user.id,
        target_user_id=user_id
    ):
        logger.info("User audit logs request received")
        
        stmt = select(AuditLog).where(
            AuditLog.user_id == user_id
        ).order_by(desc(AuditLog.created_at)).limit(limit).offset(offset)
        
        result = await db.execute(stmt)
        audit_logs = result.scalars().all()
        
        return [
            AuditLogResponse(
                id=log.id,
                action=log.action,
                resource_type=log.resource_type,
                resource_id=log.resource_id,
                ip_address=log.ip_address,
                success=log.success,
                error_message=log.error_message,
                created_at=log.created_at
            )
            for log in audit_logs
        ]


# ===== ENDPOINTS DE HEALTH CHECK =====

@router.get("/health")
async def health_check():
    """
    Endpoint simples para verificação de saúde da API
    """
    return {
        "status": "healthy",
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT
    }


@router.get("/health/detailed")
async def detailed_health_check(db: AsyncSession = Depends(get_db)):
    """
    Verificação detalhada incluindo conectividade do banco
    """
    try:
        # Testar conexão com banco de dados
        await db.execute("SELECT 1")
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    return {
        "status": "healthy" if db_status == "connected" else "unhealthy",
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "database": db_status,
        "cache": "connected",  # TODO: Verificar Redis
        "background_tasks": "connected"  # TODO: Verificar Celery
    }


# ===== ENDPOINTS DE DESENVOLVIMENTO/DEBUG =====

@router.get("/dev/verification-codes")
async def list_verification_codes(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Endpoint de desenvolvimento para listar códigos de verificação
    TODO: Remover em produção
    """
    if settings.ENVIRONMENT != "development":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Endpoint not available in this environment"
        )
    
    from sqlalchemy import select, desc
    from datetime import datetime
    
    stmt = select(VerificationCode).where(
        and_(
            or_(
                VerificationCode.identifier == current_user.phone_number,
                VerificationCode.identifier == current_user.email
            ),
            VerificationCode.expires_at > datetime.utcnow()
        )
    ).order_by(desc(VerificationCode.created_at)).limit(10)
    
    result = await db.execute(stmt)
    codes = result.scalars().all()
    
    return [
        {
            "id": code.id,
            "identifier": code.identifier,
            "code": code.code,
            "type": code.type.value,
            "expires_at": code.expires_at,
            "used": code.used,
            "created_at": code.created_at
        }
        for code in codes
    ]


@router.post("/dev/simulate-referral-payment")
async def simulate_referral_payment(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Endpoint de desenvolvimento para simular pagamento e testar sistema de referência
    TODO: Remover em produção
    """
    if settings.ENVIRONMENT != "development":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Endpoint not available in this environment"
        )
    
    from datetime import datetime, timedelta
    
    try:
        # Simular "pagamento" dando 3 créditos ao usuário
        old_credits = current_user.credits
        current_user.credits += 3
        
        # Registrar transação de "compra"
        expires_at = datetime.utcnow() + timedelta(days=40)
        purchase_transaction = CreditTransaction(
            user_id=current_user.id,
            transaction_type="purchase",
            amount=3,
            balance_before=old_credits,
            balance_after=current_user.credits,
            description="Simulated credit purchase",
            reference_id=f"sim_purchase_{current_user.id}_{int(datetime.utcnow().timestamp())}",
            expires_at=expires_at
        )
        db.add(purchase_transaction)
        
        # Processar bônus de referência
        await CalculationService._process_referral_bonus(db, current_user)
        
        await db.commit()
        
        return {
            "message": "Referral payment simulated successfully",
            "user_id": current_user.id,
            "credits_added": 3,
            "new_balance": current_user.credits
        }
        
    except Exception as e:
        await db.rollback()
        logger.error("Error simulating referral payment", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error simulating payment"
        )


# REMOVIDOS: Todos os endpoints relacionados a planos (/planos/me, /planos/upgrade) conforme solicitado