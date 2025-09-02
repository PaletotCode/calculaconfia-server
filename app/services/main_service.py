from ..models_schemas.models import SelicRate
from dateutil.relativedelta import relativedelta
from typing import List, Optional, Dict, Any
from decimal import Decimal
import time
from datetime import datetime, timedelta

from fastapi import HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, desc
from sqlalchemy.exc import IntegrityError

from ..core.security import get_password_hash, verify_password
from ..core.logging_config import get_logger, LogContext
from ..core.audit import AuditService, SecurityMonitor
from ..models_schemas.models import (
    User, QueryHistory, UserPlan, PlanType, AuditAction, 
    CreditTransaction, AuditLog
)
from ..models_schemas.schemas import (
    UserCreate, CalculationRequest, CalculationResponse, 
    UserPlanCreate, DashboardStats
)

logger = get_logger(__name__)


class UserService:
    """Serviço para gerenciamento de usuários"""
    
    @staticmethod
    async def register_new_user(
        db: AsyncSession, 
        user_data: UserCreate,
        request: Optional[Request] = None
    ) -> User:
        """
        Registra um novo usuário com plano gratuito e auditoria completa
        """
        async with AuditService.audit_context(
            db=db,
            action=AuditAction.REGISTER,
            request=request
        ) as request_id:
            
            try:
                with LogContext(email=user_data.email, request_id=request_id):
                    logger.info("Starting user registration")
                    
                    # Verificar se email já existe
                    stmt = select(User).where(User.email == user_data.email)
                    existing_user = await db.execute(stmt)
                    if existing_user.scalar_one_or_none():
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Email already registered"
                        )
                    
                    # Criar usuário
                    hashed_password = get_password_hash(user_data.password)
                    db_user = User(
                        email=user_data.email,
                        hashed_password=hashed_password,
                        is_verified=True,  # Auto-verificado por enquanto
                        credits=3  # Créditos iniciais
                    )
                    
                    db.add(db_user)
                    await db.flush()  # Para obter o ID
                    
                    # Criar plano gratuito padrão
                    user_plan = UserPlan(
                        user_id=db_user.id,
                        plan_type=PlanType.FREE,
                        credits_per_month=3,
                        max_calculations_per_day=10
                    )
                    
                    db.add(user_plan)
                    
                    # Registrar transação inicial de créditos
                    credit_transaction = CreditTransaction(
                        user_id=db_user.id,
                        transaction_type="bonus",
                        amount=3,
                        balance_before=0,
                        balance_after=3,
                        description="Welcome bonus credits",
                        reference_id=f"welcome_{db_user.id}"
                    )
                    
                    db.add(credit_transaction)
                    
                    await db.commit()
                    await db.refresh(db_user)
                    
                    logger.info("User registered successfully", 
                               user_id=db_user.id,
                               email=user_data.email)
                    
                    return db_user
                    
            except IntegrityError:
                await db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email already registered"
                )
            except Exception as e:
                await db.rollback()
                logger.error("User registration failed", error=str(e))
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Error creating user"
                )

    @staticmethod
    async def authenticate_user(
        db: AsyncSession, 
        email: str, 
        password: str,
        request: Optional[Request] = None
    ) -> User:
        """
        Autentica usuário com monitoramento de segurança
        """
        start_time = time.time()
        
        try:
            with LogContext(email=email):
                logger.info("Starting user authentication")
                
                # Buscar usuário
                stmt = select(User).where(User.email == email)
                result = await db.execute(stmt)
                user = result.scalar_one_or_none()
                
                if not user:
                    # Registrar tentativa de login com email inexistente
                    await AuditService.log_action(
                        db=db,
                        action=AuditAction.LOGIN,
                        request=request,
                        success=False,
                        error_message="User not found"
                    )
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid email or password"
                    )
                
                # Verificar senha
                if not verify_password(password, user.hashed_password):
                    # Registrar tentativa de login com senha incorreta
                    await AuditService.log_action(
                        db=db,
                        action=AuditAction.LOGIN,
                        user_id=user.id,
                        request=request,
                        success=False,
                        error_message="Invalid password"
                    )
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid email or password"
                    )
                
                # Verificar se usuário está ativo
                if not user.is_active:
                    await AuditService.log_action(
                        db=db,
                        action=AuditAction.LOGIN,
                        user_id=user.id,
                        request=request,
                        success=False,
                        error_message="User account deactivated"
                    )
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="User account is deactivated"
                    )
                
                # Verificar atividade suspeita
                if request:
                    ip_address, _ = AuditService.extract_client_info(request)
                    security_check = await SecurityMonitor.check_suspicious_activity(
                        db=db,
                        user_id=user.id,
                        action=AuditAction.LOGIN,
                        ip_address=ip_address
                    )
                    
                    if security_check["risk_level"] == "high":
                        logger.warning("Suspicious login activity detected", 
                                     user_id=user.id,
                                     security_flags=security_check["flags"])
                
                # Registrar login bem-sucedido
                await AuditService.log_action(
                    db=db,
                    action=AuditAction.LOGIN,
                    user_id=user.id,
                    request=request,
                    success=True
                )
                
                auth_time = (time.time() - start_time) * 1000
                logger.info("User authenticated successfully", 
                           user_id=user.id,
                           email=email,
                           auth_time_ms=auth_time)
                
                return user
                
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Authentication error", error=str(e), email=email)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Error authenticating user"
            )


