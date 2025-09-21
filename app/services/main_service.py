from ..models_schemas.models import SelicRate, VerificationCode, VerificationType
from dateutil.relativedelta import relativedelta
from ..models_schemas.schemas import UserResponse 
from ..models_schemas.models import VerificationCode, CreditTransaction
from typing import List, Optional, Dict, Any
from decimal import Decimal
import time
import random
import string
from datetime import datetime, timedelta
from sqlalchemy import cast, or_
import sqlalchemy as sa
from ..core.background_tasks import send_verification_email, send_password_reset_email

from fastapi import HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, desc
from sqlalchemy.exc import IntegrityError

from ..core.security import get_password_hash, verify_password
from ..core.logging_config import get_logger, LogContext
from ..core.audit import AuditService, SecurityMonitor
from ..models_schemas.models import (
    User, QueryHistory, AuditAction, 
    CreditTransaction, AuditLog, SelicRate, IPCARate
)
from ..models_schemas.schemas import (
    UserCreate, CalculationRequest, CalculationResponse,
    DashboardStats, SendVerificationCodeRequest, VerifyAccountRequest,
    RequestPasswordResetRequest, ResetPasswordRequest, VerificationCodeResponse
)

from .calculation_engine import compute_total_refund

logger = get_logger(__name__)


