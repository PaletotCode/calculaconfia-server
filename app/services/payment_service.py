import mercadopago
from fastapi import Request, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.logging_config import get_logger
from ..models_schemas.models import User
from .credit_service import CreditService
from urllib.parse import urlparse

logger = get_logger(__name__)

# --------------------------------------------------------------------------------
# --- Controle F: INÍCIO - payment_service.py
# --------------------------------------------------------------------------------

def _normalize_base_url(value: str | None, env_name: str) -> str:
    """Normaliza URLs base removendo barras finais e caminhos extras."""
    if not value or not value.strip():
        logger.error(
            "Variável de ambiente obrigatória não configurada.",
            env_var=env_name,
        )
        raise ValueError(f"Variável de ambiente {env_name} não configurada.")

    cleaned = value.strip().strip('"').strip("'")
    parsed = urlparse(cleaned)

    if not parsed.scheme or not parsed.netloc:
        candidate = cleaned
        if "//" not in candidate:
            candidate = f"https://{candidate}"
        parsed = urlparse(candidate)

    if not parsed.scheme or not parsed.netloc:
        logger.error(
            "Valor inválido para variável de ambiente de URL.",
            env_var=env_name,
            value=cleaned,
        )
        raise ValueError(f"Valor inválido configurado em {env_name}.")

    if parsed.path and parsed.path not in ("", "/"):
        logger.warning(
            "Valor de URL contém caminho adicional; ignorando caminho informado.",
            env_var=env_name,
            path=parsed.path,
        )

    normalized = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return normalized

# Inicializa o SDK do Mercado Pago
# Garante que a chave de acesso não seja nula ou vazia
if not settings.MERCADO_PAGO_ACCESS_TOKEN:
    logger.critical("MERCADO_PAGO_ACCESS_TOKEN não está configurado!")
    sdk = None
else:
    sdk = mercadopago.SDK(settings.MERCADO_PAGO_ACCESS_TOKEN)

# app/services/payment_service.py

