from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
import redis.asyncio as redis
from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend

from .config import settings

Base = declarative_base()

# Database Engine - Configuração comercial otimizada
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.ENVIRONMENT == "development",
    pool_pre_ping=True,
    pool_size=20,           # Pool maior para produção
    max_overflow=30,        # Buffer para picos de tráfego
    pool_recycle=3600,      # Reciclar conexões a cada hora
    pool_timeout=30,        # Timeout de 30 segundos
    connect_args={
        "command_timeout": 5,
        "server_settings": {
            "jit": "off"  # Otimização para queries simples
        }
    }
)

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)


async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


# Redis Cache Setup
redis_client = None


async def init_cache():
    """Inicializa o cache Redis"""
    global redis_client
    redis_client = redis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        max_connections=20
    )
    
    FastAPICache.init(
        RedisBackend(redis_client),
        prefix="torres-cache"
    )


async def get_redis():
    """Dependência para acessar o Redis"""
    return redis_client


async def close_cache():
    """Fecha conexões do cache"""
    if redis_client:
        await redis_client.close()