class UserService:
    """Serviço para gerenciamento de usuários com autenticação por email"""
    
    @staticmethod
    def _generate_referral_code(first_name: Optional[str] = None, user_id: Optional[int] = None) -> str:
        """Gera um código de referência único"""
        if first_name:
            base = first_name[:3].upper()
        else:
            base = "USR"
        
        # Adiciona números aleatórios
        numbers = ''.join(random.choices(string.digits, k=4))
        if user_id:
            numbers = str(user_id).zfill(4)[-4:]
        
        return f"{base}{numbers}"
    
    @staticmethod
    def _generate_verification_code() -> str:
        """Gera código de verificação de 6 dígitos"""
        return ''.join(random.choices(string.digits, k=6))
    
    @staticmethod
    async def register_new_user(
        db: AsyncSession, 
        user_data: UserCreate,
        request: Optional[Request] = None
    ) -> User:
        """
        Registra um novo usuário SEM gerar código de referência.
        """
        async with AuditService.audit_context(
            db=db,
            action=AuditAction.REGISTER,
            request=request
        ) as request_id:

            try:
                with LogContext(email=user_data.email, request_id=request_id):
                    logger.info("Starting user registration")

                    # Validações de usuário existente (email obrigatório e único)
                    stmt = select(User).where(User.email == user_data.email)
                    existing_email = await db.execute(stmt)
                    if existing_email.scalar_one_or_none():
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Email already registered"
                        )

                    # Validação do código de referência aplicado
                    referred_by = None
                    if user_data.applied_referral_code:
                        # Dono do código
                        stmt = select(User).where(User.referral_code == user_data.applied_referral_code)
                        referrer_result = await db.execute(stmt)
                        referred_by = referrer_result.scalar_one_or_none()
                        if not referred_by:
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Invalid referral code"
                            )
                        # Uso único: verifica se já existe algum usuário que usou este código
                        stmt_used = select(User).where(User.referred_by_id == referred_by.id)
                        used_result = await db.execute(stmt_used)
                        if used_result.scalar_one_or_none():
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Código já resgatado!"
                            )

                    # Cria o usuário
                    hashed_password = get_password_hash(user_data.password)
                    db_user = User(
                        email=user_data.email,
                        hashed_password=hashed_password,
                        first_name=user_data.first_name,
                        last_name=user_data.last_name,
                        referred_by_id=referred_by.id if referred_by else None,
                        is_verified=False,
                        is_active=False,
                        credits=0
                        # O CÓDIGO DE REFERÊNCIA NÃO É MAIS GERADO AQUI
                    )

                    db.add(db_user)

                    verification_code = UserService._generate_verification_code()
                    expires_at = datetime.utcnow() + timedelta(minutes=10)
                    verification_record = VerificationCode(
                        identifier=user_data.email,
                        code=verification_code,
                        expires_at=expires_at,
                        type=VerificationType.EMAIL
                    )
                    db.add(verification_record)

                    await db.commit()
                    await db.refresh(db_user)

                    try:
                        send_verification_email(db_user.email, verification_code)
                    except Exception as e:
                        logger.error(
                            "Failed to queue verification email",
                            error=str(e)
                        )

                    logger.info("User registered; verification email queued", user_id=db_user.id)

                    return db_user

            except IntegrityError:
                await db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Phone number or email already registered"
                )
            except Exception as e:
                await db.rollback()
                logger.error("User registration failed", error=str(e))
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Error creating user"
                )

        # app/services/main_service.py

    @staticmethod
    async def verify_account(
        db: AsyncSession,
        request_data: VerifyAccountRequest,
        request: Optional[Request] = None
    ) -> UserResponse:
        """
        Verifica a conta do usuário, tornando-a ativa, mas NÃO concede créditos.
        """
        try:
            identifier = request_data.email.strip()
            code = request_data.code

            # Buscar código válido
            stmt = select(VerificationCode).where(
                and_(
                    VerificationCode.identifier == identifier,
                    VerificationCode.code == code,
                    VerificationCode.used == False,
                    VerificationCode.expires_at > datetime.utcnow()
                )
            )
            verification_record = await db.execute(stmt)
            verification = verification_record.scalar_one_or_none()

            if not verification:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid or expired verification code"
                )

            # Buscar usuário (por email)
            stmt_user = select(User).where(User.email == identifier)

            user_result = await db.execute(stmt_user)
            user = user_result.scalar_one_or_none()

            if not user:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found"
                )

            # Apenas ativa o usuário. NENHUM CRÉDITO É CONCEDIDO AQUI.
            user.is_verified = True
            user.is_active = True
            verification.used = True

            await db.commit()

            await AuditService.log_action(
                db=db,
                action=AuditAction.VERIFICATION,
                user_id=user.id,
                request=request,
                success=True,
                resource_type="user_account"
            )

            logger.info("Account verified successfully", user_id=user.id)

            return UserResponse(
                id=user.id,
                email=user.email,
                first_name=user.first_name,
                last_name=user.last_name,
                referral_code=user.referral_code, # Será None
                credits=user.credits,             # Será 0
                is_verified=user.is_verified,
                is_active=user.is_active,
                is_admin=user.is_admin,
                created_at=user.created_at
            )

        except HTTPException:
            raise
        except Exception as e:
            await db.rollback()
            logger.error("Account verification failed", error=str(e))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Error verifying account"
            )
    
    @staticmethod
    async def request_password_reset(
        db: AsyncSession,
        request_data: RequestPasswordResetRequest,
        request: Optional[Request] = None
    ) -> VerificationCodeResponse:
        """
        Solicita reset de senha por email
        """
        try:
            email = request_data.email
            
            # Verificar se usuário existe
            stmt = select(User).where(User.email == email)
            user_result = await db.execute(stmt)
            user = user_result.scalar_one_or_none()
            
            if not user:
                # Por segurança, não revelar que email não existe
                logger.warning("Password reset requested for non-existent email", email=email)
                return VerificationCodeResponse(
                    message="If email exists, verification code was sent",
                    expires_in_minutes=5
                )
            
            # Invalidar códigos anteriores
            stmt = select(VerificationCode).where(
                and_(
                    VerificationCode.identifier == email,
                    VerificationCode.type == VerificationType.EMAIL,
                    VerificationCode.used == False,
                    VerificationCode.expires_at > datetime.utcnow()
                )
            )
            existing_codes = await db.execute(stmt)
            for code in existing_codes.scalars():
                code.used = True
            
            # Gerar código
            verification_code = UserService._generate_verification_code()
            expires_at = datetime.utcnow() + timedelta(minutes=5)
            
            verification_record = VerificationCode(
                identifier=email,
                code=verification_code,
                expires_at=expires_at,
                type=VerificationType.EMAIL
            )
            
            db.add(verification_record)
            await db.commit()
            
            # Simular envio de email
            logger.info("Password reset code sent", email=email, code=verification_code)
            send_password_reset_email(email, verification_code)
            
            await AuditService.log_action(
                db=db,
                action=AuditAction.PASSWORD_RESET,
                user_id=user.id,
                request=request,
                success=True
            )
            
            return VerificationCodeResponse(
                message="If email exists, verification code was sent",
                expires_in_minutes=5
            )
            
        except Exception as e:
            logger.error("Password reset request failed", error=str(e))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Error processing password reset request"
            )
    
    @staticmethod
    async def reset_password(
        db: AsyncSession,
        request_data: ResetPasswordRequest,
        request: Optional[Request] = None
    ) -> Dict[str, str]:
        """
        Reseta senha do usuário
        """
        try:
            email = request_data.email
            code = request_data.code
            new_password = request_data.new_password
            
            # Verificar código
            stmt = select(VerificationCode).where(
                and_(
                    VerificationCode.identifier == email,
                    VerificationCode.code == code,
                    VerificationCode.type == VerificationType.EMAIL,
                    VerificationCode.used == False,
                    VerificationCode.expires_at > datetime.utcnow()
                )
            )
            verification_result = await db.execute(stmt)
            verification = verification_result.scalar_one_or_none()
            
            if not verification:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid or expired verification code"
                )
            
            # Buscar usuário
            stmt = select(User).where(User.email == email)
            user_result = await db.execute(stmt)
            user = user_result.scalar_one_or_none()
            
            if not user:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found"
                )
            
            # Atualizar senha
            user.hashed_password = get_password_hash(new_password)
            verification.used = True
            
            await db.commit()
            
            await AuditService.log_action(
                db=db,
                action=AuditAction.PASSWORD_CHANGE,
                user_id=user.id,
                request=request,
                success=True
            )
            
            logger.info("Password reset successfully", user_id=user.id)
            
            return {"message": "Password reset successfully"}
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Password reset failed", error=str(e))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Error resetting password"
            )

    @staticmethod
    async def authenticate_user(
        db: AsyncSession, 
        identifier: str,  # Email
        password: str,
        request: Optional[Request] = None
    ) -> User:
        """
        Autentica usuário com email
        """
        start_time = time.time()
        
        try:
            with LogContext(identifier=identifier):
                logger.info("Starting user authentication")
                
                # Buscar usuário por email
                stmt = select(User).where(User.email == identifier)
                result = await db.execute(stmt)
                user = result.scalar_one_or_none()
                
                if not user:
                    await AuditService.log_action(
                        db=db,
                        action=AuditAction.LOGIN,
                        request=request,
                        success=False,
                        error_message="User not found"
                    )
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid credentials"
                    )
                
                # Verificar senha
                if not verify_password(password, user.hashed_password):
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
                        detail="Invalid credentials"
                    )
                
                # Verificar se usuário está ativo e verificado
                if not user.is_active or not user.is_verified:
                    await AuditService.log_action(
                        db=db,
                        action=AuditAction.LOGIN,
                        user_id=user.id,
                        request=request,
                        success=False,
                        error_message="Account not verified or inactive"
                    )
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Account not verified or inactive"
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
                           identifier=identifier,
                           auth_time_ms=auth_time)
                
                return user
                
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Authentication error", error=str(e), identifier=identifier)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Error authenticating user"
            )