def create_payment_preference(user: User, item_details: dict):
    """
    Cria uma preferência de pagamento no Mercado Pago, garantindo dados válidos para o pagador.
    """
    if not sdk:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sistema de pagamento indisponível.")

    public_base_url = _normalize_base_url(settings.PUBLIC_BASE_URL, "PUBLIC_BASE_URL")
    frontend_url = _normalize_base_url(settings.FRONTEND_URL, "FRONTEND_URL")
    seller_email = settings.MERCADO_PAGO_SELLER_EMAIL

    # --- INÍCIO DA CORREÇÃO DE DADOS DO PAGADOR ---
    # Garante que nome e sobrenome não sejam nulos para o Mercado Pago
    first_name = user.first_name if user.first_name else "Usuário"
    last_name = user.last_name if user.last_name else "CalculaConfia"

    # Evita auto-pagamento: quando o e-mail do comprador é o mesmo do vendedor,
    # o Checkout Pro desabilita o botão para impedir pagar a si mesmo.
    if seller_email and user.email and user.email.strip().lower() == seller_email.strip().lower():
        logger.warning(
            "Tentativa de auto-pagamento detectada: payer == seller",
            payer_email=user.email
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Não é possível criar pagamento: o email do pagador é o mesmo da conta vendedora. "
                "Use um email/conta do Mercado Pago diferente para pagar."
            ),
        )
    # --- FIM DA CORREÇÃO ---

    preference_data = {
        "items": [
            {
                "id": item_details.get("id", "CREDITS-PACK"),
                "title": item_details.get("title", "Pacote de Créditos"),
                "description": "Créditos para a calculadora Torres Project",
                "category_id": "services",
                "quantity": 1,
                "unit_price": float(item_details.get("price", 5.00)),
            }
        ],
        # Para Checkout Pro, o objeto payer usa chaves 'name' e 'surname'
        # (em Payments API direta seriam 'first_name'/'last_name').
        "payer": {
            "email": user.email,
            "name": first_name,
            "surname": last_name,
        },
        "payment_methods": {
            # Mantém métodos liberados e dá destaque ao PIX.
            "default_payment_method_id": "pix",
            "excluded_payment_methods": [],
            "excluded_payment_types": [
                {"id": "ticket"},  # Exclui boleto
                {"id": "atm"},
            ],
            "installments": 1,
        },
        "notification_url": f"{public_base_url.rstrip('/')}/api/v1/payments/webhook",
        "statement_descriptor": "TORRESPROJECT",
        "back_urls": {
            "success": f"{frontend_url.rstrip('/')}/payment/success",
            "failure": f"{frontend_url.rstrip('/')}/payment/failure",
            "pending": f"{frontend_url.rstrip('/')}/payment/pending",
        },
        "auto_return": "approved",
        "binary_mode": True,
        "external_reference": str(user.id),
        "metadata": {
            "user_id": user.id,
            "credits_amount": item_details.get("credits", 3) # Usaremos o valor do pacote
        }
    }

    try:
        logger.info("Criando preferência de pagamento para o usuário.", user_id=user.id)
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

    # Tenta ler JSON; se falhar, usa query params (formato legado)
    notification_data = {}
    try:
        # Helper para extrair a quantidade de créditos a partir dos itens
        def _extract_credits_from_items(items):
            import re
            total = 0
            found = False
            for it in items or []:
                for key in ("id", "title", "description"):
                    val = it.get(key)
                    if not isinstance(val, str):
                        continue
                    m = re.search(r"(\d+)", val)
                    if m:
                        n = int(m.group(1))
                        if n > 0:
                            total += n
                            found = True
                            break
            return total if found else None
        notification_data = await request.json()
    except Exception:
        notification_data = {}

    params = dict(request.query_params) if request.query_params else {}
    notif_type = (
        (notification_data.get("type") if isinstance(notification_data, dict) else None)
        or params.get("type")
        or params.get("topic")
    )

    logger.info(
        "Notificação de webhook recebida do Mercado Pago.",
        type=notif_type,
        body=notification_data if notification_data else None,
        query=params if params else None,
    )

    try:
        # Suporta notificações tipo 'payment' (recomendado) e 'merchant_order' (alguns fluxos)
        if notif_type == "payment":
            payment_id = (
                (notification_data.get("data", {}) if isinstance(notification_data, dict) else {}).get("id")
                or params.get("id")
            )
            if not payment_id:
                logger.warning("Webhook 'payment' sem ID.")
                return

            # 1. Buscar os detalhes do pagamento
            logger.info(f"Buscando detalhes do pagamento {payment_id} no Mercado Pago.")
            payment_info_response = sdk.payment().get(payment_id)
            payment_info = payment_info_response.get("response")

            if not payment_info:
                logger.error(f"Não foi possível obter informações do pagamento {payment_id}.")
                return

            status_pagamento = payment_info.get("status")
            if status_pagamento != "approved":
                logger.info(
                    f"Pagamento {payment_id} não está aprovado. Status: {status_pagamento}. Ignorando.")
                return

            user_id = payment_info.get("external_reference")
            metadata = payment_info.get("metadata", {}) or {}
            credits_to_add = metadata.get("credits_amount")

            # Fallback: tentar extrair pelos itens da merchant_order vinculada
            if not credits_to_add:
                try:
                    order = payment_info.get("order") or {}
                    mo_id = order.get("id") if isinstance(order, dict) else None
                    if mo_id:
                        mo_resp = sdk.merchant_order().find_by_id(mo_id)
                        mo = mo_resp.get("response") or {}
                        items = mo.get("items") or []
                        credits_to_add = _extract_credits_from_items(items)
                except Exception:
                    credits_to_add = None

            if not user_id or not credits_to_add:
                logger.error(
                    f"Faltando 'external_reference' (user_id) ou não foi possível inferir créditos no pagamento {payment_id}.")
                return

            await CreditService.add_credits_from_purchase(
                db=db,
                user_id=int(user_id),
                amount=int(credits_to_add),
                payment_id=str(payment_id),
            )
            logger.info(
                f"Créditos adicionados via webhook para pagamento {payment_id} e usuário {user_id}.")

        elif notif_type == "merchant_order":
            order_id = (
                (notification_data.get("data", {}) if isinstance(notification_data, dict) else {}).get("id")
                or params.get("id")
            )
            if not order_id:
                logger.warning("Webhook 'merchant_order' sem ID.")
                return

            logger.info(f"Buscando merchant_order {order_id} no Mercado Pago.")
            order_info_resp = sdk.merchant_order().find_by_id(order_id)
            order_info = order_info_resp.get("response")
            if not order_info:
                logger.error(f"Não foi possível obter merchant_order {order_id}.")
                return

            payments = order_info.get("payments", []) or []
            if not payments:
                logger.info(f"merchant_order {order_id} sem pagamentos associados.")
                return

            # Processa todos pagamentos aprovados (idempotência garantida por CreditService)
            for p in payments:
                if (p.get("status") or p.get("status_detail")) and p.get("id"):
                    pid = p.get("id")
                    logger.info(f"Verificando pagamento {pid} da merchant_order {order_id}")
                    pay_resp = sdk.payment().get(pid)
                    pay = pay_resp.get("response")
                    if not pay:
                        continue
                    if pay.get("status") != "approved":
                        continue
                    user_id = pay.get("external_reference")
                    metadata = pay.get("metadata", {}) or {}
                    credits_to_add = metadata.get("credits_amount")
                    if not credits_to_add:
                        items = order_info.get("items") or []
                        credits_to_add = _extract_credits_from_items(items)
                    if not user_id or not credits_to_add:
                        continue
                    await CreditService.add_credits_from_purchase(
                        db=db,
                        user_id=int(user_id),
                        amount=int(credits_to_add),
                        payment_id=str(pid),
                    )
                    logger.info(
                        f"Créditos adicionados via merchant_order {order_id} pagamento {pid}.")

        else:
            logger.info("Tipo de notificação não suportado (ignorado).", type=notif_type)

    except Exception as e:
        logger.error(
            f"Erro ao processar webhook do Mercado Pago: {e}", exc_info=True
        )
        # Não propaga exceção para não causar retries excessivos do MP
        return

# --------------------------------------------------------------------------------
# --- Controle F: FIM - payment_service.py
# --------------------------------------------------------------------------------
