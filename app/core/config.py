from pydantic import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://torres_user:torres_password@localhost:5432/torres_db"
    
    # Security
    SECRET_KEY: str = "change-this-super-secret-key-in-production-please"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # Redis Cache
    REDIS_URL: str = "redis://localhost:6379/0"
    CACHE_TTL: int = 3600
    
    # Celery Background Tasks
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"
    
    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"
    
    # Email Configuration (VALORES PADRÃO PARA DESENVOLVIMENTO)
    MAIL_USERNAME: str = "noreply@torresproject.local"
    MAIL_PASSWORD: str = "dummy-password-change-in-production"
    MAIL_FROM: str = "noreply@torresproject.local"
    MAIL_PORT: int = 587
    MAIL_SERVER: str = "smtp.gmail.com"
    MAIL_FROM_NAME: str = "Torres Project"
    MAIL_STARTTLS: bool = True
    MAIL_SSL_TLS: bool = False
    USE_CREDENTIALS: bool = True
    
    # Application
    APP_NAME: str = "Torres Project API"
    APP_VERSION: str = "2.0.0"
    ENVIRONMENT: str = "development"

    class Config:
        env_file = ".env"


settings = Settings()