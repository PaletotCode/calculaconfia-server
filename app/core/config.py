from pydantic_settings import BaseSettings
from typing import Optional
import os


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://torres_user:torres_password@localhost:5432/torres_db"
    
    # Postgres variables (para docker-compose)
    POSTGRES_DB: Optional[str] = None
    POSTGRES_USER: Optional[str] = None  
    POSTGRES_PASSWORD: Optional[str] = None
    
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
    
    # 🔥 SendGrid Configuration (MELHORADO)
    SENDGRID_API_KEY: Optional[str] = None
    MAIL_FROM: str = "paletot.business@gmail.com"
    MAIL_FROM_NAME: str = "Torres Project"

    # 🔥 ADICIONADO: Configurações do Twilio
    TWILIO_ACCOUNT_SID: Optional[str] = None
    TWILIO_AUTH_TOKEN: Optional[str] = None
    TWILIO_PHONE_NUMBER: Optional[str] = None
    
    # Email Configuration (VALORES PADRÃO PARA DESENVOLVIMENTO)
    MAIL_USERNAME: str = "paletot.business@gmail.com"
    MAIL_PASSWORD: str = "dummy-password-change-in-production"
    MAIL_PORT: int = 587
    MAIL_SERVER: str = "smtp.gmail.com"
    MAIL_STARTTLS: bool = True
    MAIL_SSL_TLS: bool = False
    USE_CREDENTIALS: bool = True
    
    # Application
    APP_NAME: str = "Torres Project API"
    APP_VERSION: str = "2.0.0"
    ENVIRONMENT: str = "development"

    class Config:
        env_file = ".env"  # 🔥 Importante: ler do arquivo .env
        case_sensitive = True
        extra = "ignore"  # 🔥 CORREÇÃO: Ignorar variáveis extras
    
    def __post_init__(self):
        """Validações após a inicialização"""
        if self.ENVIRONMENT == "production" and not self.SENDGRID_API_KEY:
            raise ValueError("SENDGRID_API_KEY é obrigatória em produção")


settings = Settings()

# 🔥 Log de debug para verificar se as chaves estão sendo carregadas
if settings.ENVIRONMENT == "development":
    sendgrid_status = "✅ Configurada" if settings.SENDGRID_API_KEY else "❌ NÃO Configurada"
    print(f"🔑 SENDGRID_API_KEY: {sendgrid_status}")
    
    # 🔥 ADICIONADO: Debug das configurações Twilio
    twilio_sid_status = "✅ Configurada" if settings.TWILIO_ACCOUNT_SID else "❌ NÃO Configurada"
    twilio_token_status = "✅ Configurada" if settings.TWILIO_AUTH_TOKEN else "❌ NÃO Configurada"
    twilio_phone_status = "✅ Configurada" if settings.TWILIO_PHONE_NUMBER else "❌ NÃO Configurada"
    
    print(f"🔑 TWILIO_ACCOUNT_SID: {twilio_sid_status}")
    print(f"🔑 TWILIO_AUTH_TOKEN: {twilio_token_status}")
    print(f"🔑 TWILIO_PHONE_NUMBER: {twilio_phone_status}")
    
    if settings.SENDGRID_API_KEY:
        # Mostrar apenas os primeiros 10 caracteres para debug seguro
        masked_key = f"{settings.SENDGRID_API_KEY[:10]}..."
        print(f"🔑 SendGrid Key (parcial): {masked_key}")
    
    if settings.TWILIO_ACCOUNT_SID:
        masked_sid = f"{settings.TWILIO_ACCOUNT_SID[:10]}..."
        print(f"🔑 Twilio SID (parcial): {masked_sid}")