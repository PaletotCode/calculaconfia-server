import re
from dataclasses import dataclass
from typing import Any, Optional
from datetime import datetime, timedelta  # ADICIONE ESTA LINHA
from uuid import uuid4

import mercadopago
from fastapi import HTTPException, Request, status
from fastapi_cache import FastAPICache
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


def _extract_credits_from_items(items: Optional[list[dict[str, Any]]]) -> Optional[int]:
    """Extrai quantidade total de créditos a partir da lista de itens retornados pelo MP."""
    if not items:
        return None

    total = 0
    found = False
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("id", "title", "description"):
            raw_value = item.get(key)
            if not isinstance(raw_value, str):
                continue
            match = re.search(r"(\d+)", raw_value)
            if match:
                credits = int(match.group(1))
                if credits > 0:
                    total += credits
                    found = True
                    break

    return total if found else None


def _fetch_merchant_order(merchant_order_id: Any) -> dict[str, Any]:
    """Consulta a merchant_order no SDK lidando com diferenças de método entre versões."""
    if not sdk:
        return {}

    try:
        merchant_api = sdk.merchant_order()
        fetch_method = getattr(merchant_api, "get", None) or getattr(merchant_api, "find_by_id", None)
        if not callable(fetch_method):
            raise AttributeError("Mercado Pago SDK merchant_order client lacks fetch method")

        response = fetch_method(merchant_order_id)
        if isinstance(response, dict):
            return response.get("response") or {}
    except Exception as exc:  # pragma: no cover - integrações externas
        logger.warning(
            "Falha ao consultar merchant_order vinculada ao pagamento.",
            merchant_order_id=merchant_order_id,
            error=str(exc),
        )
    return {}


def _resolve_credits_from_order(payment_info: dict[str, Any]) -> Optional[int]:
    """Tenta identificar a quantidade de créditos consultando a merchant_order vinculada."""
    if not sdk:
        return None

    order = payment_info.get("order") or {}
    merchant_order_id = order.get("id") if isinstance(order, dict) else None
    if not merchant_order_id:
        return None

    merchant_order = _fetch_merchant_order(merchant_order_id)
    if not merchant_order:
        return None

    items = merchant_order.get("items") or []
    return _extract_credits_from_items(items)


@dataclass
class PaymentProcessingResult:
    payment_id: str
    status: Optional[str]
    user_id: Optional[int]
    credits_amount: Optional[int]
    processed: bool
    already_processed: bool
    detail: Optional[str] = None


