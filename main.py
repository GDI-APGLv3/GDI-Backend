from dotenv import load_dotenv

# Load environment variables from .env file PRIMERO
load_dotenv()

# Configurar logging ANTES de cualquier import que use logging
# Esto garantiza que todos los loggers hereden la configuración con correlation_id
from shared.logging import setup_logging, get_logger
setup_logging()

# Ahora sí importar el resto
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
from psycopg2.extras import RealDictCursor
from database import DATABASE_URL, init_db_pool
from typing import Dict, Any
import asyncio
import importlib
import os
from models.tags import tag_metadata
from middleware.tenant_middleware import TenantMiddleware
from middleware.rate_limit import RateLimitMiddleware

# Logger para el módulo main (usa el formatter con correlation_id)
main_logger = get_logger(__name__)

# Configuración de la aplicación con metadatos
app = FastAPI(
    title="GDI Backend",
    description="""
    API para gestión de documentos institucionales.

    Esta API permite:
    - Gestión de documentos en diferentes estados (borradores y oficiales)
    - Consulta de documentos por usuario con filtros avanzados y paginación
    - Recuperación de metadatos del sistema (estados visuales, tipos de documentos)

    USER: 457c52a4-9305-4e8a-9642-0b9380a4768a
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
        "persistAuthorization": True,  # Mantener token en Swagger UI entre recargas
    }
)

# Configuración de CORS para permitir acceso desde el frontend
# Orígenes permitidos: localhost (desarrollo) y Vercel (producción, vía FRONTEND_URL)
allowed_origins = (
    # Puertos 3000-3050 para localhost y 127.0.0.1
    [f"http://localhost:{port}" for port in range(3000, 3051)] +
    [f"http://127.0.0.1:{port}" for port in range(3000, 3051)] +
    # Puertos 8000-8050 para localhost y 127.0.0.1
    [f"http://localhost:{port}" for port in range(8000, 8051)] +
    [f"http://127.0.0.1:{port}" for port in range(8000, 8051)]
)

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
    allow_headers=["*"],  # Permite todos los headers
)

# Rate limiting por IP (60 req/min) - corta ANTES de TenantMiddleware
app.add_middleware(RateLimitMiddleware)

# Middleware multi-tenant (DESPUÉS de CORS)
# Valida acceso de usuarios a schemas de municipalidades
app.add_middleware(TenantMiddleware)
main_logger.info("TenantMiddleware registrado correctamente")

# Función para cargar dinámicamente todos los endpoints
def include_endpoints(app):
    """Incluye dinámicamente todos los endpoints encontrados en la carpeta endpoints"""
    # Categorías de endpoints a cargar
    endpoint_categories = ['auth', 'documents', 'users', 'system', 'cases', 'sectors', 'dashboard', 'notes', 'memos', 'ccoo', 'rlm', 'search']
    
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


# Inicializar pool de conexiones al arrancar
@app.on_event("startup")
async def startup_event():
    """Inicializar recursos al arrancar la aplicación"""
    init_db_pool()
    # Limpiar cache de tenants al iniciar (evita datos obsoletos)
    from shared.tenant_validation import clear_all_cache
    clear_all_cache()
    main_logger.info("[OK] Backend GDI iniciado correctamente con sistema de expedientes")
    asyncio.create_task(_warm_up_services())

# Función de favicon para evitar 404 en navegadores
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Endpoint para manejar solicitudes de favicon de los navegadores"""
    return {"message": "No favicon configured"}

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