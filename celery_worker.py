#!/usr/bin/env python3
"""
Script para iniciar o worker do Celery
Uso: python celery_worker.py
"""
import sys
import time
from app.core.background_tasks import celery_app
from app.core.logging_config import configure_logging, get_logger

logger = get_logger(__name__)
if __name__ == "__main__":
    # Configurar logging
    configure_logging()
    
    # Reinicia automaticamente caso o worker pare inesperadamente
    while True:
        try:
            celery_app.worker_main([
                "worker",
                "--loglevel=info",
                "--concurrency=4",
                "--pool=solo" if sys.platform == "win32" else "--pool=prefork",
            ])
        except Exception:  # pragma: no cover - log and restart on any failure
            logger.exception("Celery worker crashed; restarting in 5 seconds")
            time.sleep(5)
