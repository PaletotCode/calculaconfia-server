#!/usr/bin/env python3
"""
Script para iniciar o worker do Celery
Uso: python celery_worker.py
"""

from app.core.background_tasks import celery_app
from app.core.logging_config import configure_logging

if __name__ == "__main__":
    # Configurar logging
    configure_logging()
    
    # Iniciar worker do Celery
    celery_app.worker_main([
        "worker",
        "--loglevel=info",
        "--concurrency=4",
        "--pool=solo" if __import__("sys").platform == "win32" else "--pool=prefork"
    ])
