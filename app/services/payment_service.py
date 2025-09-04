import os
import mercadopago
from ..core.config import settings
from ..core.logging_config import get_logger
from sqlalchemy.ext.asyncio import AsyncSession
from ..models_schemas.models import User

logger = get_logger(__name__)

# Inicializa o SDK do Mercado Pago
sdk = mercadopago.SDK(settings.MERCADO_PAGO_ACCESS_TOKEN)

def create_payment_preference(user: User):
    """Cria uma preferência de pagamento no Mercado Pago."""
    public_base_url = os.getenv("PUBLIC_BASE_URL")
    frontend_url = os.getenv("FRONTEND_URL")

    if not public_base_url or not frontend_url:
        raise ValueError("PUBLIC_BASE_URL and FRONTEND_URL environment variables must be set")

    preference_data = {
        "items": [
            {
                "id": "CREDITS-3",
                "title": "Pacote de 3 Créditos",
                "description": "Créditos para usar na calculadora do Torres Project",
                "category_id": "services",
                "quantity": 1,
                "unit_price": 5.00,
            }
        ],
        "payer": {
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
        },
        "notification_url": f"{public_base_url.rstrip('/')}/api/v1/payments/webhook",
        "statement_descriptor": "TORRESPROJECT",
        "back_urls": {
            "success": f"{frontend_url.rstrip('/')}/pagamento/sucesso",
            "failure": f"{frontend_url.rstrip('/')}/pagamento/falha",
            "pending": f"{frontend_url.rstrip('/')}/pagamento/pendente",
        },
        "external_reference": str(user.id),
    }

    try:
        preference_response = sdk.preference().create(preference_data)
        preference = preference_response.get("response", {})
        preference_id = preference.get("id")
        init_point = preference.get("init_point")

        if not preference_id or not init_point:
            raise Exception("Falha ao criar preferência de pagamento no Mercado Pago.")

        return {"preference_id": preference_id, "init_point": init_point}
    
    except Exception as e:
        logger.error(f"Erro na SDK do Mercado Pago: {e}", exc_info=True)
        raise

async def handle_webhook_notification(data_id: str, db: AsyncSession):
    """Processa notificações recebidas pelo webhook do Mercado Pago."""
    logger.info(f"Notificação de pagamento recebida para o ID: {data_id}")
    # TODO: implementar lógica de adição de créditos
    pass