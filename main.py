from dotenv import load_dotenv

# Load environment variables from .env file PRIMERO
load_dotenv()

# Configurar logging ANTES de cualquier import que use logging
# Esto garantiza que todos los loggers hereden la configuración con correlation_id
from shared.logging import setup_logging, get_logger
setup_logging()

# Ahora sí importar el resto
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import asyncpg
from database import DATABASE_URL, init_pool, close_pool
from typing import Dict, Any
import asyncio
import importlib
import os
from models.tags import tag_metadata
from middleware.tenant_middleware import TenantMiddleware
from middleware.rate_limit import RateLimitMiddleware
from middleware.host_filter import HostFilterMiddleware

# Logger para el módulo main (usa el formatter con correlation_id)
main_logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestiona init/cierre del pool asyncpg y servicios de background."""
    await init_pool()
    from shared.tenant_validation import clear_all_cache
    clear_all_cache()
    main_logger.info("[OK] Pool asyncpg inicializado")

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from jobs.orphan_inprocess import schedule_orphan_reclaim
    scheduler = AsyncIOScheduler()
    schedule_orphan_reclaim(scheduler)
    scheduler.start()
    app.state.scheduler = scheduler
    main_logger.info("[OK] APScheduler iniciado - orphan_reclaim cada 300s")

    asyncio.create_task(_warm_up_services())
    main_logger.info("[OK] Backend GDI iniciado correctamente con sistema de expedientes")

    yield

    if hasattr(app.state, "scheduler"):
        app.state.scheduler.shutdown(wait=False)
        main_logger.info("[OK] APScheduler detenido correctamente")
    await close_pool()
    main_logger.info("[OK] Pool asyncpg cerrado")


# MEDIA-15: Ocultar docs en producción — solo disponibles en desarrollo/testing
_ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
_is_production = _ENVIRONMENT == "production"

# Configuración de la aplicación con metadatos
app = FastAPI(
    title="GDI Backend",
    description="""
    API para gestión de documentos institucionales.

    Esta API permite:
    - Gestión de documentos en diferentes estados (borradores y oficiales)
    - Consulta de documentos por usuario con filtros avanzados y paginación
    - Recuperación de metadatos del sistema (estados visuales, tipos de documentos)
    """,
    version="1.0.0",
    contact={
        "name": "Equipo de Desarrollo GDI",
        "url": "https://municipalidad-ejemplo.cl/soporte",
        "email": "soporte@municipalidad-ejemplo.cl",
    },
    license_info={
        "name": "Licencia Propietaria",
        "url": "https://municipalidad-ejemplo.cl/licencia",
    },
    openapi_tags=tag_metadata,
    swagger_ui_parameters={
        "persistAuthorization": True,
    },
    lifespan=lifespan,
    # MEDIA-15: Deshabilitar docs en producción para no exponer la API sin auth
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
)

# Configuración de CORS para permitir acceso desde el frontend
# Orígenes permitidos: localhost (desarrollo) y Vercel (producción, vía FRONTEND_URL)
allowed_origins = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
]

# Agregar origenes de produccion (soporta multiples URLs separadas por coma)
frontend_urls = os.getenv("FRONTEND_URL", "")
for url in frontend_urls.split(","):
    url = url.strip()
    if url:
        allowed_origins.append(url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,  # Orígenes específicos
    allow_credentials=True,  # Permite cookies/credenciales
    allow_methods=["*"],  # Permite todos los métodos HTTP
    # MEDIA-16: lista explícita de headers permitidos (no wildcard)
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "Origin",
        "X-Requested-With",
        "X-User-ID",
        "X-Tenant-Schema",
        "X-API-Key",
    ],
)

# Rate limiting por IP (60 req/min) - corta ANTES de TenantMiddleware
app.add_middleware(RateLimitMiddleware)

# Middleware multi-tenant (DESPUÉS de CORS)
# Valida acceso de usuarios a schemas de municipalidades
app.add_middleware(TenantMiddleware)
main_logger.info("TenantMiddleware registrado correctamente")

# Host-based filter: subdominios dedicados (enlace.your-domain.com) solo
# responden a paths whitelisted; el resto -> 404. Se registra ULTIMO
# para que ejecute PRIMERO en el request (Starlette aplica middlewares
# en orden inverso al registro).
app.add_middleware(HostFilterMiddleware)
main_logger.info("HostFilterMiddleware registrado correctamente")

# ---------------------------------------------------------------------------
# Handlers globales de excepciones
# Garantizan que NUNCA llegue al cliente un mensaje técnico de BD o stack.
# ---------------------------------------------------------------------------

@app.exception_handler(asyncpg.PostgresError)
async def asyncpg_error_handler(request: Request, exc: asyncpg.PostgresError) -> JSONResponse:
    """Intercepta excepciones asyncpg que lleguen sin capturar."""
    main_logger.error(
        f"[DB ERROR] {type(exc).__name__} en {request.method} {request.url.path}: {exc}",
        exc_info=True,
    )
    if isinstance(exc, asyncpg.ForeignKeyViolationError):
        return JSONResponse(
            status_code=409,
            content={"detail": "No se puede realizar esta operación porque el registro está vinculado a otros datos"},
        )
    if isinstance(exc, asyncpg.UniqueViolationError):
        return JSONResponse(
            status_code=409,
            content={"detail": "Ya existe un registro con esos datos"},
        )
    if isinstance(exc, asyncpg.IntegrityConstraintViolationError):
        return JSONResponse(
            status_code=409,
            content={"detail": "No se puede realizar esta operación por restricciones de integridad"},
        )
    return JSONResponse(
        status_code=500,
        content={"detail": "Error al procesar la solicitud"},
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Captura cualquier excepción no controlada y evita que llegue al cliente como mensaje técnico."""
    # HTTPException ya tiene su propio handler en FastAPI — no la interceptamos aquí
    if isinstance(exc, HTTPException):
        raise exc

    main_logger.error(
        f"[UNHANDLED] {type(exc).__name__} en {request.method} {request.url.path}: {exc}",
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Error interno del servidor"},
    )


