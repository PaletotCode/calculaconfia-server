import mercadopago
from datetime import datetime, timedelta
from ..core.config import settings
from ..core.logging_config import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# Inicializa o SDK
sdk = mercadopago.SDK(settings.MERCADO_PAGO_ACCESS_TOKEN)

def create_pix_payment(user_id: int, amount: float, description: str):
    expiration_time = datetime.utcnow() + timedelta(minutes=30)

    payment_data = {
        "transaction_amount": amount,
        "description": description,
        "payment_method_id": "pix",
        "payer": {
            "email": f"user-{user_id}@calculaconfia.com",
        },
        "external_reference": str(user_id),
        "date_of_expiration": expiration_time.strftime("%Y-%m-%dT%H:%M:%S.000-03:00")
    }

    try:
        payment_response = sdk.payment().create(payment_data)
        payment = payment_response.get("response")

        if payment and payment.get("status") == "pending":
            transaction_data = payment.get("point_of_interaction", {}).get("transaction_data", {})
            qr_code = transaction_data.get("qr_code")
            qr_code_base64 = transaction_data.get("qr_code_base64")
            
            if not qr_code:
                raise Exception("Dados do PIX não encontrados na resposta do Mercado Pago.")

            return {"qr_code": qr_code, "qr_code_base64": qr_code_base64, "payment_id": payment.get("id")}
        else:
            raise Exception("Falha ao criar pagamento PIX no Mercado Pago.")
    
    except Exception as e:
        logger.error(f"Erro na SDK do Mercado Pago: {e}", exc_info=True)
        raise

async def handle_webhook_notification(data_id: str, db: AsyncSession):
    # Esta função será chamada pelo endpoint do webhook
    # A lógica de adicionar créditos será colocada aqui.
    # Por enquanto, vamos apenas logar a notificação.
    logger.info(f"Notificação de pagamento recebida para o ID: {data_id}")
    # Aqui virá a lógica de adicionar créditos, que faremos a seguir.
    pass