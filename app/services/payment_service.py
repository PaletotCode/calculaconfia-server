import os
import mercadopago
from fastapi import Request, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.logging_config import get_logger
from ..models_schemas.models import User
from .credit_service import CreditService

logger = get_logger(__name__)

# --------------------------------------------------------------------------------
# --- Controle F: INÍCIO - payment_service.py
# --------------------------------------------------------------------------------

# Inicializa o SDK do Mercado Pago
# Garante que a chave de acesso não seja nula ou vazia
if not settings.MERCADO_PAGO_ACCESS_TOKEN:
    logger.critical("MERCADO_PAGO_ACCESS_TOKEN não está configurado!")
    sdk = None
else:
    sdk = mercadopago.SDK(settings.MERCADO_PAGO_ACCESS_TOKEN)

def create_payment_preference(user: User, item_details: dict):
    """
    Cria uma preferência de pagamento no Mercado Pago, aceitando APENAS PIX.
    """
    if not sdk:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sistema de pagamento indisponível.")

    public_base_url = os.getenv("PUBLIC_BASE_URL")
    frontend_url = os.getenv("FRONTEND_URL")

    if not public_base_url or not frontend_url:
        logger.error("PUBLIC_BASE_URL e FRONTEND_URL não estão configuradas no ambiente.")
        raise ValueError("Variáveis de ambiente de URL necessárias não foram configuradas.")

    preference_data = {
        "items": [
            {
                "id": item_details.get("id", "CREDITS-PACK"),
                "title": item_details.get("title", "Pacote de Créditos"),
                "description": "Créditos para a calculadora Torres Project",
                "category_id": "services",
                "quantity": 1,
                "unit_price": float(item_details.get("price", 10.00)),
            }
        ],
        "payer": {
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
        },
        # --- INÍCIO DA MODIFICAÇÃO PARA ACEITAR APENAS PIX ---
        "payment_methods": {
            "excluded_payment_methods": [
                {"id": "amex"},
                {"id": "elo"},
                {"id": "hipercard"},
                {"id": "master"},
                {"id": "visa"},
            ],
            "excluded_payment_types": [
                {"id": "credit_card"},
                {"id": "debit_card"},
                {"id": "ticket"}, # Boleto
                {"id": "atm"},
            ],
            "installments": 1
        },
        # --- FIM DA MODIFICAÇÃO ---
        "notification_url": f"{public_base_url.rstrip('/')}/api/v1/payments/webhook",
        "statement_descriptor": "TORRESPROJECT",
        "back_urls": {
            "success": f"{frontend_url.rstrip('/')}/payment/success",
            "failure": f"{frontend_url.rstrip('/')}/payment/failure",
            "pending": f"{frontend_url.rstrip('/')}/payment/pending",
        },
        "external_reference": str(user.id),
        "metadata": {
            "user_id": user.id,
            "credits_amount": item_details.get("credits", 3)
        }
    }

    try:
        logger.info("Criando preferência de pagamento (PIX-only) para o usuário.", user_id=user.id)
        preference_response = sdk.preference().create(preference_data)
        preference = preference_response.get("response")

        if not preference or "init_point" not in preference:
            logger.error("Resposta inválida do Mercado Pago ao criar preferência.", response=preference_response)
            raise Exception("Falha ao criar preferência de pagamento no Mercado Pago.")

        logger.info("Preferência de pagamento criada com sucesso.", preference_id=preference.get("id"))
        return preference

    except Exception as e:
        logger.error(f"Erro na SDK do Mercado Pago ao criar preferência: {e}", exc_info=True)
        raise

async def handle_webhook_notification(request: Request, db: AsyncSession):
    """
    Processa notificações recebidas pelo webhook do Mercado Pago com validação de segurança.
    Control F Amigável: handle_webhook_notification
    """
    if not sdk:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sistema de pagamento indisponível.")

    notification_data = await request.json()
    logger.info("Notificação de webhook recebida do Mercado Pago.", data=notification_data)

    if notification_data.get("type") != "payment":
        logger.info("Notificação não é do tipo 'payment'. Ignorando.", type=notification_data.get("type"))
        return

    payment_id = notification_data.get("data", {}).get("id")
    if not payment_id:
        logger.warning("Webhook não contém ID de pagamento.")
        return

    try:
        # 1. Buscar os detalhes do pagamento na API do Mercado Pago
        logger.info(f"Buscando detalhes do pagamento {payment_id} no Mercado Pago.")
        payment_info_response = sdk.payment().get(payment_id)
        payment_info = payment_info_response.get("response")

        if not payment_info:
            logger.error(f"Não foi possível obter informações do pagamento {payment_id}.")
            return

        # 2. Validar o status do pagamento
        status_pagamento = payment_info.get("status")
        if status_pagamento != "approved":
            logger.info(f"Pagamento {payment_id} não está aprovado. Status: {status_pagamento}. Ignorando.")
            return

        # 3. Extrair informações cruciais e metadados
        user_id = payment_info.get("external_reference")
        metadata = payment_info.get("metadata", {})
        credits_to_add = metadata.get("credits_amount")

        if not user_id or not credits_to_add:
            logger.error(f"Faltando 'external_reference' (user_id) ou 'credits_amount' nos metadados do pagamento {payment_id}.")
            return

        user_id = int(user_id)
        credits_to_add = int(credits_to_add)

        logger.info(
            f"Pagamento {payment_id} aprovado para o usuário {user_id}. Adicionando {credits_to_add} créditos."
        )

        # 4. Chamar o CreditService para adicionar os créditos de forma atômica
        await CreditService.add_credits_from_purchase(
            db=db,
            user_id=user_id,
            amount=credits_to_add,
            payment_id=str(payment_id)
        )

        logger.info(f"Processo de webhook para o pagamento {payment_id} concluído com sucesso.")

    except Exception as e:
        logger.error(f"Erro ao processar webhook do Mercado Pago para o pagamento {payment_id}: {e}", exc_info=True)
        # É importante não levantar uma exceção HTTP aqui para que o Mercado Pago não continue tentando reenviar
        # indefinidamente se o erro for na nossa lógica interna. O erro já foi logado para análise.
        pass

# --------------------------------------------------------------------------------
# --- Controle F: FIM - payment_service.py
# --------------------------------------------------------------------------------