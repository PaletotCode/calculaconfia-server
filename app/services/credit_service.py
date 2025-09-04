# app/services/credit_service.py (NOVO ARQUIVO)

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta
from ..models_schemas.models import CreditTransaction, User
from ..core.logging_config import get_logger
from .main_service import CalculationService

logger = get_logger(__name__)

class CreditService:
    @staticmethod
    async def add_credits_from_purchase(db: AsyncSession, user_id: int, amount: int, payment_id: str):
        """
        Adiciona créditos a um usuário após uma compra bem-sucedida e lida com o bônus de referência.
        """
        # Garante que a operação seja atômica
        async with db.begin_nested():
            # Busca o usuário para garantir que ele existe
            user_result = await db.execute(select(User).where(User.id == user_id))
            user = user_result.scalar_one_or_none()
            if not user:
                logger.error(f"Usuário {user_id} não encontrado para adicionar créditos.")
                return
            
            # Verifica se essa transação já foi processada para evitar duplicidade
            existing_transaction = await db.execute(
                select(CreditTransaction).where(CreditTransaction.reference_id == f"mp_{payment_id}")
            )
            if existing_transaction.scalar_one_or_none():
                logger.warning(f"Transação {payment_id} já processada. Ignorando.")
                return

            # Adiciona os créditos comprados
            balance_before = await CalculationService._get_valid_credits_balance(db, user.id)
            expires_at = datetime.utcnow() + timedelta(days=40) # Validade de 40 dias
            
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
            
            # Processa o bônus de indicação
            await CreditService._process_referral_bonus(db, user)
        
        await db.commit()
    
    @staticmethod
    async def _process_referral_bonus(db: AsyncSession, user: User):
        """
        Processa o bônus para o usuário que indicou.
        """
        if not user.referred_by_id:
            return  # Usuário não foi indicado por ninguém

        # Verifica se já foi concedido bônus por este usuário
        stmt_bonus_given = select(CreditTransaction).where(
            CreditTransaction.reference_id == f"referral_{user.id}"
        )
        bonus_already_given = await db.execute(stmt_bonus_given)
        if bonus_already_given.scalar_one_or_none():
            logger.info(f"Bônus de indicação para {user.id} já foi concedido.")
            return

        # Busca o usuário que indicou
        referrer_result = await db.execute(select(User).where(User.id == user.referred_by_id))
        referrer = referrer_result.scalar_one_or_none()

        if not referrer or referrer.referral_credits_earned >= 3:
            return # Não processa se o indicador não existe ou já atingiu o limite

        # Adiciona 1 crédito bônus ao indicador
        balance_before = await CalculationService._get_valid_credits_balance(db, referrer.id)
        referrer.referral_credits_earned += 1
        
        bonus_transaction = CreditTransaction(
            user_id=referrer.id,
            transaction_type="referral_bonus",
            amount=1,
            balance_before=balance_before,
            balance_after=balance_before + 1,
            description=f"Bônus por indicação do usuário {user.id}",
            reference_id=f"referral_{user.id}",
            expires_at=datetime.utcnow() + timedelta(days=60) # Validade de 60 dias
        )
        db.add(bonus_transaction)
        logger.info(f"Bônus de indicação processado para o user_id: {referrer.id}")