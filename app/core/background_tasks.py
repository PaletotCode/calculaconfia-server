from celery import Celery
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from typing import Dict, Any, List
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

# --- TAREFA GENÉRICA DE ENVIO DE EMAIL COM SENDGRID ---
@celery_app.task(bind=True, max_retries=3)
def send_email_task(self, to_email: str, subject: str, html_content: str):
    """
    Tarefa Celery para enviar e-mails de forma assíncrona usando SendGrid.
    """
    if not settings.SENDGRID_API_KEY:
        logger.warning("SENDGRID_API_KEY não configurada. Simulando envio de email.")
        print(f"📧 EMAIL SIMULADO para {to_email} | Assunto: {subject}")
        return {"status": "simulated", "to": to_email}

    try:
        message = Mail(
            from_email=settings.MAIL_FROM,
            to_emails=to_email,
            subject=subject,
            html_content=html_content
        )
        sg = SendGridAPIClient(settings.SENDGRID_API_KEY)
        response = sg.send(message)
        logger.info(f"Email enviado para {to_email}, status: {response.status_code}")
        return {"status": "sent", "to": to_email}
    except Exception as exc:
        logger.error(f"Falha ao enviar email para {to_email}", error=str(exc))
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=60)
        return {"status": "failed", "error": str(exc)}


# --- FUNÇÕES QUE CHAMAM A TAREFA ---

def send_verification_email(to_email: str, code: str):
    """Prepara e envia o e-mail de verificação."""
    subject = f"Seu Código de Verificação: {code}"
    html_content = f"<h3>Olá!</h3><p>Seu código para ativar sua conta é: <strong>{code}</strong></p><p>Este código expira em 5 minutos.</p>"
    send_email_task.delay(to_email, subject, html_content)

def send_password_reset_email(to_email: str, code: str):
    """Prepara e envia o e-mail de redefinição de senha."""
    subject = f"Redefinição de Senha: {code}"
    html_content = f"<h3>Olá!</h3><p>Seu código para redefinir sua senha é: <strong>{code}</strong></p><p>Este código expira em 5 minutos.</p>"
    send_email_task.delay(to_email, subject, html_content)

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