# Función para cargar dinámicamente todos los endpoints
def include_endpoints(app):
    """Incluye dinámicamente todos los endpoints encontrados en la carpeta endpoints"""
    # Categorías de endpoints a cargar
    endpoint_categories = ['auth', 'documents', 'users', 'system', 'cases', 'sectors', 'dashboard', 'notes', 'memos', 'ccoo', 'rlm', 'search', 'digital_signature']
    
    # Recorrer cada categoría
    for category in endpoint_categories:
        category_path = f'endpoints.{category}'
        category_dir = f'endpoints/{category}'
        
        # Verificar que el directorio exista
        if not os.path.isdir(category_dir):
            continue
        
        # Manejo especial para categorías con router principal
        if category == 'documents':
            # Cargar el router principal de documentos (orden lógico)
            router_module = importlib.import_module(f"{category_path}.router")
            if hasattr(router_module, 'documents_router'):
                app.include_router(router_module.documents_router)
        elif category == 'cases':
            # Cargar el router principal de casos
            router_module = importlib.import_module(f"{category_path}.router")
            if hasattr(router_module, 'cases_router'):
                app.include_router(router_module.cases_router)
        elif category == 'sectors':
            # Cargar el router principal de sectores
            router_module = importlib.import_module(f"{category_path}.router")
            if hasattr(router_module, 'router'):
                app.include_router(router_module.router)
        elif category == 'dashboard':
            # Cargar el router principal de dashboard
            router_module = importlib.import_module(f"{category_path}.router")
            if hasattr(router_module, 'dashboard_router'):
                app.include_router(router_module.dashboard_router)
        elif category == 'notes':
            # Cargar el router principal de notas
            router_module = importlib.import_module(f"{category_path}.router")
            if hasattr(router_module, 'router'):
                app.include_router(router_module.router)
        elif category == 'memos':
            # Cargar el router principal de memos
            router_module = importlib.import_module(f"{category_path}.router")
            if hasattr(router_module, 'router'):
                app.include_router(router_module.router)
        elif category == 'ccoo':
            # Cargar el router principal de CCOO (comunicaciones oficiales)
            router_module = importlib.import_module(f"{category_path}.router")
            if hasattr(router_module, 'router'):
                app.include_router(router_module.router)
        elif category == 'rlm':
            # Cargar el router principal de RLM (legajos)
            router_module = importlib.import_module(f"{category_path}.router")
            if hasattr(router_module, 'rlm_router'):
                app.include_router(router_module.rlm_router)
        elif category == 'search':
            router_module = importlib.import_module(f"{category_path}.router")
            if hasattr(router_module, 'router'):
                app.include_router(router_module.router)
        elif category == 'digital_signature':
            router_module = importlib.import_module(f"{category_path}.router")
            if hasattr(router_module, 'digital_signature_router'):
                app.include_router(router_module.digital_signature_router)
        else:
            # Cargar todos los módulos .py de otras categorías
            for file in os.listdir(category_dir):
                if file.endswith('.py') and file != '__init__.py' and file != 'router.py':
                    module_name = f"{category_path}.{file[:-3]}"
                    module = importlib.import_module(module_name)
                    if hasattr(module, 'router'):
                        app.include_router(module.router)

