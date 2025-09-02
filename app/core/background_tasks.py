from celery import Celery
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType
from typing import Dict, Any, List
import json
from datetime import datetime

from .config import settings
from .logging_config import get_logger

# Configuração do Celery
celery_app = Celery(
    "torres_tasks",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.core.background_tasks"]
)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,  # 5 minutos
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
)

logger = get_logger(__name__)

# Configuração de email
email_conf = ConnectionConfig(
    MAIL_USERNAME=settings.MAIL_USERNAME,
    MAIL_PASSWORD=settings.MAIL_PASSWORD,
    MAIL_FROM=settings.MAIL_FROM,
    MAIL_PORT=settings.MAIL_PORT,
    MAIL_SERVER=settings.MAIL_SERVER,
    MAIL_FROM_NAME=settings.MAIL_FROM_NAME,
    MAIL_STARTTLS=settings.MAIL_STARTTLS,
    MAIL_SSL_TLS=settings.MAIL_SSL_TLS,
    USE_CREDENTIALS=settings.USE_CREDENTIALS,
    TEMPLATE_FOLDER='app/templates'
)

fastmail = FastMail(email_conf)


@celery_app.task(bind=True, max_retries=3)
def send_calculation_email(self, user_email: str, calculation_data: Dict[str, Any]):
    """
    Envia email com resultado do cálculo para o usuário
    """
    try:
        logger.info("Sending calculation email", 
                   user_email=user_email, 
                   task_id=self.request.id)
        
        html_content = f"""
        <html>
        <body>
            <h2>Resultado do Cálculo - Torres Project</h2>
            <p>Olá!</p>
            <p>Seu cálculo foi processado com sucesso:</p>
            <ul>
                <li><strong>Valor médio do ICMS informado:</strong> R$ {calculation_data['average_icms']:,.2f}</li>
                <li><strong>Número de faturas informadas:</strong> {calculation_data['bill_count']}</li>
                <li><strong>Período calculado:</strong> 120 meses</li>
                <li><strong>Valor calculado:</strong> R$ {calculation_data['valor_calculado']:,.2f}</li>
                <li><strong>Data:</strong> {datetime.now().strftime('%d/%m/%Y %H:%M')}</li>
            </ul>
            <p>Créditos restantes: {calculation_data['creditos_restantes']}</p>
            <p>Obrigado por usar o Torres Project!</p>
        </body>
        </html>
        """
        
        message = MessageSchema(
            subject="Resultado do Cálculo - Torres Project",
            recipients=[user_email],
            body=html_content,
            subtype=MessageType.html
        )
        
        # Como estamos em um worker Celery, precisamos usar um loop assíncrono
        import asyncio
        asyncio.run(fastmail.send_message(message))
        
        logger.info("Calculation email sent successfully", 
                   user_email=user_email, 
                   task_id=self.request.id)
        
        return {"status": "sent", "user_email": user_email}
        
    except Exception as exc:
        logger.error("Failed to send calculation email", 
                    user_email=user_email, 
                    error=str(exc), 
                    task_id=self.request.id)
        
        if self.request.retries < self.max_retries:
            logger.info("Retrying email send", 
                       user_email=user_email, 
                       retry_count=self.request.retries + 1)
            raise self.retry(countdown=60 * (2 ** self.request.retries))
        
        return {"status": "failed", "error": str(exc)}


