from celery import Celery
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from typing import Dict, Any, List
from datetime import datetime
import os

from twilio.rest import Client

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

# TAREFA DE ENVIO DE EMAIL COM SENDGRID
@celery_app.task(bind=True, max_retries=3)
def send_email_task(self, to_email: str, subject: str, html_content: str):
    """
    Tarefa Celery para enviar e-mails de forma assíncrona usando SendGrid.
    """
    # 🔥 Debug: Verificar se a chave está disponível no worker
    sendgrid_key = settings.SENDGRID_API_KEY or os.getenv('SENDGRID_API_KEY')
    
    logger.info(f"SendGrid Key Status: {'Available' if sendgrid_key else 'Missing'}")
    logger.info(f"Attempting to send email to: {to_email}")
    logger.info(f"Subject: {subject}")
    
    if not sendgrid_key:
        logger.warning("SENDGRID_API_KEY não configurada. Simulando envio de email.")
        logger.warning("🔍 Debug - Environment variables available:")
        for key in os.environ:
            if 'SENDGRID' in key or 'MAIL' in key:
                logger.warning(f"  {key}: {'SET' if os.environ[key] else 'NOT SET'}")
        
        print(f"📧 EMAIL SIMULADO para {to_email} | Assunto: {subject}")
        return {"status": "simulated", "to": to_email, "reason": "SENDGRID_API_KEY not configured"}

    try:
        # 🔥 Criar mensagem com configurações corretas
        message = Mail(
            from_email=settings.MAIL_FROM,
            to_emails=to_email,
            subject=subject,
            html_content=html_content
        )
        
        # Adicionar nome do remetente (fallback para CalculaConfia)
        message.from_email.name = settings.MAIL_FROM_NAME or "CalculaConfia"
        
        # Inicializar cliente SendGrid
        sg = SendGridAPIClient(sendgrid_key)
        
        # Enviar email
        logger.info("Sending email via SendGrid...")
        response = sg.send(message)
        
        logger.info(f"Email sent successfully")
        logger.info(f"SendGrid Response - Status: {response.status_code}")
        logger.info(f"SendGrid Response - Body: {response.body}")
        logger.info(f"SendGrid Response - Headers: {response.headers}")
        
        return {
            "status": "sent", 
            "to": to_email,
            "sendgrid_status": response.status_code,
            "message_id": response.headers.get('X-Message-Id', 'unknown')
        }
        
    except Exception as exc:
        error_msg = str(exc)
        logger.error(f"Failed to send email to {to_email}: {error_msg}")
        
        # Log detalhado do erro
        if hasattr(exc, 'status_code'):
            logger.error(f"SendGrid Error Status: {exc.status_code}")
        if hasattr(exc, 'body'):
            logger.error(f"SendGrid Error Body: {exc.body}")
            
        # Retry logic
        if self.request.retries < self.max_retries:
            logger.info(f"Retrying email send (attempt {self.request.retries + 1}/{self.max_retries})")
            raise self.retry(countdown=60)
            
        return {
            "status": "failed", 
            "error": error_msg,
            "to": to_email,
            "retries_exhausted": True
        }


# 🔥 FUNÇÕES MELHORADAS QUE CHAMAM A TAREFA
def send_verification_email(to_email: str, code: str):
    """Prepara e envia o e-mail de verificação (branding CalculaConfia)."""
    # Coloca o código diretamente no título para facilitar no push/lockscreen
    subject = f"Código de verificação: {code} · CalculaConfia"

    html_content = f"""
    <!DOCTYPE html>
    <html lang=\"pt-BR\">
    <head>
        <meta charset=\"UTF-8\">
        <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
        <title>Verificação de conta - CalculaConfia</title>
    </head>
    <body style=\"margin:0; padding:0; background:#f1f5f9; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, 'Helvetica Neue', sans-serif; color:#0f172a;\">
      <table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" width=\"100%\" style=\"background:#f1f5f9;\">
        <tr>
          <td>
            <table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" width=\"600\" align=\"center\" style=\"width:100%; max-width:600px; margin:40px auto; background:#ffffff; border-radius:12px; overflow:hidden; box-shadow:0 2px 8px rgba(30,41,59,0.05);\">
              <tr>
                <td style=\"padding:20px 24px; background:#1e293b; border-bottom:4px solid #16a34a;\">
                  <div style=\"font-size:20px; line-height:24px; color:#f8fafc; font-weight:600;\">CalculaConfia</div>
                </td>
              </tr>
              <tr>
                <td style=\"padding:28px 24px 8px 24px;\">
                  <div style=\"font-size:18px; font-weight:600; color:#0f172a;\">Código de verificação</div>
                  <p style=\"margin:12px 0 0 0; font-size:14px; color:#0f172a;\">Use o código abaixo para confirmar seu e‑mail e ativar sua conta.</p>
                </td>
              </tr>
              <tr>
                <td style=\"padding:8px 24px 24px 24px;\">
                  <div style=\"border:1px solid #e2e8f0; border-radius:10px; padding:20px; text-align:center;\">
                    <div style=\"font-size:14px; color:#1e293b; margin-bottom:8px;\">Seu código</div>
                    <div style=\"font-size:32px; letter-spacing:6px; font-weight:700; color:#16a34a;\">{code}</div>
                    <div style=\"margin-top:10px; font-size:12px; color:#475569;\">O código expira em 10 minutos</div>
                  </div>
                </td>
              </tr>
              <tr>
                <td style=\"padding:0 24px 28px 24px;\">
                  <p style=\"margin:0; font-size:12px; color:#475569;\">Se você não solicitou este e‑mail, ignore esta mensagem.</p>
                </td>
              </tr>
              <tr>
                <td style=\"padding:16px 24px; background:#f8fafc; text-align:center;\">
                  <div style=\"font-size:12px; color:#475569;\">© {datetime.utcnow().year} CalculaConfia</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
    </body>
    </html>
    """

    # Executar tarefa assíncrona
    logger.info(f"Queueing verification email to: {to_email}")
    result = send_email_task.delay(to_email, subject, html_content)
    logger.info(f"Email task queued with ID: {result.id}")
    return result

