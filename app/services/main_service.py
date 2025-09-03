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
from ..core.background_tasks import send_verification_email, send_password_reset_email, send_verification_sms

from fastapi import HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, desc
from sqlalchemy.exc import IntegrityError

from ..core.security import get_password_hash, verify_password
from ..core.logging_config import get_logger, LogContext
from ..core.audit import AuditService, SecurityMonitor
from ..models_schemas.models import (
    User, QueryHistory, AuditAction, 
    CreditTransaction, AuditLog
)
from ..models_schemas.schemas import (
    UserCreate, CalculationRequest, CalculationResponse,
    DashboardStats, SendVerificationCodeRequest, VerifyAccountRequest,
    RequestPasswordResetRequest, ResetPasswordRequest, VerificationCodeResponse
)

logger = get_logger(__name__)


class UserService:
    """Serviço para gerenciamento de usuários com autenticação por telefone"""
    
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
        Registra um novo usuário com verificação por telefone e sistema de referência
        """
        async with AuditService.audit_context(
            db=db,
            action=AuditAction.REGISTER,
            request=request
        ) as request_id:
            
            try:
                with LogContext(phone_number=user_data.phone_number, request_id=request_id):
                    logger.info("Starting user registration")
                    
                    # Verificar se phone_number já existe
                    stmt = select(User).where(User.phone_number == user_data.phone_number)
                    existing_user = await db.execute(stmt)
                    if existing_user.scalar_one_or_none():
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Phone number already registered"
                        )
                    
                    # Verificar email se fornecido
                    referred_by = None
                    if user_data.email:
                        stmt = select(User).where(User.email == user_data.email)
                        existing_email = await db.execute(stmt)
                        if existing_email.scalar_one_or_none():
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Email already registered"
                            )
                    
                    # Verificar código de referência se fornecido
                    if user_data.applied_referral_code:
                        stmt = select(User).where(User.referral_code == user_data.applied_referral_code)
                        referrer_result = await db.execute(stmt)
                        referred_by = referrer_result.scalar_one_or_none()
                        if not referred_by:
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Invalid referral code"
                            )
                    
                    # Criar usuário (inativo até verificação)
                    hashed_password = get_password_hash(user_data.password)
                    db_user = User(
                        phone_number=user_data.phone_number,
                        email=user_data.email,
                        hashed_password=hashed_password,
                        first_name=user_data.first_name,
                        last_name=user_data.last_name,
                        referred_by_id=referred_by.id if referred_by else None,
                        is_verified=False,  # Não verificado
                        is_active=False,    # Não ativo
                        credits=0           # Zero créditos iniciais
                    )
                    
                    db.add(db_user)
                    await db.flush()  # Para obter o ID
                    
                    # Gerar código de referência único
                    referral_code = UserService._generate_referral_code(
                        user_data.first_name, 
                        db_user.id
                    )
                    
                    # Garantir que o código seja único
                    max_attempts = 10
                    for _ in range(max_attempts):
                        stmt = select(User).where(User.referral_code == referral_code)
                        existing_code = await db.execute(stmt)
                        if not existing_code.scalar_one_or_none():
                            break
                        referral_code = UserService._generate_referral_code(
                            user_data.first_name, 
                            random.randint(1000, 9999)
                        )
                    
                    db_user.referral_code = referral_code
                    
                    # Gerar código de verificação por E-MAIL
                    if not db_user.email:
                        raise HTTPException(status_code=400, detail="O e-mail é obrigatório para o cadastro.")

                    verification_code = UserService._generate_verification_code()
                    expires_at = datetime.utcnow() + timedelta(minutes=5)

                    verification_record = VerificationCode(
                        identifier=db_user.email, # <-- MUDANÇA CRÍTICA
                        code=verification_code,
                        expires_at=expires_at,
                        type=VerificationType.EMAIL # <-- MUDANÇA CRÍTICA
                    )

                    db.add(verification_record)
                    await db.commit()
                    await db.refresh(db_user)

                    # Disparar o e-mail de verificação real
                    send_verification_email(db_user.email, verification_code) # <-- MUDANÇA CRÍTICA

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
    
    @staticmethod
    async def send_verification_code(
        db: AsyncSession,
        request_data: SendVerificationCodeRequest,
        request: Optional[Request] = None
    ) -> VerificationCodeResponse:
        """
        Gera e envia código de verificação por SMS ou email
        """
        try:
            identifier = request_data.identifier.strip()
            
            # Determinar tipo (email ou telefone)
            import re
            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            verification_type = VerificationType.EMAIL if re.match(email_pattern, identifier) else VerificationType.SMS
            
            # Invalidar códigos anteriores não utilizados
            stmt = select(VerificationCode).where(
                and_(
                    VerificationCode.identifier == identifier,
                    VerificationCode.used == False,
                    VerificationCode.expires_at > datetime.utcnow()
                )
            )
            existing_codes = await db.execute(stmt)
            for code in existing_codes.scalars():
                code.used = True
            
            # Gerar novo código
            verification_code = UserService._generate_verification_code()
            expires_at = datetime.utcnow() + timedelta(minutes=5)
            
            verification_record = VerificationCode(
                identifier=identifier,
                code=verification_code,
                expires_at=expires_at,
                type=verification_type
            )
            
            db.add(verification_record)
            await db.commit()
            
            # Substitua o bloco if/else de simulação por isto:
            if verification_type == VerificationType.EMAIL:
                send_verification_email(identifier, verification_code)
            else: # SMS
                # Chama a nova função que enfileira a tarefa no Celery.
                send_verification_sms(identifier, verification_code)
            
            await AuditService.log_action(
                db=db,
                action=AuditAction.VERIFICATION,
                request=request,
                success=True,
                resource_type="verification_code"
            )
            
            return VerificationCodeResponse(
                message="Verification code sent successfully",
                expires_in_minutes=5
            )
            
        except Exception as e:
            logger.error("Failed to send verification code", error=str(e))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Error sending verification code"
            )
    
    @staticmethod
    async def verify_account(
        db: AsyncSession,
        request_data: VerifyAccountRequest,
        request: Optional[Request] = None
    ) -> UserResponse:
        """
        Verifica conta do usuário e ativa
        """
        try:
            identifier = request_data.identifier.strip()
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
            
            # Buscar usuário
            if verification.type == VerificationType.SMS:
                stmt = select(User).where(User.phone_number == identifier)
            else:
                stmt = select(User).where(User.email == identifier)
            
            user_result = await db.execute(stmt)
            user = user_result.scalar_one_or_none()
            
            if not user:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found"
                )
            
            # Ativar usuário e dar créditos iniciais se ainda não ativo
            old_active_status = user.is_active
            user.is_verified = True
            user.is_active = True
            
            if not old_active_status:
                # Dar 3 créditos iniciais com validade de 30 dias
                user.credits = 3
                expires_at = datetime.utcnow() + timedelta(days=40)
                
                credit_transaction = CreditTransaction(
                    user_id=user.id,
                    transaction_type="bonus",
                    amount=3,
                    balance_before=0,
                    balance_after=3,
                    description="Welcome bonus credits",
                    reference_id=f"welcome_{user.id}",
                    expires_at=expires_at
                )
                db.add(credit_transaction)
            
            # Marcar código como usado
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
                phone_number=user.phone_number,
                first_name=user.first_name,
                last_name=user.last_name,
                referral_code=user.referral_code,
                credits=user.credits,
                is_verified=user.is_verified,
                is_active=user.is_active,
                created_at=user.created_at
            )
            
        except HTTPException:
            raise
        except Exception as e:
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
        identifier: str,  # Phone number ou email
        password: str,
        request: Optional[Request] = None
    ) -> User:
        """
        Autentica usuário com telefone ou email
        """
        start_time = time.time()
        
        try:
            with LogContext(identifier=identifier):
                logger.info("Starting user authentication")
                
                # Buscar usuário por telefone ou email
                stmt = select(User).where(
                    or_(
                        User.phone_number == identifier,
                        User.email == identifier
                    )
                )
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
                base_values = {date: icms * PIS_COFINS_FACTOR for date, icms in provided_bills.items()}
                average_base_value = sum(base_values.values()) / len(base_values)

                # Preenchimento dos 120 meses
                all_months_base = {}
                for i in range(120):
                    current_month_date = most_recent_date - relativedelta(months=i)
                    all_months_base[current_month_date] = base_values.get(current_month_date, average_base_value)

                # Buscar taxas SELIC
                start_period_date = most_recent_date - relativedelta(months=119)
                stmt = select(SelicRate).where(
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
                selic_results = await db.execute(stmt)
                selic_rates_map = {datetime(r.year, r.month, 1).date(): r.rate for r in selic_results.scalars()}

                if not selic_rates_map:
                    raise HTTPException(status.HTTP_404_NOT_FOUND, "Dados da SELIC não encontrados para o período solicitado.")

                # Aplicação da SELIC
                total_restitution = Decimal("0.0")
                for date, base_value in all_months_base.items():
                    selic_rate = selic_rates_map.get(date, Decimal("0.0"))
                    corrected_value = base_value * (Decimal("1.0") + selic_rate)
                    total_restitution += corrected_value

                resultado_final = float(total_restitution)

                # Transação atômica para registrar histórico e consumir crédito
                async with db.begin_nested():
                    # Atualizar crédito do usuário (campo legacy)
                    await db.refresh(user)
                    old_credits = user.credits
                    user.credits = max(0, user.credits - 1)

                    # Registrar transação de uso de crédito
                    credit_transaction = CreditTransaction(
                        user_id=user.id,
                        transaction_type="usage",
                        amount=-1,
                        balance_before=old_credits,
                        balance_after=user.credits,
                        description="Cálculo detalhado de ICMS",
                        reference_id=None  # Será preenchido após criar histórico
                    )
                    db.add(credit_transaction)
                    
                    # Salvar histórico
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
                    await db.flush()  # Para obter o ID
                    
                    credit_transaction.reference_id = f"calc_{history_record.id}"
                    
                    # Processar bônus de referência se aplicável
                    await CalculationService._process_referral_bonus(db, user)
                    
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
    
    @staticmethod
    async def _process_referral_bonus(db: AsyncSession, user: User):
        """
        Processa bônus de referência após pagamento bem-sucedido
        """
        try:
            if not user.referred_by_id:
                return  # Usuário não foi referenciado
            
            # Buscar o referenciador
            stmt = select(User).where(User.id == user.referred_by_id)
            result = await db.execute(stmt)
            referrer = result.scalar_one_or_none()
            
            if not referrer:
                logger.warning("Referrer not found", referred_by_id=user.referred_by_id)
                return
            
            # Verificar se referrer ainda pode ganhar créditos (máximo 3)
            if referrer.referral_credits_earned >= 3:
                logger.info("Referrer already earned maximum credits", referrer_id=referrer.id)
                return
            
            # Adicionar 1 crédito bônus ao referrer
            old_credits = referrer.credits
            referrer.credits += 1
            referrer.referral_credits_earned += 1
            
            # Registrar transação de bônus com validade de 60 dias
            expires_at = datetime.utcnow() + timedelta(days=60)
            
            bonus_transaction = CreditTransaction(
                user_id=referrer.id,
                transaction_type="referral_bonus",
                amount=1,
                balance_before=old_credits,
                balance_after=referrer.credits,
                description=f"Referral bonus from user {user.id}",
                reference_id=f"referral_{user.id}",
                expires_at=expires_at
            )
            
            db.add(bonus_transaction)
            
            logger.info("Referral bonus processed", 
                       referrer_id=referrer.id,
                       referee_id=user.id,
                       bonus_amount=1,
                       total_earned=referrer.referral_credits_earned)
            
        except Exception as e:
            logger.error("Error processing referral bonus", error=str(e))
            # Não falhar a transação principal por erro no bônus

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