async def process_payment_and_award(
    payment_id: str,
    db: AsyncSession,
    expected_user_id: Optional[int] = None,
) -> PaymentProcessingResult:
    """Consulta o pagamento no Mercado Pago e garante que os créditos do usuário sejam liberados."""
    if not sdk:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sistema de pagamento indisponível.",
        )

    try:
        payment_response = sdk.payment().get(payment_id)
    except Exception as exc:  # pragma: no cover - dependência externa
        logger.error(
            "Erro ao consultar pagamento no Mercado Pago.",
            payment_id=payment_id,
            error=str(exc),
        )
        raise

    payment_info = payment_response.get("response") if isinstance(payment_response, dict) else None
    if not payment_info:
        logger.error(
            "Resposta inválida ao tentar obter informações do pagamento.",
            payment_id=payment_id,
            response=payment_response,
        )
        return PaymentProcessingResult(
            payment_id=payment_id,
            status=None,
            user_id=None,
            credits_amount=None,
            processed=False,
            already_processed=False,
            detail="payment_not_found",
        )

    status_pagamento = payment_info.get("status")
    external_reference = payment_info.get("external_reference")
    user_id = int(external_reference) if external_reference is not None else None
    metadata = payment_info.get("metadata") or {}
    credits_to_add = metadata.get("credits_amount")

    if credits_to_add is None:
        credits_to_add = _resolve_credits_from_order(payment_info)

    if status_pagamento != "approved":
        logger.info(
            "Pagamento não está aprovado no Mercado Pago.",
            payment_id=payment_id,
            status=status_pagamento,
        )
        return PaymentProcessingResult(
            payment_id=payment_id,
            status=status_pagamento,
            user_id=user_id,
            credits_amount=None,
            processed=False,
            already_processed=False,
            detail="payment_not_approved",
        )

    if expected_user_id is not None and user_id is not None and user_id != expected_user_id:
        logger.warning(
            "Pagamento aprovado pertence a outro usuário.",
            payment_id=payment_id,
            user_id=user_id,
            expected_user_id=expected_user_id,
        )
        return PaymentProcessingResult(
            payment_id=payment_id,
            status=status_pagamento,
            user_id=user_id,
            credits_amount=None,
            processed=False,
            already_processed=False,
            detail="unexpected_user",
        )

    if not user_id:
        logger.error(
            "Pagamento aprovado sem external_reference (user_id).",
            payment_id=payment_id,
        )
        return PaymentProcessingResult(
            payment_id=payment_id,
            status=status_pagamento,
            user_id=None,
            credits_amount=None,
            processed=False,
            already_processed=False,
            detail="missing_external_reference",
        )

    if credits_to_add is None:
        logger.error(
            "Pagamento aprovado, mas não foi possível determinar os créditos.",
            payment_id=payment_id,
        )
        return PaymentProcessingResult(
            payment_id=payment_id,
            status=status_pagamento,
            user_id=user_id,
            credits_amount=None,
            processed=False,
            already_processed=False,
            detail="missing_credits_amount",
        )

    credits_int = int(credits_to_add)
    already_processed = await CreditService.has_processed_payment(db, payment_id)

    if already_processed:
        logger.info(
            "Pagamento já havia sido processado anteriormente (idempotente).",
            payment_id=payment_id,
            user_id=user_id,
        )
        try:
            await FastAPICache.clear(namespace="user_me")
        except Exception as exc:  # pragma: no cover - evita falha caso cache não esteja inicializado
            logger.warning(
                "Falha ao limpar cache após detectar pagamento já processado.",
                payment_id=payment_id,
                error=str(exc),
            )
        return PaymentProcessingResult(
            payment_id=payment_id,
            status=status_pagamento,
            user_id=user_id,
            credits_amount=credits_int,
            processed=False,
            already_processed=True,
            detail="already_processed",
        )

    await CreditService.add_credits_from_purchase(
        db=db,
        user_id=user_id,
        amount=credits_int,
        payment_id=str(payment_id),
    )

    logger.info(
        "Créditos adicionados ao usuário após confirmação do pagamento.",
        payment_id=payment_id,
        user_id=user_id,
        credits=credits_int,
    )

    try:
        await FastAPICache.clear(namespace="user_me")
    except Exception as exc:  # pragma: no cover - evita derrubar fluxo em caso de indisponibilidade do cache
        logger.warning(
            "Falha ao limpar cache após adicionar créditos.",
            payment_id=payment_id,
            error=str(exc),
        )

    return PaymentProcessingResult(
        payment_id=payment_id,
        status=status_pagamento,
        user_id=user_id,
        credits_amount=credits_int,
        processed=True,
        already_processed=False,
        detail="credits_added",
    )

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


def _fetch_preference(preference_id: str) -> dict[str, Any]:
    if not sdk:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sistema de pagamento indisponível.",
        )

    try:
        response = sdk.preference().get(preference_id)
    except Exception as exc:  # pragma: no cover - integrações externas
        logger.error(
            "Erro ao consultar preferência no Mercado Pago.",
            preference_id=preference_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Falha ao consultar preferência de pagamento.",
        ) from exc

    preference = response.get("response") if isinstance(response, dict) else None
    if not preference:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Preferência de pagamento não encontrada.",
        )

    return preference


def _sum_amount_from_items(items: Optional[list[dict[str, Any]]]) -> float:
    if not items:
        return 0.0

    total = 0.0
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            unit_price = float(item.get("unit_price", 0))
            quantity = int(item.get("quantity", 1))
        except (TypeError, ValueError):
            continue

        total += unit_price * quantity

    return round(total, 2)


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
        "expires": True,
        "expiration_date_to": (datetime.utcnow() + timedelta(minutes=settings.MERCADO_PAGO_PIX_EXPIRATION_MINUTES)).isoformat(timespec="milliseconds") + "Z",
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


