from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from contextlib import asynccontextmanager
import time
import uuid

from app.api.endpoints import router
from core.database import init_cache, close_cache, engine, Base
from core.logging_config import configure_logging, get_logger, LogContext
from core.config import settings

# Configurar logging antes de tudo
configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Gerencia o ciclo de vida da aplicação
    """
    logger.info("Starting Torres Project API", 
                version=settings.APP_VERSION,
                environment=settings.ENVIRONMENT)
    
    try:
        # Inicializar cache Redis
        await init_cache()
        logger.info("Redis cache initialized successfully")
        
        # Criar tabelas do banco de dados
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created successfully")
        
        logger.info("Application startup completed")
        yield
        
    except Exception as e:
        logger.error("Failed to start application", error=str(e))
        raise
    finally:
        # Cleanup
        logger.info("Shutting down application")
        await close_cache()
        await engine.dispose()
        logger.info("Application shutdown completed")


# Criar aplicação FastAPI
app = FastAPI(
    title=settings.APP_NAME,
    description="API Backend comercial para cálculos de ICMS com auditoria completa, cache Redis, background tasks e sistema de planos",
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.ENVIRONMENT == "development" else None,
    redoc_url="/redoc" if settings.ENVIRONMENT == "development" else None
)


# ===== MIDDLEWARE DE SEGURANÇA =====

if settings.ENVIRONMENT == "production":
    # HTTPS obrigatório em produção
    from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
    app.add_middleware(HTTPSRedirectMiddleware)
    
    # Apenas domínios confiáveis
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["torresproject.com", "*.torresproject.com", "api.torresproject.com"]
    )


# Middleware de segurança personalizado
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """
    Adiciona headers de segurança a todas as respostas
    """
    response = await call_next(request)
    
    # Headers de segurança obrigatórios
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    
    # CSP para produção
    if settings.ENVIRONMENT == "production":
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        )
    
    return response


# Middleware de logging e correlação
@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    """
    Middleware para logging estruturado de todas as requisições
    """
    # Gerar ID único para a requisição
    request_id = str(uuid.uuid4())
    
    # Extrair informações da requisição
    ip_address = (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or
        request.headers.get("X-Real-IP", "").strip() or
        request.client.host if request.client else "unknown"
    )
    
    user_agent = request.headers.get("User-Agent", "unknown")
    
    # Adicionar contexto estruturado
    with LogContext(
        request_id=request_id,
        method=request.method,
        url=str(request.url),
        ip_address=ip_address,
        user_agent=user_agent[:100]  # Truncar user agent
    ):
        start_time = time.time()
        
        logger.info("Request started",
                   method=request.method,
                   path=request.url.path,
                   query_params=str(request.query_params))
        
        try:
            # Processar requisição
            response = await call_next(request)
            
            # Calcular tempo de processamento
            process_time = time.time() - start_time
            
            # Adicionar headers de correlação
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Process-Time"] = str(round(process_time * 1000, 2))
            
            # Log da resposta
            logger.info("Request completed",
                       status_code=response.status_code,
                       process_time_ms=round(process_time * 1000, 2))
            
            return response
            
        except Exception as e:
            # Log de erro
            process_time = time.time() - start_time
            
            logger.error("Request failed",
                        error=str(e),
                        process_time_ms=round(process_time * 1000, 2))
            
            raise


# Configuração do CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",  # React dev
        "http://localhost:8080",  # Vue dev
        "https://torresproject.com",  # Produção
        "https://*.torresproject.com"  # Subdomínios
    ] if settings.ENVIRONMENT == "production" else ["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# ===== EXCEPTION HANDLERS =====

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Handler customizado para erros de validação
    """
    logger.warning("Validation error",
                  errors=exc.errors(),
                  body=exc.body)
    
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "Validation error",
            "errors": exc.errors(),
            "request_id": request.headers.get("X-Request-ID", "unknown")
        }
    )


@app.exception_handler(500)
async def internal_server_error_handler(request: Request, exc: Exception):
    """
    Handler para erros internos do servidor
    """
    logger.error("Internal server error",
                error=str(exc),
                path=request.url.path)
    
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Internal server error",
            "request_id": request.headers.get("X-Request-ID", "unknown")
        }
    )


# ===== INCLUIR ROTAS =====

app.include_router(router, prefix="/api/v1")


# ===== ENDPOINT RAIZ =====

@app.get("/")
async def root():
    """
    Endpoint raiz com informações básicas da API
    """
    return {
        "message": f"{settings.APP_NAME} is running!",
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "docs_url": "/docs" if settings.ENVIRONMENT == "development" else "Contact admin for API documentation",
        "health_check": "/api/v1/health"
    }


# ===== ENDPOINT PARA MÉTRICAS (PROMETHEUS) =====

@app.get("/metrics")
async def metrics():
    """
    Endpoint para métricas do Prometheus (implementar quando necessário)
    """
    # TODO: Implementar métricas com prometheus_client
    return {"message": "Metrics endpoint - implement prometheus_client integration"}


# Adicionar informações de debug em desenvolvimento
if settings.ENVIRONMENT == "development":
    logger.info("Development mode enabled")
    logger.info("Swagger docs available at: http://localhost:8000/docs")
    logger.info("ReDoc available at: http://localhost:8000/redoc")