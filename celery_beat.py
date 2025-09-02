#!/usr/bin/env python3
"""
Script para iniciar o Celery Beat (scheduler)
Uso: python celery_beat.py
"""

from app.core.background_tasks import celery_app
from app.core.logging_config import configure_logging

if __name__ == "__main__":
    # Configurar logging
    configure_logging()
    
    # Iniciar beat scheduler do Celery
    celery_app.start([
        "-A", "app.core.background_tasks",
        "beat",
        "--loglevel=info",
        "--schedule=/tmp/celerybeat-schedule",
        "--pidfile=/tmp/celerybeat.pid"
    ])