def create_pix_payment(
    user: User,
    preference_id: str,
    *,
    idempotency_key: Optional[str] = None,
) -> dict[str, Any]:
    if not preference_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="preference_id é obrigatório",
        )

    if not user.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Usuário autenticado não possui e-mail válido cadastrado.",
        )

    preference = _fetch_preference(preference_id)

    external_reference = preference.get("external_reference")
    if external_reference and str(external_reference) != str(user.id):
        logger.warning(
            "Usuário tentou utilizar preferência pertencente a outro usuário.",
            preference_id=preference_id,
            preference_owner=external_reference,
            current_user=user.id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Preferência não pertence ao usuário autenticado.",
        )

    amount = _sum_amount_from_items(preference.get("items"))
    if amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Valor da preferência inválido para gerar pagamento PIX.",
        )

    metadata = preference.get("metadata") or {}
    credits_amount = metadata.get("credits_amount")
    if credits_amount is None:
        credits_amount = _extract_credits_from_items(preference.get("items"))

    public_base_url = _normalize_base_url(settings.PUBLIC_BASE_URL, "PUBLIC_BASE_URL")

    payment_data = {
        "transaction_amount": amount,
        "payment_method_id": "pix",
        "description": (preference.get("items") or [{}])[0].get("title")
        or "Créditos CalculaConfia",
        "external_reference": str(user.id),
        "binary_mode": True,
        "date_of_expiration": (
            datetime.utcnow()
            + timedelta(minutes=settings.MERCADO_PAGO_PIX_EXPIRATION_MINUTES)
        ).isoformat(timespec="milliseconds")
        + "Z",
        "notification_url": f"{public_base_url.rstrip('/')}/api/v1/payments/webhook",
        "metadata": {
            "user_id": user.id,
            "credits_amount": credits_amount or metadata.get("credits_amount") or 3,
            "preference_id": preference_id,
        },
        "payer": {
            "email": user.email,
            "first_name": user.first_name or "Usuário",
            "last_name": user.last_name or "CalculaConfia",
        },
    }

    request_options = mercadopago.config.RequestOptions()
    request_options.custom_headers = {
        "x-idempotency-key": idempotency_key or str(uuid4()),
    }

    try:
        logger.info(
            "Criando pagamento PIX a partir da preferência.",
            user_id=user.id,
            preference_id=preference_id,
        )
        payment_response = sdk.payment().create(payment_data, request_options)
    except Exception as exc:  # pragma: no cover - dependência externa
        logger.error(
            "Erro ao criar pagamento PIX no Mercado Pago.",
            preference_id=preference_id,
            user_id=user.id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Não foi possível gerar o pagamento PIX.",
        ) from exc

    payment = payment_response.get("response") if isinstance(payment_response, dict) else None
    if not payment or "id" not in payment:
        logger.error(
            "Resposta inválida ao criar pagamento PIX.",
            response=payment_response,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Falha ao criar pagamento PIX.",
        )

    return payment

async def handle_webhook_notification(request: Request, db: AsyncSession):
    """
    Processa notificações recebidas pelo webhook do Mercado Pago com validação de segurança.
    Control F Amigável: handle_webhook_notification
    """
    if not sdk:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sistema de pagamento indisponível.")

    # Tenta ler JSON; se falhar, usa query params (formato legado)
    try:
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

            result = await process_payment_and_award(str(payment_id), db)
            if result.processed:
                logger.info(
                    "Créditos adicionados via webhook.",
                    payment_id=result.payment_id,
                    user_id=result.user_id,
                    credits=result.credits_amount,
                )
            elif result.detail == "already_processed":
                logger.info(
                    "Webhook recebido para pagamento já processado.",
                    payment_id=result.payment_id,
                    user_id=result.user_id,
                )
            else:
                logger.info(
                    "Webhook processado sem inserir créditos (status não aprovado ou dados ausentes).",
                    payment_id=result.payment_id,
                    status=result.status,
                    detail=result.detail,
                )

        elif notif_type == "merchant_order":
            order_id = (
                (notification_data.get("data", {}) if isinstance(notification_data, dict) else {}).get("id")
                or params.get("id")
            )
            if not order_id:
                logger.warning("Webhook 'merchant_order' sem ID.")
                return

            logger.info(f"Buscando merchant_order {order_id} no Mercado Pago.")
            order_info = _fetch_merchant_order(order_id)
            if not order_info:
                logger.error(f"Não foi possível obter merchant_order {order_id}.")
                return

            payments = order_info.get("payments") or []
            if not payments:
                logger.info(f"merchant_order {order_id} sem pagamentos associados.")
                return

            # Processa todos pagamentos aprovados (idempotência garantida por CreditService)
            for payment in payments:
                payment_id = payment.get("id")
                if not payment_id:
                    continue
                result = await process_payment_and_award(str(payment_id), db)
                if result.processed:
                    logger.info(
                        "Créditos adicionados via merchant_order.",
                        merchant_order_id=order_id,
                        payment_id=result.payment_id,
                        user_id=result.user_id,
                    )
                elif result.detail == "already_processed":
                    logger.info(
                        "Pagamento da merchant_order já havia sido processado.",
                        merchant_order_id=order_id,
                        payment_id=result.payment_id,
                    )

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