class CalculationService:
    """Serviço para processamento de cálculos"""
    
    @staticmethod
    async def execute_calculation_for_user(
        db: AsyncSession,
        user: User,
        calculation_data: CalculationRequest,
        request: Optional[Request] = None
    ) -> CalculationResponse:
        """
        Executa o cálculo de restituição detalhado com base em faturas
        reais e aplicação da taxa SELIC histórica.
        """
        start_time = time.time()
        PIS_COFINS_FACTOR = Decimal("0.037955")

        async with AuditService.audit_context(
            db=db,
            action=AuditAction.CALCULATION,
            user_id=user.id,
            resource_type="calculation",
            request=request
        ) as request_id:
            try:
                # 1. VALIDAÇÃO E PROCESSAMENTO DA ENTRADA
                if not calculation_data.bills:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, "A lista de faturas não pode estar vazia.")
                if len(calculation_data.bills) > 12:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, "Você pode informar no máximo 12 faturas.")

                provided_bills = {}
                for bill in calculation_data.bills:
                    try:
                        # Usamos o primeiro dia do mês para consistência
                        bill_date = datetime.strptime(bill.issue_date, "%Y-%m").date().replace(day=1)
                        provided_bills[bill_date] = Decimal(str(bill.icms_value))
                    except ValueError:
                        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Formato de data inválido: {bill.issue_date}. Use YYYY-MM.")

                most_recent_date = max(provided_bills.keys())

                # 2. CÁLCULO BASE E MÉDIA
                base_values = {date: icms * PIS_COFINS_FACTOR for date, icms in provided_bills.items()}
                average_base_value = sum(base_values.values()) / len(base_values)

                # 3. PREENCHIMENTO DOS 120 MESES
                all_months_base = {}
                for i in range(120):
                    current_month_date = most_recent_date - relativedelta(months=i)
                    all_months_base[current_month_date] = base_values.get(current_month_date, average_base_value)

                # 4. BUSCA DAS TAXAS SELIC NO BANCO
                start_period_date = most_recent_date - relativedelta(months=119)
                stmt = select(SelicRate).where(
                    and_(
                        func.to_date(SelicRate.year::sa.Text || '-' || SelicRate.month::sa.Text, 'YYYY-MM') >= start_period_date,
                        func.to_date(SelicRate.year::sa.Text || '-' || SelicRate.month::sa.Text, 'YYYY-MM') <= most_recent_date
                    )
                )
                selic_results = await db.execute(stmt)
                # Mapeia data -> taxa para busca rápida
                selic_rates_map = {datetime(r.year, r.month, 1).date(): r.rate for r in selic_results.scalars()}

                if not selic_rates_map:
                    raise HTTPException(status.HTTP_404_NOT_FOUND, "Dados da SELIC não encontrados para o período solicitado. Verifique se o banco foi populado.")

                # 5. APLICAÇÃO DA SELIC E SOMA FINAL
                total_restitution = Decimal("0.0")
                for date, base_value in all_months_base.items():
                    selic_rate = selic_rates_map.get(date, Decimal("0.0"))
                    # Fórmula: valor_corrigido = base * (1 + selic_mensal)
                    corrected_value = base_value * (Decimal("1.0") + selic_rate)
                    total_restitution += corrected_value

                resultado_final = float(total_restitution)

                # Transação atômica para registrar o histórico e decrementar crédito
                async with db.begin_nested():
                    await db.refresh(user)
                    if user.credits <= 0:
                        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Créditos insuficientes")

                    old_credits = user.credits
                    user.credits -= 1

                    ip_address, user_agent = AuditService.extract_client_info(request) if request else (None, None)

                    # Salva um resumo no histórico
                    history_record = QueryHistory(
                        user_id=user.id,
                        icms_value=sum(provided_bills.values()) / len(provided_bills), # Média do ICMS informado
                        months=120, # Sempre calculamos 120 meses
                        calculated_value=Decimal(str(resultado_final)),
                        calculation_time_ms=int((time.time() - start_time) * 1000),
                        ip_address=ip_address,
                        user_agent=user_agent
                    )
                    db.add(history_record)
                    await db.flush()

                    credit_transaction = CreditTransaction(
                        user_id=user.id,
                        transaction_type="usage", amount=-1,
                        balance_before=old_credits, balance_after=user.credits,
                        description="Cálculo detalhado de ICMS",
                        reference_id=f"calc_{history_record.id}"
                    )
                    db.add(credit_transaction)
                    await db.commit()

                total_time_ms = int((time.time() - start_time) * 1000)
                logger.info("Cálculo detalhado concluído", calculation_id=history_record.id, total_time_ms=total_time_ms)

                return CalculationResponse(
                    valor_calculado=resultado_final,
                    creditos_restantes=user.credits,
                    calculation_id=history_record.id,
                    processing_time_ms=total_time_ms
                )

            except HTTPException:
                await db.rollback()
                raise
            except Exception as e:
                await db.rollback()
                logger.error("Falha no processamento do cálculo detalhado", error=str(e), user_id=user.id)
                raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Erro ao processar o cálculo.")
    
    @staticmethod
    async def _get_daily_calculation_count(db: AsyncSession, user_id: int) -> int:
        """Conta cálculos do usuário no dia atual"""
        today = datetime.now().date()
        tomorrow = today + timedelta(days=1)
        
        stmt = select(func.count(QueryHistory.id)).where(
            and_(
                QueryHistory.user_id == user_id,
                QueryHistory.created_at >= today,
                QueryHistory.created_at < tomorrow
            )
        )
        
        result = await db.execute(stmt)
        return result.scalar() or 0

    @staticmethod
    async def get_user_history(
        db: AsyncSession, 
        user: User,
        limit: int = 100,
        offset: int = 0
    ) -> List[QueryHistory]:
        """
        Busca histórico paginado com cache
        """
        try:
            with LogContext(user_id=user.id):
                logger.info("Fetching user calculation history", 
                           limit=limit, 
                           offset=offset)
                
                stmt = select(QueryHistory).where(
                    QueryHistory.user_id == user.id
                ).order_by(
                    desc(QueryHistory.created_at)
                ).limit(limit).offset(offset)
                
                result = await db.execute(stmt)
                history = result.scalars().all()
                
                logger.info("History fetched successfully", 
                           records_count=len(history))
                
                return list(history)
                
        except Exception as e:
            logger.error("Error fetching user history", 
                        error=str(e), 
                        user_id=user.id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Error retrieving user history"
            )


