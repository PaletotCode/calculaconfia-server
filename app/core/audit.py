import json
import uuid
from datetime import datetime
from typing import Optional, Dict, Any
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession
from contextlib import asynccontextmanager

from .logging_config import get_logger, LogContext
from ..models_schemas.models import AuditLog, AuditAction, User

logger = get_logger(__name__)


class AuditService:
    """
    Serviço de auditoria completo para rastrear todas as ações críticas
    """
    
    @staticmethod
    def extract_client_info(request: Request) -> tuple[str, str]:
        """Extrai informações do cliente da requisição"""
        # IP real considerando proxies/load balancers
        ip = (
            request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or
            request.headers.get("X-Real-IP", "").strip() or
            request.client.host if request.client else "unknown"
        )
        
        user_agent = request.headers.get("User-Agent", "unknown")
        
        return ip, user_agent
    
    @staticmethod
    async def log_action(
        db: AsyncSession,
        action: AuditAction,
        user_id: Optional[int] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[int] = None,
        old_values: Optional[Dict[str, Any]] = None,
        new_values: Optional[Dict[str, Any]] = None,
        request: Optional[Request] = None,
        success: bool = True,
        error_message: Optional[str] = None,
        request_id: Optional[str] = None
    ) -> AuditLog:
        """
        Registra uma ação de auditoria no banco de dados
        """
        
        # Gerar ID único para a requisição se não fornecido
        if not request_id:
            request_id = str(uuid.uuid4())
        
        # Extrair informações do cliente se request fornecida
        ip_address = None
        user_agent = None
        if request:
            ip_address, user_agent = AuditService.extract_client_info(request)
        
        try:
            audit_log = AuditLog(
                user_id=user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                old_values=json.dumps(old_values, default=str) if old_values else None,
                new_values=json.dumps(new_values, default=str) if new_values else None,
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request_id,
                success=success,
                error_message=error_message
            )
            
            db.add(audit_log)
            await db.commit()
            await db.refresh(audit_log)
            
            # Log estruturado para monitoramento
            with LogContext(
                audit_id=audit_log.id,
                user_id=user_id,
                action=action.value,
                resource_type=resource_type,
                resource_id=resource_id,
                ip_address=ip_address,
                request_id=request_id,
                success=success
            ):
                if success:
                    logger.info("Audit action logged", 
                               action=action.value,
                               user_id=user_id,
                               resource_type=resource_type)
                else:
                    logger.warning("Failed action audited", 
                                 action=action.value,
                                 error=error_message,
                                 user_id=user_id)
            
            return audit_log
            
        except Exception as e:
            logger.error("Failed to create audit log", 
                        action=action.value,
                        error=str(e),
                        user_id=user_id)
            raise
    
    @staticmethod
    @asynccontextmanager
    async def audit_context(
        db: AsyncSession,
        action: AuditAction,
        user_id: Optional[int] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[int] = None,
        request: Optional[Request] = None
    ):
        """
        Context manager para auditoria automática de operações
        """
        request_id = str(uuid.uuid4())
        start_time = datetime.now()
        
        try:
            with LogContext(request_id=request_id, action=action.value):
                logger.info("Starting audited operation", 
                           action=action.value,
                           user_id=user_id,
                           resource_type=resource_type)
                
                yield request_id
                
                # Sucesso - registrar auditoria
                await AuditService.log_action(
                    db=db,
                    action=action,
                    user_id=user_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    request=request,
                    success=True,
                    request_id=request_id
                )
                
                duration = (datetime.now() - start_time).total_seconds()
                logger.info("Audited operation completed", 
                           action=action.value,
                           duration_seconds=duration)
                
        except Exception as e:
            # Falha - registrar auditoria com erro
            await AuditService.log_action(
                db=db,
                action=action,
                user_id=user_id,
                resource_type=resource_type,
                resource_id=resource_id,
                request=request,
                success=False,
                error_message=str(e),
                request_id=request_id
            )
            
            duration = (datetime.now() - start_time).total_seconds()
            logger.error("Audited operation failed", 
                        action=action.value,
                        error=str(e),
                        duration_seconds=duration)
            raise


class SecurityMonitor:
    """
    Monitor de segurança para detectar atividades suspeitas
    """
    
    @staticmethod
    async def check_suspicious_activity(
        db: AsyncSession,
        user_id: int,
        action: AuditAction,
        ip_address: str
    ) -> Dict[str, Any]:
        """
        Verifica atividades suspeitas do usuário
        """
        from sqlalchemy import select, func
        from datetime import timedelta
        
        now = datetime.now()
        hour_ago = now - timedelta(hours=1)
        day_ago = now - timedelta(days=1)
        
        # Contar ações na última hora
        stmt_hour = select(func.count(AuditLog.id)).where(
            AuditLog.user_id == user_id,
            AuditLog.action == action,
            AuditLog.created_at >= hour_ago
        )
        result_hour = await db.execute(stmt_hour)
        actions_last_hour = result_hour.scalar()
        
        # Contar IPs diferentes no último dia
        stmt_ips = select(func.count(func.distinct(AuditLog.ip_address))).where(
            AuditLog.user_id == user_id,
            AuditLog.created_at >= day_ago,
            AuditLog.ip_address.isnot(None)
        )
        result_ips = await db.execute(stmt_ips)
        different_ips = result_ips.scalar()
        
        # Definir thresholds de segurança
        suspicious_flags = []
        
        if actions_last_hour > 100:  # Mais de 100 ações por hora
            suspicious_flags.append("high_frequency_actions")
        
        if different_ips > 5:  # Mais de 5 IPs diferentes em 1 dia
            suspicious_flags.append("multiple_ip_addresses")
        
        if action == AuditAction.LOGIN:
            # Contar tentativas de login falhadas na última hora
            stmt_failed = select(func.count(AuditLog.id)).where(
                AuditLog.user_id == user_id,
                AuditLog.action == AuditAction.LOGIN,
                AuditLog.success == False,
                AuditLog.created_at >= hour_ago
            )
            result_failed = await db.execute(stmt_failed)
            failed_logins = result_failed.scalar()
            
            if failed_logins > 5:  # Mais de 5 tentativas falhadas por hora
                suspicious_flags.append("multiple_failed_logins")
        
        risk_level = "low"
        if len(suspicious_flags) >= 2:
            risk_level = "high"
        elif len(suspicious_flags) == 1:
            risk_level = "medium"
        
        return {
            "user_id": user_id,
            "risk_level": risk_level,
            "flags": suspicious_flags,
            "actions_last_hour": actions_last_hour,
            "different_ips_today": different_ips,
            "timestamp": now.isoformat(),
        }