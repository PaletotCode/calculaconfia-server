from datetime import timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Request, BackgroundTasks
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi_cache.decorator import cache

from ..core.database import get_db
from ..core.security import create_access_token, get_current_active_user
from ..core.config import settings
from ..core.background_tasks import send_calculation_email, send_welcome_email
from ..core.logging_config import get_logger, LogContext
from ..models_schemas.models import User, PlanType
from ..models_schemas.schemas import (
    UserCreate,
    UserResponse,
    Token,
    CalculationRequest,
    CalculationResponse,
    QueryHistoryResponse,
    UserPlanResponse,
    UserPlanCreate,
    AuditLogResponse,
    CreditTransactionResponse,
    DashboardStats
)
from ..services.main_service import (
    UserService,
    CalculationService,
    PlanService,
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
    Registra um novo usuário no sistema com plano gratuito
    """
    with LogContext(endpoint="register", email=user_data.email):
        logger.info("User registration request received")
        
        user = await UserService.register_new_user(db, user_data, request)
        
        # Enviar email de boas-vindas em background
        background_tasks.add_task(
            send_welcome_email.delay,
            user.email,
            user.email.split('@')[0]  # Nome baseado no email
        )
        
        return UserResponse(
            id=user.id,
            email=user.email,
            credits=user.credits,
            is_verified=user.is_verified,
            is_active=user.is_active,
            created_at=user.created_at,
            plan_type=PlanType.FREE  # Padrão para novos usuários
        )


@router.post("/login", response_model=Token)
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db)
):
    """
    Autentica o usuário e retorna um token de acesso JWT
    """
    with LogContext(endpoint="login", email=form_data.username):
        logger.info("User login request received")
        
        user = await UserService.authenticate_user(
            db, form_data.username, form_data.password, request
        )
        
        # Buscar informações do plano
        user_plan = await PlanService.get_user_plan(db, user.id)
        
        # Criar token
        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": user.email},
            expires_delta=access_token_expires
        )
        
        user_info = UserResponse(
            id=user.id,
            email=user.email,
            credits=user.credits,
            is_verified=user.is_verified,
            is_active=user.is_active,
            created_at=user.created_at,
            plan_type=user_plan.plan_type if user_plan else PlanType.FREE,
            plan_expires_at=user_plan.expires_at if user_plan else None
        )
        
        return Token(
            access_token=access_token,
            token_type="bearer",
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user_info=user_info
        )


@router.post("/calcular", response_model=CalculationResponse)
async def calcular(
    calculation_data: CalculationRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Executa cálculo de ICMS para o usuário autenticado
    Requer pelo menos 1 crédito disponível
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
        
        # Enviar email com resultado em background
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
    Retorna informações do usuário atual incluindo plano
    """
    user_plan = await PlanService.get_user_plan(db, current_user.id)
    
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        credits=current_user.credits,
        is_verified=current_user.is_verified,
        is_active=current_user.is_active,
        created_at=current_user.created_at,
        plan_type=user_plan.plan_type if user_plan else PlanType.FREE,
        plan_expires_at=user_plan.expires_at if user_plan else None
    )


# ===== ENDPOINTS DE PLANOS =====

@router.get("/planos/me", response_model=UserPlanResponse)
async def get_my_plan(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Retorna o plano atual do usuário
    """
    user_plan = await PlanService.get_user_plan(db, current_user.id)
    
    if not user_plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User plan not found"
        )
    
    return UserPlanResponse(
        id=user_plan.id,
        plan_type=user_plan.plan_type,
        credits_per_month=user_plan.credits_per_month,
        max_calculations_per_day=user_plan.max_calculations_per_day,
        expires_at=user_plan.expires_at,
        is_active=user_plan.is_active,
        created_at=user_plan.created_at
    )


@router.put("/planos/upgrade", response_model=UserPlanResponse)
async def upgrade_plan(
    plan_data: UserPlanCreate,
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Atualiza o plano do usuário (upgrade/downgrade)
    """
    with LogContext(
        endpoint="upgrade_plan",
        user_id=current_user.id,
        new_plan_type=plan_data.plan_type.value
    ):
        logger.info("Plan upgrade request received")
        
        updated_plan = await PlanService.update_user_plan(
            db, current_user.id, plan_data, request
        )
        
        return UserPlanResponse(
            id=updated_plan.id,
            plan_type=updated_plan.plan_type,
            credits_per_month=updated_plan.credits_per_month,
            max_calculations_per_day=updated_plan.max_calculations_per_day,
            expires_at=updated_plan.expires_at,
            is_active=updated_plan.is_active,
            created_at=updated_plan.created_at
        )


# ===== ENDPOINTS ADMINISTRATIVOS =====

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