def send_password_reset_email(to_email: str, code: str):
    """Prepara e envia o e-mail de redefinição de senha (branding CalculaConfia)."""
    # Inclui o código no título para visualização imediata
    subject = f"Código para redefinir senha: {code} · CalculaConfia"
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang=\"pt-BR\">
    <head>
        <meta charset=\"UTF-8\">
        <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
        <title>Redefinição de senha - CalculaConfia</title>
    </head>
    <body style=\"margin:0; padding:0; background:#f1f5f9; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, 'Helvetica Neue', sans-serif; color:#0f172a;\">
      <table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" width=\"100%\" style=\"background:#f1f5f9;\">
        <tr>
          <td>
            <table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" width=\"600\" align=\"center\" style=\"width:100%; max-width:600px; margin:40px auto; background:#ffffff; border-radius:12px; overflow:hidden; box-shadow:0 2px 8px rgba(30,41,59,0.05);\">
              <tr>
                <td style=\"padding:20px 24px; background:#1e293b; border-bottom:4px solid #16a34a;\">
                  <div style=\"font-size:20px; line-height:24px; color:#f8fafc; font-weight:600;\">CalculaConfia</div>
                </td>
              </tr>
              <tr>
                <td style=\"padding:28px 24px 8px 24px;\">
                  <div style=\"font-size:18px; font-weight:600; color:#0f172a;\">Redefinição de senha</div>
                  <p style=\"margin:12px 0 0 0; font-size:14px; color:#0f172a;\">Use o código abaixo para criar uma nova senha.</p>
                </td>
              </tr>
              <tr>
                <td style=\"padding:8px 24px 24px 24px;\">
                  <div style=\"border:1px solid #e2e8f0; border-radius:10px; padding:20px; text-align:center;\">
                    <div style=\"font-size:14px; color:#1e293b; margin-bottom:8px;\">Código de confirmação</div>
                    <div style=\"font-size:32px; letter-spacing:6px; font-weight:700; color:#16a34a;\">{code}</div>
                    <div style=\"margin-top:10px; font-size:12px; color:#475569;\">O código expira em 5 minutos</div>
                  </div>
                </td>
              </tr>
              <tr>
                <td style=\"padding:0 24px 28px 24px;\">
                  <p style=\"margin:0; font-size:12px; color:#475569;\">Se você não solicitou esta operação, ignore esta mensagem.</p>
                </td>
              </tr>
              <tr>
                <td style=\"padding:16px 24px; background:#f8fafc; text-align:center;\">
                  <div style=\"font-size:12px; color:#475569;\">© {datetime.utcnow().year} CalculaConfia</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
    </body>
    </html>
    """
    
    # Executar tarefa assíncrona
    logger.info(f"Queueing password reset email to: {to_email}")
    result = send_email_task.delay(to_email, subject, html_content)
    logger.info(f"Email task queued with ID: {result.id}")
    return result

# Outras tarefas permanecem iguais...
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

#TAREFA DE ENVIO DE SMS
@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def send_sms_task(self, to_phone_number: str, body: str):
    """
    Tarefa Celery para enviar SMS de forma assíncrona usando Twilio.
    """
    if not all([settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN, settings.TWILIO_PHONE_NUMBER]):
        logger.warning("Twilio não configurado. Simulando envio de SMS.")
        print(f"📱 SMS SIMULADO para {to_phone_number} | Body: {body}")
        return {"status": "simulated", "to": to_phone_number}

    try:
        logger.info(f"📱 Attempting to send SMS to: {to_phone_number}")
        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

        message = client.messages.create(
            body=body,
            from_=settings.TWILIO_PHONE_NUMBER,
            to=to_phone_number # O número deve estar no formato E.164, ex: +5511999998888
        )

        logger.info(f"✅ SMS sent successfully! SID: {message.sid}")
        return {"status": "sent", "to": to_phone_number, "sid": message.sid}

    except Exception as exc:
        logger.error(f"❌ Failed to send SMS to {to_phone_number}: {exc}", exc_info=True)
        # Tenta reenviar a tarefa em caso de falha de rede, etc.
        raise self.retry(exc=exc)

# 🔥 NOVA FUNÇÃO HELPER CORRIGIDA
def send_verification_sms(to_phone_number: str, code: str):
    """
    Prepara a mensagem e enfileira a tarefa de envio de SMS de verificação.
    * Problema resolvido: Padroniza o formato do número para o padrão E.164 (+55) que o Twilio exige.
    """
    # Garante que o número esteja no formato internacional E.164
    if not to_phone_number.startswith('+'):
        to_phone_number = f"+55{to_phone_number}" # Adiciona o código do Brasil

    body = f"Seu código de verificação para o Torres Project é: {code}"
    logger.info(f"📱 Queueing verification SMS to: {to_phone_number}")
    
    # 🔥 CORREÇÃO: Retornar o resultado da tarefa
    result = send_sms_task.delay(to_phone_number, body)
    logger.info(f"📱 SMS task queued with ID: {result.id}")
    return result