@celery_app.task(bind=True)
def send_welcome_email(self, user_email: str, user_name: str = None):
    """
    Envia email de boas-vindas para novos usuários
    """
    try:
        logger.info("Sending welcome email", user_email=user_email)
        
        html_content = f"""
        <html>
        <body>
            <h2>Bem-vindo ao Torres Project!</h2>
            <p>Olá{f' {user_name}' if user_name else ''}!</p>
            <p>Sua conta foi criada com sucesso. Você recebeu 3 créditos gratuitos para começar.</p>
            <p>Com o Torres Project você pode:</p>
            <ul>
                <li>Calcular valores de ICMS de forma rápida e precisa</li>
                <li>Acessar histórico completo de seus cálculos</li>
                <li>Integrar com sua aplicação via API</li>
            </ul>
            <p>Comece agora mesmo fazendo seu primeiro cálculo!</p>
            <p>Equipe Torres Project</p>
        </body>
        </html>
        """
        
        message = MessageSchema(
            subject="Bem-vindo ao Torres Project!",
            recipients=[user_email],
            body=html_content,
            subtype=MessageType.html
        )
        
        import asyncio
        asyncio.run(fastmail.send_message(message))
        
        logger.info("Welcome email sent successfully", user_email=user_email)
        return {"status": "sent", "user_email": user_email}
        
    except Exception as exc:
        logger.error("Failed to send welcome email", 
                    user_email=user_email, 
                    error=str(exc))
        return {"status": "failed", "error": str(exc)}


@celery_app.task
def process_bulk_calculations(calculation_requests: List[Dict[str, Any]], user_id: int):
    """
    Processa múltiplos cálculos em lote (para funcionalidade futura)
    """
    try:
        logger.info("Processing bulk calculations", 
                   user_id=user_id, 
                   count=len(calculation_requests))
        
        results = []
        for calc in calculation_requests:
            # Simular processamento de cálculo
            result = calc['valor_icms'] * (0.0065 + 0.03) * calc['numero_meses']
            results.append({
                'valor_icms': calc['valor_icms'],
                'numero_meses': calc['numero_meses'],
                'valor_calculado': result
            })
        
        logger.info("Bulk calculations processed successfully", 
                   user_id=user_id, 
                   processed_count=len(results))
        
        return {
            "status": "completed",
            "user_id": user_id,
            "processed": len(results),
            "results": results
        }
        
    except Exception as exc:
        logger.error("Failed to process bulk calculations", 
                    user_id=user_id, 
                    error=str(exc))
        return {"status": "failed", "error": str(exc)}


@celery_app.task
def cleanup_old_audit_logs():
    """
    Limpa logs de auditoria antigos (executar via cron)
    """
    try:
        logger.info("Starting audit logs cleanup")
        
        # Esta tarefa seria implementada para limpar logs antigos
        # Por exemplo, logs mais antigos que 1 ano
        from datetime import datetime, timedelta
        cutoff_date = datetime.now() - timedelta(days=365)
        
        # Aqui iria a lógica de limpeza do banco de dados
        # Por enquanto apenas logamos a ação
        
        logger.info("Audit logs cleanup completed", cutoff_date=cutoff_date.isoformat())
        return {"status": "completed", "cutoff_date": cutoff_date.isoformat()}
        
    except Exception as exc:
        logger.error("Failed to cleanup audit logs", error=str(exc))
        return {"status": "failed", "error": str(exc)}


@celery_app.task
def generate_monthly_reports():
    """
    Gera relatórios mensais de uso (executar via cron)
    """
    try:
        logger.info("Starting monthly reports generation")
        
        # Aqui iria a lógica de geração de relatórios
        # Por exemplo: total de cálculos, usuários ativos, receita, etc.
        
        current_month = datetime.now().strftime("%Y-%m")
        
        logger.info("Monthly reports generated", month=current_month)
        return {"status": "completed", "month": current_month}
        
    except Exception as exc:
        logger.error("Failed to generate monthly reports", error=str(exc))
        return {"status": "failed", "error": str(exc)}


# Configuração de tarefas periódicas (Celery Beat)
celery_app.conf.beat_schedule = {
    'cleanup-audit-logs': {
        'task': 'app.core.background_tasks.cleanup_old_audit_logs',
        'schedule': 86400.0,  # Diário (24 horas)
    },
    'monthly-reports': {
        'task': 'app.core.background_tasks.generate_monthly_reports',
        'schedule': 2592000.0,  # Mensal (30 dias)
    },
}