class CalculationService:
    """Serviço para processamento de cálculos com validação de créditos em tempo real"""
    
    @staticmethod
    async def _get_valid_credits_balance(db: AsyncSession, user_id: int) -> int:
        """
        Calcula saldo de créditos válidos em tempo real
        """
        current_time = datetime.utcnow()
        
        # Somar todas as transações de crédito que não expiraram
        stmt = select(func.sum(CreditTransaction.amount)).where(
            and_(
                CreditTransaction.user_id == user_id,
                or_(
                    CreditTransaction.expires_at.is_(None),
                    CreditTransaction.expires_at > current_time
                )
            )
        )
        
        result = await db.execute(stmt)
        balance = result.scalar() or 0
        
        return max(0, balance)  # Garantir que nunca seja negativo
    
    @staticmethod
    async def execute_calculation_for_user(
        db: AsyncSession,
        user: User,
        calculation_data: CalculationRequest,
        request: Optional[Request] = None
    ) -> CalculationResponse:
        """
        Executa o cálculo com nova lógica de créditos válidos
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
                # Verificar créditos válidos em tempo real
                valid_credits = await CalculationService._get_valid_credits_balance(db, user.id)
                
                if valid_credits <= 0:
                    raise HTTPException(
                        status_code=status.HTTP_402_PAYMENT_REQUIRED,
                        detail="Insufficient valid credits"
                    )
                
                # Validações de entrada
                if not calculation_data.bills:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, "A lista de faturas não pode estar vazia.")
                if len(calculation_data.bills) > 12:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, "Você pode informar no máximo 12 faturas.")

                provided_bills = {}
                for bill in calculation_data.bills:
                    try:
                        bill_date = datetime.strptime(bill.issue_date, "%Y-%m").date().replace(day=1)
                        provided_bills[bill_date] = Decimal(str(bill.icms_value))
                    except ValueError:
                        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Formato de data inválido: {bill.issue_date}. Use YYYY-MM.")

                most_recent_date = max(provided_bills.keys())

                # Período de 120 meses
                start_period_date = most_recent_date - relativedelta(months=119)

                # Buscar taxas IPCA do período
                ipca_stmt = select(IPCARate).where(
                    and_(
                        func.to_date(
                            cast(IPCARate.year, sa.Text) + '-' + cast(IPCARate.month, sa.Text),
                            'YYYY-MM'
                        ) >= start_period_date,
                        func.to_date(
                            cast(IPCARate.year, sa.Text) + '-' + cast(IPCARate.month, sa.Text),
                            'YYYY-MM'
                        ) <= most_recent_date
                    )
                )
                ipca_results = await db.execute(ipca_stmt)
                ipca_rates_map = {datetime(r.year, r.month, 1).date(): r.rate for r in ipca_results.scalars()}

                if not ipca_rates_map:
                    raise HTTPException(status.HTTP_404_NOT_FOUND, "Dados do IPCA não encontrados para o período solicitado.")

                # Buscar taxas SELIC do período
                selic_stmt = select(SelicRate).where(
                    and_(
                        func.to_date(
                            cast(SelicRate.year, sa.Text) + '-' + cast(SelicRate.month, sa.Text),
                            'YYYY-MM'
                        ) >= start_period_date,
                        func.to_date(
                            cast(SelicRate.year, sa.Text) + '-' + cast(SelicRate.month, sa.Text),
                            'YYYY-MM'
                        ) <= most_recent_date
                    )
                )
                selic_results = await db.execute(selic_stmt)
                selic_rates_map = {datetime(r.year, r.month, 1).date(): r.rate for r in selic_results.scalars()}

                if not selic_rates_map:
                    # Mantém compatibilidade: se faltar SELIC, assume 0% (mas recomenda-se popular)
                    selic_rates_map = {}

                # Cálculo segundo a nova especificação (IPCA + indevido + SELIC cumulativa)
                total_decimal, _breakdown = compute_total_refund(
                    provided_icms=provided_bills,
                    most_recent=most_recent_date,
                    ipca_rates=ipca_rates_map,
                    selic_rates=selic_rates_map,
                )

                resultado_final = float(total_decimal)

                # Transação atômica para registrar histórico e consumir crédito
                async with db.begin_nested():
                    balance_before_usage = await CalculationService._get_valid_credits_balance(db, user.id)
                    if balance_before_usage <= 0:
                        raise HTTPException(
                            status_code=status.HTTP_402_PAYMENT_REQUIRED,
                            detail="Insufficient valid credits"
                        )
                
                    # Registrar transacao de uso de credito
                    usage_transaction = CreditTransaction(
                        user_id=user.id,
                        transaction_type="usage",
                        amount=-1,
                        balance_before=balance_before_usage,
                        balance_after=balance_before_usage - 1,
                        description="Calculo detalhado de ICMS",
                        reference_id=None
                    )
                    db.add(usage_transaction)
                
                    # Salvar historico
                    ip_address, user_agent = AuditService.extract_client_info(request) if request else (None, None)
                
                    history_record = QueryHistory(
                        user_id=user.id,
                        icms_value=sum(provided_bills.values()) / len(provided_bills),
                        months=120,
                        calculated_value=Decimal(str(resultado_final)),
                        calculation_time_ms=int((time.time() - start_time) * 1000),
                        ip_address=ip_address,
                        user_agent=user_agent
                    )
                    db.add(history_record)
                    await db.flush()
                
                    usage_transaction.reference_id = f"calc_{history_record.id}"
                
                    user.credits = max(0, balance_before_usage - 1)
                
                    await db.commit()
                # Calcular saldo atualizado de créditos válidos
                valid_credits_remaining = await CalculationService._get_valid_credits_balance(db, user.id)

                total_time_ms = int((time.time() - start_time) * 1000)
                logger.info("Cálculo detalhado concluído", 
                           calculation_id=history_record.id, 
                           total_time_ms=total_time_ms)

                return CalculationResponse(
                    valor_calculado=resultado_final,
                    creditos_restantes=valid_credits_remaining,
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
            
            # Total de créditos usados
            credit_usage_stmt = select(func.sum(func.abs(CreditTransaction.amount))).where(
                CreditTransaction.transaction_type == "usage"
            )
            credit_usage_result = await db.execute(credit_usage_stmt)
            total_credits_used = credit_usage_result.scalar()
            
            return DashboardStats(
                total_calculations=total_calculations or 0,
                total_users=total_users or 0,
                total_credits_used=total_credits_used or 0,
                calculations_today=calculations_today or 0,
                avg_calculation_time_ms=float(avg_calculation_time) if avg_calculation_time else None
            )
            
        except Exception as e:
            logger.error("Error fetching dashboard stats", error=str(e))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Error retrieving dashboard statistics"
            )


# REMOVIDO: Classe PlanService foi completamente removida conforme solicitado
