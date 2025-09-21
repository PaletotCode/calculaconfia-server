from datetime import timedelta
from typing import List, Optional

from ..services import payment_service
from ..services.credit_service import CreditService # Importa o novo serviço

from fastapi.responses import JSONResponse

from datetime import datetime
from sqlalchemy import and_
from ..models_schemas.models import VerificationCode, CreditTransaction, VerificationType

from fastapi import APIRouter, Depends, HTTPException, status, Request, BackgroundTasks, Response
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi_cache.decorator import cache

from ..core.database import get_db
from ..core.security import (
    create_access_token,
    get_current_active_user,
    get_current_admin_user,
)
from ..core.config import settings
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
    ReferralStatsResponse,
)
from pydantic import BaseModel
from ..services.main_service import (
    UserService,
    CalculationService,
    AnalyticsService
)

router = APIRouter()
logger = get_logger(__name__)


class PaymentConfirmationRequest(BaseModel):
    payment_id: str
    status: Optional[str] = None
    preference_id: Optional[str] = None


class PaymentConfirmationResponse(BaseModel):
    payment_id: str
    status: Optional[str] = None
    credits_added: bool
    already_processed: bool
    credits_balance: Optional[int] = None
    detail: Optional[str] = None


class RegistrationResponse(BaseModel):
    message: str
    requires_verification: bool = True
    expires_in_minutes: int = 10


@router.post("/register", response_model=RegistrationResponse, status_code=status.HTTP_201_CREATED)
async def register(
    user_data: UserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Registra um novo usuário e envia o código de verificação por e-mail.
    """
    with LogContext(endpoint="register", email=user_data.email):
        logger.info("User registration request received")
        
        user = await UserService.register_new_user(db, user_data, request)

        return RegistrationResponse(
            message="Conta criada! Enviamos um código de verificação para o seu e-mail.",
            requires_verification=not user.is_verified,
            expires_in_minutes=10
        )


@router.post("/login", response_model=Token)
async def login(
    request: Request,
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db)
):
    """
    Autentica o usuário por email e grava o token em cookie HTTP-only
    """
    with LogContext(endpoint="login", identifier=form_data.username):
        logger.info("User login request received")
        
        user = await UserService.authenticate_user(
            db, form_data.username, form_data.password, request
        )
        
        # Criar token
        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": user.email},
            expires_delta=access_token_expires
        )
        
        valid_credits = await CalculationService._get_valid_credits_balance(db, user.id)
        user.credits = valid_credits
        user_info = UserResponse(
            id=user.id,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            referral_code=user.referral_code,
            credits=valid_credits,
            is_verified=user.is_verified,
            is_active=user.is_active,
            is_admin=user.is_admin,
            created_at=user.created_at
        )

        # Gravar token em cookie HTTP-only
        cookie_kwargs = {
            "key": "access_token",
            "value": access_token,
            "httponly": True,
            "max_age": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "expires": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "secure": settings.ENVIRONMENT == "production",
            "samesite": "none" if settings.ENVIRONMENT == "production" else "lax",
        }

        if settings.COOKIE_DOMAIN:
            cookie_kwargs["domain"] = settings.COOKIE_DOMAIN

        response.set_cookie(**cookie_kwargs)
        
        return Token(
            access_token=access_token,
            token_type="bearer",
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user_info=user_info
        )
    
@router.post("/logout")
async def logout(response: Response):
    """Remove token do cookie"""
    response.delete_cookie("access_token")
    return {"message": "Logged out"}


# ===== NOVOS ENDPOINTS DE VERIFICAÇÃO E SENHA =====

@router.post("/auth/send-verification-code", response_model=VerificationCodeResponse)
async def send_verification_code(
    request_data: SendVerificationCodeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Envia código de verificação por email
    """
    with LogContext(endpoint="send_verification_code", email=request_data.email):
        logger.info("Verification code request received")
        
        # Reuso do fluxo de registro já envia email. Aqui, reenvio do código.
        # Implementado no UserService.verify_account/request_password_reset, mas mantemos a assinatura.
        # Para compatibilidade, vamos delegar para request_password_reset-like (envio de código de verificação).
        from ..services.main_service import UserService as _US
        class _Req(BaseModel):
            email: str
        # Gera código de verificação de conta (não redefinição de senha)
        from sqlalchemy import select, and_
        from ..models_schemas.models import VerificationCode
        from ..services.main_service import UserService as US
        from ..core.background_tasks import send_verification_email
        from datetime import datetime, timedelta
        from fastapi import HTTPException

        # Checa existência do usuário para reenvio
        from sqlalchemy import select as _select
        from ..models_schemas.models import User as _User
        res = await db.execute(_select(_User).where(_User.email == request_data.email))
        if not res.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="User not found")

        # Invalida códigos anteriores não usados
        from sqlalchemy import select as s, and_ as a
        existing = await db.execute(s(VerificationCode).where(
            a(
                VerificationCode.identifier == request_data.email,
                VerificationCode.used == False,
                VerificationCode.expires_at > datetime.utcnow()
            )
        ))
        for c in existing.scalars().all():
            c.used = True

        code = UserService._generate_verification_code()
        expires_at = datetime.utcnow() + timedelta(minutes=10)
        db.add(VerificationCode(
            identifier=request_data.email,
            code=code,
            expires_at=expires_at,
            type=VerificationType.EMAIL
        ))
        await db.commit()
        send_verification_email(request_data.email, code)
        response = VerificationCodeResponse(message="Verification code sent", expires_in_minutes=10)
        
        return response


