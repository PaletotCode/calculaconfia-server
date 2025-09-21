from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging_config import get_logger
from ..models_schemas.models import CreditTransaction, User
from .main_service import CalculationService, UserService


logger = get_logger(__name__)


class CreditService:
    """Centraliza operacoes de credito (compra e bonus de indicacao)."""

    @staticmethod
    async def has_processed_payment(db: AsyncSession, payment_id: str) -> bool:
        """Retorna True se ja existe transacao vinculada ao pagamento informado."""
        result = await db.execute(
            select(CreditTransaction).where(
                CreditTransaction.reference_id == f"mp_{payment_id}"
            )
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def _refresh_user_legacy_balance(db: AsyncSession, user: User) -> None:
        """Mantem o campo legado user.credits alinhado ao saldo valido atual."""
        if not user:
            return
        await db.flush()
        current_balance = await CalculationService._get_valid_credits_balance(db, user.id)
        user.credits = current_balance

    @staticmethod
    async def add_credits_from_purchase(
        db: AsyncSession,
        user_id: int,
        amount: int,
        payment_id: str,
    ) -> None:
        """Adiciona creditos, gera referral na primeira compra e processa bonus."""
        async with db.begin_nested():
            user_result = await db.execute(select(User).where(User.id == user_id))
            user = user_result.scalar_one_or_none()
            if not user:
                logger.error("Usuario %s nao encontrado para adicionar creditos.", user_id)
                return

            if await CreditService.has_processed_payment(db, payment_id):
                logger.warning("Transacao %s ja processada. Ignorando.", payment_id)
                return

            # Gera o codigo de indicacao na primeira compra
            if not user.referral_code:
                user.referral_code = UserService._generate_referral_code(user.first_name, user.id)
                logger.info(
                    "Codigo de referencia '%s' gerado para o usuario %s na primeira compra.",
                    user.referral_code,
                    user.id,
                )

            balance_before = await CalculationService._get_valid_credits_balance(db, user.id)
            expires_at = datetime.utcnow() + timedelta(days=40)

            purchase_tx = CreditTransaction(
                user_id=user_id,
                transaction_type="purchase",
                amount=amount,
                balance_before=balance_before,
                balance_after=balance_before + amount,
                description=f"Compra de {amount} creditos via PIX",
                reference_id=f"mp_{payment_id}",
                expires_at=expires_at,
            )
            db.add(purchase_tx)
            logger.info("%s creditos adicionados ao user_id %s pela compra %s", amount, user_id, payment_id)

            await CreditService._refresh_user_legacy_balance(db, user)

            # Bonus de indicacao (se aplicavel)
            await CreditService._process_referral_bonus(db, user)

        await db.commit()

    @staticmethod
    async def _process_referral_bonus(db: AsyncSession, user: User) -> None:
        """Processa o bonus para quem usou o codigo e para o indicador (quando aplicavel)."""
        if not user or not user.referred_by_id:
            return

        # Bonus para o usuario indicado (apenas uma vez)
        stmt_bonus_to_user = select(CreditTransaction).where(
            CreditTransaction.reference_id == f"referral_bonus_for_{user.id}"
        )
        bonus_to_user = await db.execute(stmt_bonus_to_user)
        if not bonus_to_user.scalar_one_or_none():
            balance_before_user = await CalculationService._get_valid_credits_balance(db, user.id)
            bonus_user_tx = CreditTransaction(
                user_id=user.id,
                transaction_type="referral_bonus",
                amount=1,
                balance_before=balance_before_user,
                balance_after=balance_before_user + 1,
                description="Bonus por usar um codigo de convite.",
                reference_id=f"referral_bonus_for_{user.id}",
                expires_at=datetime.utcnow() + timedelta(days=60),
            )
            db.add(bonus_user_tx)
            logger.info("Bonus de indicacao (1 credito) concedido ao novo usuario %s", user.id)
            await CreditService._refresh_user_legacy_balance(db, user)

        # Bonus para o indicador (codigo so pode ser usado uma vez)
        referrer_result = await db.execute(select(User).where(User.id == user.referred_by_id))
        referrer = referrer_result.scalar_one_or_none()
        if not referrer:
            logger.warning("Referrer %s nao encontrado ao processar bonus.", user.referred_by_id)
            return

        if referrer.referral_credits_earned >= 1:
            logger.info("Referrer %s ja resgatou o bonus maximo permitido.", referrer.id)
            return

        stmt_bonus_by_user = select(CreditTransaction).where(
            CreditTransaction.reference_id == f"referral_from_{user.id}"
        )
        bonus_ref_exists = await db.execute(stmt_bonus_by_user)
        if bonus_ref_exists.scalar_one_or_none():
            return

        balance_before_referrer = await CalculationService._get_valid_credits_balance(db, referrer.id)
        referrer.referral_credits_earned += 1

        bonus_ref_tx = CreditTransaction(
            user_id=referrer.id,
            transaction_type="referral_bonus",
            amount=1,
            balance_before=balance_before_referrer,
            balance_after=balance_before_referrer + 1,
            description=f"Bonus por indicacao do usuario {user.id}",
            reference_id=f"referral_from_{user.id}",
            expires_at=datetime.utcnow() + timedelta(days=60),
        )
        db.add(bonus_ref_tx)
        logger.info("Bonus de indicacao (1 credito) processado para o indicador %s", referrer.id)
        await CreditService._refresh_user_legacy_balance(db, referrer)
