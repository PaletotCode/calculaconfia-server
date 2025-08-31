import logging
import structlog
import sys
from typing import Any, Dict
from pythonjsonlogger import jsonlogger

from .config import settings


def configure_logging():
    """
    Configuração de logging profissional com estrutlog
    """
    
    # Processadores do structlog
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="ISO"),
        structlog.processors.CallsiteParameterAdder(
            parameters=[
                structlog.processors.CallsiteParameter.FUNC_NAME,
                structlog.processors.CallsiteParameter.LINENO,
            ]
        ),
    ]
    
    if settings.LOG_FORMAT == "json":
        # Formato JSON para produção
        processors.append(structlog.processors.JSONRenderer())
    else:
        # Formato colorido para desenvolvimento  
        processors.extend([
            structlog.dev.ConsoleRenderer(colors=True),
        ])
    
    # Configurar structlog
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.LOG_LEVEL.upper())
        ),
        logger_factory=structlog.WriteLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    
    # Configurar logging padrão
    if settings.LOG_FORMAT == "json":
        formatter = jsonlogger.JsonFormatter(
            fmt='%(asctime)s %(name)s %(levelname)s %(message)s'
        )
    else:
        formatter = logging.Formatter(
            fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
    
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    
    # Configurar loggers
    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper()))
    
    # Reduzir verbosidade de bibliotecas externas
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str = None):
    """
    Retorna um logger estruturado
    """
    return structlog.get_logger(name)


# Context manager para adicionar contexto aos logs
class LogContext:
    def __init__(self, **context):
        self.context = context
        
    def __enter__(self):
        structlog.contextvars.bind_contextvars(**self.context)
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        structlog.contextvars.unbind_contextvars(*self.context.keys())