@router.post("/auth/verify-account", response_model=Token)
async def verify_account(
    request_data: VerifyAccountRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db)
):
    """
    Verifica conta do usuário com código SMS/Email e autentica a sessão.
    """
    with LogContext(endpoint="verify_account", email=request_data.email):
        logger.info("Account verification request received")
        
        user_response = await UserService.verify_account(db, request_data, request)

        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        identifier = user_response.email or request_data.email
        access_token = create_access_token(
            data={"sub": identifier},
            expires_delta=access_token_expires
        )

        cookie_kwargs = {
            "key": "access_token",
            "value": access_token,
            "httponly": True,
            "max_age": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "expires": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "secure": settings.ENVIRONMENT == "production",
            "samesite": "none" if settings.ENVIRONMENT == "production" else "lax",
        }

        if settings.COOKIE_DOMAIN:
            cookie_kwargs["domain"] = settings.COOKIE_DOMAIN

        response.set_cookie(**cookie_kwargs)

        return Token(
            access_token=access_token,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user_info=user_response,
            token_type="bearer"
        )


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
        first_name=current_user.first_name,
        last_name=current_user.last_name,
        referral_code=current_user.referral_code,
        credits=valid_credits,  # Mostrar créditos válidos
        is_verified=current_user.is_verified,
        is_active=current_user.is_active,
        is_admin=current_user.is_admin,
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
        
        # Novo limite: código de indicação é uso único, logo no máximo 1 crédito possível
        referral_credits_remaining = 0 if current_user.referral_credits_earned >= 1 else 1
        
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
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Dashboard administrativo com estatísticas gerais
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
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Busca logs de auditoria de um usuário específico
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
            VerificationCode.identifier == current_user.email,
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
        balance_before = await CalculationService._get_valid_credits_balance(db, current_user.id)
        purchase_transaction = CreditTransaction(
            user_id=current_user.id,
            transaction_type="purchase",
            amount=3,
            balance_before=balance_before,
            balance_after=balance_before + 3,
            description="Simulated credit purchase",
            reference_id=f"sim_purchase_{current_user.id}_{int(datetime.utcnow().timestamp())}",
            expires_at=datetime.utcnow() + timedelta(days=40)
        )
        db.add(purchase_transaction)
        
        await CreditService._refresh_user_legacy_balance(db, current_user)
        
        # Processar bônus de referência
        await CreditService._process_referral_bonus(db, current_user)
        
        await db.commit()
        
        new_balance = await CalculationService._get_valid_credits_balance(db, current_user.id)
        
        return {
            "message": "Referral payment simulated successfully",
            "user_id": current_user.id,
            "credits_added": 3,
            "new_balance": new_balance
        }
        
    except Exception as e:
        await db.rollback()
        logger.error("Error simulating referral payment", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error simulating payment"
        )