# Incluir todos los endpoints
include_endpoints(app)

def _to_public_url(internal_url: str | None) -> str | None:
    """Convierte URL .internal de Fly.io a URL publica .fly.dev.

    Necesario porque las requests por .internal NO despiertan maquinas
    dormidas, solo las requests por la URL publica las despiertan.

    Ejemplo:
        http://<your-app>-agentelang-prd.internal:8080
        -> https://<your-app>-agentelang-prd.fly.dev
    """
    if not internal_url:
        return None
    try:
        host = internal_url.split("//", 1)[1]
        app_name = host.split(".internal", 1)[0]
        return f"https://{app_name}.fly.dev"
    except (IndexError, AttributeError):
        return None


async def _warm_up_services():
    """Despierta microservicios en Fly.io via URL publica.

    Cuando este backend arranca (cold start gatillado por Vercel),
    pinguea por URL publica a los servicios que lo rodean para que
    Fly.io los despierte tambien. Asi todo el ambiente queda caliente
    en ~5-10 segundos. Best-effort, no bloquea el startup.
    """
    import httpx

    # Convertimos las URLs .internal a publicas (.fly.dev) para que
    # los pings despierten servicios dormidos.
    services = {
        "PDFComposer": _to_public_url(os.getenv("PDFCOMPOSER_URL")),
        "Notary":      _to_public_url(os.getenv("NOTARY_URL")),
        "AgenteLANG":  _to_public_url(os.getenv("AGENT_URL")),
        "Gateway":     _to_public_url(os.getenv("GATEWAY_URL")),
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        for name, url in services.items():
            if not url:
                continue
            try:
                resp = await client.get(f"{url}/health")
                main_logger.info(f"[WARM-UP] {name}: {resp.status_code}")
            except Exception as e:
                main_logger.warning(f"[WARM-UP] {name}: no responde ({e.__class__.__name__})")


# Función de favicon para evitar 404 en navegadores
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Endpoint para manejar solicitudes de favicon de los navegadores"""
    return {"message": "No favicon configured"}


# Health check publico (sin auth, sin tenant). Lo usa Fly.io para
# monitoring + Let's Encrypt para validar el subdominio enlace.your-domain.com
# (HostFilterMiddleware lo whitelistea).
@app.get("/health", include_in_schema=False)
async def health_check():
    return {"status": "ok"}

# Iniciar el servidor solo cuando se ejecuta directamente (desarrollo local)
if __name__ == "__main__":
    import uvicorn
    import os

    # Configuración para desarrollo local y Fly.io
    # Si PORT está seteado (Fly.io), usar 0.0.0.0 por defecto
    # Si no (local), usar 127.0.0.1
    is_production = os.getenv("PORT") is not None
    default_host = "0.0.0.0" if is_production else "127.0.0.1"

    host = os.getenv("HOST", default_host)
    port = int(os.getenv("PORT", 8000))
    reload = os.getenv("RELOAD", "true" if not is_production else "false").lower() == "true"

    uvicorn.run("main:app", host=host, port=port, reload=reload)