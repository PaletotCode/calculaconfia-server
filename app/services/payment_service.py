import mercadopago
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from ..core.config import settings
from ..services.credit_service import CreditService
from ..core.logging_config import get_logger

import hmac
import hashlib
from fastapi import Request

logger = get_logger(__name__)

# Inicializa o SDK com seu Access Token de produção
sdk = mercadopago.SDK(settings.MERCADO_PAGO_ACCESS_TOKEN)

def create_pix_payment(user_id: int, amount: float, description: str):
    """
    Cria uma preferência de pagamento para PIX no Mercado Pago.
    """
    expiration_time = datetime.utcnow() + timedelta(minutes=30)
    
    # URLs para onde o usuário será redirecionado após o pagamento [cite: 462]
    # NOTA: Troque 'https://www.seu-site.com' pelo seu domínio real do frontend.
    back_urls = {
        "success": "https://www.seu-site.com/pagamento/sucesso",
        "failure": "https://www.seu-site.com/pagamento/falha",
        "pending": "https://www.seu-site.com/pagamento/pendente"
    }

    payment_data = {
        "transaction_amount": amount,
        "description": description,
        "payment_method_id": "pix",
        "payer": {
            # O email é obrigatório, mas podemos usar um fictício se não o tivermos
            "email": f"user-{user_id}@calculaconfia.com",
        },
        # Usamos a external_reference para identificar o usuário no webhook [cite: 479]
        "external_reference": str(user_id),
        "date_of_expiration": expiration_time.strftime("%Y-%m-%dT%H:%M:%S.000-03:00"),
        "back_urls": back_urls, # [cite: 462]
        "auto_return": "approved" # Redireciona automaticamente se aprovado [cite: 470]
    }

    try:
        payment_response = sdk.payment().create(payment_data)
        payment = payment_response.get("response")

        if payment and payment.get("status") == "pending":
            transaction_data = payment.get("point_of_interaction", {}).get("transaction_data", {})
            qr_code = transaction_data.get("qr_code")
            qr_code_base64 = transaction_data.get("qr_code_base64")
            
            if not qr_code or not qr_code_base64:
                logger.error("Resposta do Mercado Pago não contém dados do PIX", extra=payment)
                raise Exception("Dados do PIX não encontrados na resposta do Mercado Pago.")

            return {"qr_code": qr_code, "qr_code_base64": qr_code_base64, "payment_id": payment.get("id")}
        else:
            logger.error("Falha ao criar pagamento PIX no Mercado Pago", extra=payment)
            raise Exception("Falha ao criar pagamento PIX no Mercado Pago.")
    
    except Exception as e:
        logger.error(f"Erro na SDK do Mercado Pago: {e}", exc_info=True)
        raise

async def handle_webhook_notification(data_id: str, db: AsyncSession):
    """
    Processa a notificação de pagamento recebida do Mercado Pago.
    """
    try:
        logger.info(f"Recebendo notificação para o payment_id: {data_id}")
        
        # Obtém as informações completas do pagamento usando o ID [cite: 908]
        payment_info_response = sdk.payment().get(data_id)
        payment_info = payment_info_response.get("response")

        if not payment_info:
            logger.error("Pagamento não encontrado no Mercado Pago", extra={"payment_id": data_id})
            return

        status = payment_info.get("status")
        external_reference = payment_info.get("external_reference")
        
        # Ação principal: se o pagamento foi aprovado e tem nossa referência
        if status == "approved" and external_reference:
            user_id = int(external_reference)
            logger.info(f"Pagamento APROVADO para user_id: {user_id}. Adicionando créditos.")
            
            # Adiciona 3 créditos ao usuário e processa bônus de referência se aplicável
            await CreditService.add_credits_from_purchase(db, user_id=user_id, amount=3, payment_id=data_id)
        else:
            logger.warning("Notificação recebida mas não processada (status não aprovado ou sem referência)", extra=payment_info)

    except Exception as e:
        logger.error(f"Erro ao processar webhook do Mercado Pago: {e}", exc_info=True)
        # Não levanta exceção para que o MP receba status 200 e não reenvie a notificação


async def validate_webhook_signature(request: Request) -> bool:
    """
    Valida a assinatura do webhook do Mercado Pago conforme a documentação.
    """
    x_signature = request.headers.get("x-signature")
    if not x_signature:
        return False

    # Extrai o timestamp (ts) e o hash (v1) do header [cite: 847]
    parts = {k.strip(): v.strip() for k, v in (part.split("=", 1) for part in x_signature.split(","))}
    ts = parts.get("ts")
    v1_hash = parts.get("v1")

    if not ts or not v1_hash:
        return False

    # Obtém os parâmetros da URL da requisição [cite: 839]
    data_id = request.query_params.get("data.id")
    
    # Cria o 'manifest' para assinar [cite: 850]
    manifest = f"id:{data_id};ts:{ts};"

    # Gera a assinatura HMAC-SHA256 usando sua chave secreta [cite: 857]
    secret = settings.MERCADO_PAGO_WEBHOOK_SECRET
    generated_hash = hmac.new(secret.encode(), msg=manifest.encode(), digestmod=hashlib.sha256).hexdigest()

    # Compara a assinatura gerada com a recebida [cite: 858]
    return hmac.compare_digest(generated_hash, v1_hash)