# Adicionar este endpoint em app/api/endpoints.py para debug

@router.get("/dev/sendgrid-status")
async def sendgrid_debug_status():
    """
    Endpoint de debug para verificar configuração do SendGrid
    TODO: Remover em produção
    """
    if settings.ENVIRONMENT != "development":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Endpoint not available in this environment"
        )
    
    import os
    from sendgrid import SendGridAPIClient
    
    # Verificar diferentes formas de carregar a chave
    key_from_settings = settings.SENDGRID_API_KEY
    key_from_env = os.getenv('SENDGRID_API_KEY')
    key_from_environ = os.environ.get('SENDGRID_API_KEY')
    
    # Status da chave
    key_status = {
        "from_pydantic_settings": "✅ Available" if key_from_settings else "❌ Missing",
        "from_os_getenv": "✅ Available" if key_from_env else "❌ Missing", 
        "from_os_environ": "✅ Available" if key_from_environ else "❌ Missing",
    }
    
    # Verificar se as chaves são iguais (se existirem)
    keys_match = None
    if key_from_settings and key_from_env:
        keys_match = key_from_settings == key_from_env
    
    # Testar conexão com SendGrid (se chave disponível)
    sendgrid_test = {"status": "not_tested"}
    active_key = key_from_settings or key_from_env or key_from_environ
    
    if active_key:
        try:
            sg = SendGridAPIClient(active_key)
            # Fazer uma chamada simples para testar a chave
            response = sg.client.user.email.get()
            sendgrid_test = {
                "status": "✅ API Key Valid",
                "status_code": response.status_code,
                "user_email": response.body.decode() if response.body else "N/A"
            }
        except Exception as e:
            sendgrid_test = {
                "status": "❌ API Key Invalid",
                "error": str(e)
            }
    
    # Mascarar chaves para segurança (mostrar apenas início e fim)
    def mask_key(key):
        if not key:
            return None
        if len(key) > 20:
            return f"{key[:10]}...{key[-6:]}"
        return f"{key[:4]}...{key[-2:]}"
    
    return {
        "sendgrid_configuration": {
            "mail_from": settings.MAIL_FROM,
            "mail_from_name": settings.MAIL_FROM_NAME,
            "environment": settings.ENVIRONMENT
        },
        "api_key_status": key_status,
        "api_key_values": {
            "from_pydantic_settings": mask_key(key_from_settings),
            "from_os_getenv": mask_key(key_from_env),
            "from_os_environ": mask_key(key_from_environ)
        },
        "keys_consistency": {
            "all_sources_match": keys_match,
            "active_key_source": "pydantic_settings" if key_from_settings else ("os_getenv" if key_from_env else "os_environ" if key_from_environ else "none")
        },
        "sendgrid_api_test": sendgrid_test,
        "recommendations": [
            "✅ Ensure SENDGRID_API_KEY is set in .env file",
            "✅ Verify sender email is authenticated in SendGrid dashboard", 
            "✅ Check SendGrid API key permissions",
            "✅ Monitor SendGrid usage quotas"
        ]
    }