class PlanService:
    """Serviço para gerenciamento de planos"""
    
    @staticmethod
    async def get_user_plan(db: AsyncSession, user_id: int) -> Optional[UserPlan]:
        """Busca o plano ativo do usuário"""
        stmt = select(UserPlan).where(
            and_(
                UserPlan.user_id == user_id,
                UserPlan.is_active == True
            )
        )
        
        result = await db.execute(stmt)
        return result.scalar_one_or_none()
    
    @staticmethod
    async def update_user_plan(
        db: AsyncSession,
        user_id: int,
        plan_data: UserPlanCreate,
        request: Optional[Request] = None
    ) -> UserPlan:
        """
        Atualiza o plano do usuário com auditoria
        """
        async with AuditService.audit_context(
            db=db,
            action=AuditAction.PLAN_CHANGE,
            user_id=user_id,
            resource_type="user_plan",
            request=request
        ):
            
            try:
                # Buscar plano atual
                current_plan = await PlanService.get_user_plan(db, user_id)
                
                if current_plan:
                    # Desativar plano atual
                    current_plan.is_active = False
                    
                # Criar novo plano
                new_plan = UserPlan(
                    user_id=user_id,
                    plan_type=plan_data.plan_type,
                    credits_per_month=plan_data.credits_per_month,
                    max_calculations_per_day=plan_data.max_calculations_per_day,
                    expires_at=plan_data.expires_at,
                    is_active=True
                )
                
                db.add(new_plan)
                await db.commit()
                await db.refresh(new_plan)
                
                logger.info("User plan updated successfully",
                           user_id=user_id,
                           new_plan_type=plan_data.plan_type.value)
                
                return new_plan
                
            except Exception as e:
                await db.rollback()
                logger.error("Failed to update user plan", 
                            error=str(e), 
                            user_id=user_id)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Error updating user plan"
                )


class AnalyticsService:
    """Serviço para analytics e relatórios"""
    
    @staticmethod
    async def get_dashboard_stats(db: AsyncSession) -> DashboardStats:
        """Busca estatísticas para dashboard administrativo"""
        try:
            # Total de cálculos
            total_calc_stmt = select(func.count(QueryHistory.id))
            total_calc_result = await db.execute(total_calc_stmt)
            total_calculations = total_calc_result.scalar()
            
            # Total de usuários
            total_users_stmt = select(func.count(User.id))
            total_users_result = await db.execute(total_users_stmt)
            total_users = total_users_result.scalar()
            
            # Cálculos hoje
            today = datetime.now().date()
            today_calc_stmt = select(func.count(QueryHistory.id)).where(
                QueryHistory.created_at >= today
            )
            today_calc_result = await db.execute(today_calc_stmt)
            calculations_today = today_calc_result.scalar()
            
            # Tempo médio de cálculo
            avg_time_stmt = select(func.avg(QueryHistory.calculation_time_ms)).where(
                QueryHistory.calculation_time_ms.isnot(None)
            )
            avg_time_result = await db.execute(avg_time_stmt)
            avg_calculation_time = avg_time_result.scalar()
            
            return DashboardStats(
                total_calculations=total_calculations or 0,
                total_users=total_users or 0,
                total_credits_used=0,  # Calcular baseado em transações
                calculations_today=calculations_today or 0,
                avg_calculation_time_ms=float(avg_calculation_time) if avg_calculation_time else None
            )
            
        except Exception as e:
            logger.error("Error fetching dashboard stats", error=str(e))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Error retrieving dashboard statistics"
            )