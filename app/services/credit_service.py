# app/services/credit_service.py (NOVO ARQUIVO)

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta
from ..models_schemas.models import CreditTransaction, User
from ..core.logging_config import get_logger
from .main_service import CalculationService, UserService

logger = get_logger(__name__)

class CreditService:

    @staticmethod
    async def add_credits_from_purchase(db: AsyncSession, user_id: int, amount: int, payment_id: str):
        """
        Adiciona créditos, gera o código de referência na primeira compra e lida com bônus.
        """
        async with db.begin_nested():
            user_result = await db.execute(select(User).where(User.id == user_id))
            user = user_result.scalar_one_or_none()
            if not user:
                logger.error(f"Usuário {user_id} não encontrado para adicionar créditos.")
                return

            existing_transaction = await db.execute(
                select(CreditTransaction).where(CreditTransaction.reference_id == f"mp_{payment_id}")
            )
            if existing_transaction.scalar_one_or_none():
                logger.warning(f"Transação {payment_id} já processada. Ignorando.")
                return

            # GERA O CÓDIGO DE REFERÊNCIA NA PRIMEIRA COMPRA
            if not user.referral_code:
                user.referral_code = UserService._generate_referral_code(user.first_name, user.id)
                logger.info(f"Código de referência '{user.referral_code}' gerado para o usuário {user.id} na primeira compra.")

            balance_before = await CalculationService._get_valid_credits_balance(db, user.id)
            expires_at = datetime.utcnow() + timedelta(days=40)

            new_transaction = CreditTransaction(
                user_id=user_id,
                transaction_type="purchase",
                amount=amount,
                balance_before=balance_before,
                balance_after=balance_before + amount,
                description=f"Compra de {amount} créditos via PIX",
                reference_id=f"mp_{payment_id}",
                expires_at=expires_at
            )
            db.add(new_transaction)
            logger.info(f"{amount} créditos adicionados ao user_id: {user_id} pela compra {payment_id}")

            # Processa o bônus de indicação (para ambos, se aplicável)
            await CreditService._process_referral_bonus(db, user)

        await db.commit()
    
    @staticmethod
    async def _process_referral_bonus(db: AsyncSession, user: User):
        """
        Processa o bônus para o indicador E para o indicado, se aplicável.
        """
        if not user.referred_by_id:
            return  # Usuário não foi indicado por ninguém

        # Verifica se o bônus já foi concedido para o INDICADO
        stmt_bonus_given_to_user = select(CreditTransaction).where(
            CreditTransaction.reference_id == f"referral_bonus_for_{user.id}"
        )
        bonus_already_given = await db.execute(stmt_bonus_given_to_user)
        if not bonus_already_given.scalar_one_or_none():
            # BÔNUS PARA O INDICADO (quem usou o código)
            balance_before_user = await CalculationService._get_valid_credits_balance(db, user.id)
            bonus_for_user = CreditTransaction(
                user_id=user.id,
                transaction_type="referral_bonus",
                amount=1,
                balance_before=balance_before_user,
                balance_after=balance_before_user + 1,
                description=f"Bônus por usar um código de convite.",
                reference_id=f"referral_bonus_for_{user.id}",
                expires_at=datetime.utcnow() + timedelta(days=60)
            )
            db.add(bonus_for_user)
            logger.info(f"Bônus de indicação (1 crédito) concedido ao novo usuário {user.id}")

        # --- Lógica para o INDICADOR ---
        referrer_result = await db.execute(select(User).where(User.id == user.referred_by_id))
        referrer = referrer_result.scalar_one_or_none()

        if not referrer or referrer.referral_credits_earned >= 3:
            return

        # Verifica se o bônus já foi concedido PELO indicado
        stmt_bonus_given_by_user = select(CreditTransaction).where(
            CreditTransaction.reference_id == f"referral_from_{user.id}"
        )
        bonus_from_user_exists = await db.execute(stmt_bonus_given_by_user)
        if not bonus_from_user_exists.scalar_one_or_none():
            # BÔNUS PARA O INDICADOR (dono do código)
            balance_before_referrer = await CalculationService._get_valid_credits_balance(db, referrer.id)
            referrer.referral_credits_earned += 1

            bonus_for_referrer = CreditTransaction(
                user_id=referrer.id,
                transaction_type="referral_bonus",
                amount=1,
                balance_before=balance_before_referrer,
                balance_after=balance_before_referrer + 1,
                description=f"Bônus por indicação do usuário {user.id}",
                reference_id=f"referral_from_{user.id}",
                expires_at=datetime.utcnow() + timedelta(days=60)
            )
            db.add(bonus_for_referrer)
            logger.info(f"Bônus de indicação (1 crédito) processado para o indicador {referrer.id}")