@router.post("/dev/test-email")
async def test_email_sending(
    email: str = "teste@example.com"
):
    """
    Endpoint para testar envio de email diretamente
    TODO: Remover em produção
    """
    if settings.ENVIRONMENT != "development":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Endpoint not available in this environment"
        )
    
    from ..core.background_tasks import send_email_task
    
    try:
        # Enviar email de teste diretamente (sem Celery)
        result = send_email_task.delay(
            to_email=email,
            subject="🧪 Teste de Email - Torres Project",
            html_content="""
            <h2>Teste de Email</h2>
            <p>Este é um email de teste do sistema Torres Project.</p>
            <p><strong>Se você recebeu este email, a configuração está funcionando!</strong></p>
            <hr>
            <small>Este é um email automático de teste.</small>
            """
        )
        
        return {
            "message": "Test email queued successfully",
            "task_id": result.id,
            "target_email": email,
            "check_logs": "Monitor celery worker logs for detailed status"
        }
        
    except Exception as e:
        logger.error("Test email failed", error=str(e))
        return {
            "message": "Test email failed",
            "error": str(e),
            "target_email": email
        }


# ===== ENDPOINTS DE PAGAMENTO =====

@router.post("/payments/create-order")
async def create_payment_order(current_user: User = Depends(get_current_active_user)):
    """
    Cria uma ordem de pagamento para o pacote padrão de 3 créditos.
    Control F Amigável: create-order
    """
    try:
        # Detalhes do item a ser vendido. Pode ser expandido para receber diferentes pacotes.
        item_details = {
            "id": "CREDITS-PACK-3",
            "title": "Pacote Padrão de 3 Créditos",
            "price":5.00,
            "credits": 3
        }
        
        preference = payment_service.create_payment_preference(current_user, item_details)
        # Retorna o ID da preferência e o ponto de inicialização do checkout
        return {
            "preference_id": preference["id"],
            "init_point": preference["init_point"]
        }
    except Exception as e:
        logger.error("Falha ao criar ordem de pagamento.", error=str(e), user_id=current_user.id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Não foi possível iniciar o pagamento.")


@router.post("/payments/confirm", response_model=PaymentConfirmationResponse)
async def confirm_payment_status(
    payload: PaymentConfirmationRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Permite que o frontend valide e sincronize imediatamente um pagamento retornado pelo Checkout Pro."""
    payment_id = payload.payment_id.strip()
    if not payment_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="payment_id é obrigatório",
        )

    result = await payment_service.process_payment_and_award(
        payment_id=payment_id,
        db=db,
        expected_user_id=current_user.id,
    )

    if result.detail == "unexpected_user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Pagamento não pertence ao usuário autenticado",
        )

    # Se o pagamento ainda estiver pendente, apenas informamos o status ao frontend.
    credits_balance = await CalculationService._get_valid_credits_balance(db, current_user.id)

    return PaymentConfirmationResponse(
        payment_id=result.payment_id,
        status=result.status,
        credits_added=result.processed,
        already_processed=result.already_processed,
        credits_balance=credits_balance,
        detail=result.detail or payload.status,
    )


@router.post("/payments/webhook", status_code=status.HTTP_200_OK)
async def mercado_pago_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Webhook para receber notificações de pagamento do Mercado Pago.
    Control F Amigável: webhook
    """
    # A lógica de processamento foi movida para o payment_service para melhor organização.
    await payment_service.handle_webhook_notification(request, db)
    # Sempre retorna 200 OK para o Mercado Pago para confirmar o recebimento.
    return JSONResponse(content={"status": "notification received"})

# Alguns ambientes do Mercado Pago ainda disparam GET com query params (formato legado)
@router.get("/payments/webhook", status_code=status.HTTP_200_OK)
async def mercado_pago_webhook_get(request: Request, db: AsyncSession = Depends(get_db)):
    await payment_service.handle_webhook_notification(request, db)
    return JSONResponse(content={"status": "notification received"})

# --------------------------------------------------------------------------------
# --- Controle F: FIM - ENDPOINTS DE PAGAMENTO
