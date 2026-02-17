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
import importlib
import os
from models.tags import tag_metadata
from middleware.tenant_middleware import TenantMiddleware

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
    """,
    version="1.0.0",
    contact={
        "name": "Equipo de Desarrollo GDI",
        "url": "https://github.com/GDI-APGLv3/GDI-Backend",
        "email": "soporte@gdilatam.com",
    },
    license_info={
        "name": "AGPL-3.0",
        "url": "https://www.gnu.org/licenses/agpl-3.0.html",
    },
    openapi_tags=tag_metadata,
    swagger_ui_parameters={
        "persistAuthorization": True,  # Mantener token en Swagger UI entre recargas
    }
)

# Configuración de CORS para permitir acceso desde el frontend
# Orígenes permitidos: localhost (desarrollo) y Railway (producción)
allowed_origins = (
    # Puertos 3000-3050 para localhost y 127.0.0.1
    [f"http://localhost:{port}" for port in range(3000, 3051)] +
    [f"http://127.0.0.1:{port}" for port in range(3000, 3051)] +
    # Puertos 8000-8050 para localhost y 127.0.0.1
    [f"http://localhost:{port}" for port in range(8000, 8051)] +
    [f"http://127.0.0.1:{port}" for port in range(8000, 8051)]
)

# Agregar origen de Railway si está configurado
railway_frontend = os.getenv("FRONTEND_URL")
if railway_frontend:
    allowed_origins.append(railway_frontend)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,  # Orígenes específicos
    allow_credentials=True,  # Permite cookies/credenciales
    allow_methods=["*"],  # Permite todos los métodos HTTP
    allow_headers=["*"],  # Permite todos los headers
)

# Middleware multi-tenant (DESPUÉS de CORS)
# Valida acceso de usuarios a schemas de municipalidades
app.add_middleware(TenantMiddleware)
main_logger.info("TenantMiddleware registrado correctamente")

# Función para cargar dinámicamente todos los endpoints
def include_endpoints(app):
    """Incluye dinámicamente todos los endpoints encontrados en la carpeta endpoints"""
    # Categorías de endpoints a cargar
    endpoint_categories = ['auth', 'documents', 'users', 'system', 'cases', 'sectors', 'dashboard', 'notes']
    
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

# Inicializar pool de conexiones al arrancar
@app.on_event("startup")
async def startup_event():
    """Inicializar recursos al arrancar la aplicación"""
    init_db_pool()
    # Limpiar cache de tenants al iniciar (evita datos obsoletos)
    from shared.tenant_validation import clear_all_cache
    clear_all_cache()
    print("[OK] Backend GDI iniciado correctamente con sistema de expedientes")

# Función de favicon para evitar 404 en navegadores
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Endpoint para manejar solicitudes de favicon de los navegadores"""
    return {"message": "No favicon configured"}

# Iniciar el servidor solo cuando se ejecuta directamente (desarrollo local)
if __name__ == "__main__":
    import uvicorn
    import os

    # Configuración para desarrollo local y Railway
    # Si PORT está seteado (Railway), usar 0.0.0.0 por defecto
    # Si no (local), usar 127.0.0.1
    is_production = os.getenv("PORT") is not None
    default_host = "0.0.0.0" if is_production else "127.0.0.1"

    host = os.getenv("HOST", default_host)
    port = int(os.getenv("PORT", 8000))
    reload = os.getenv("RELOAD", "true" if not is_production else "false").lower() == "true"

    uvicorn.run("main:app", host=host, port=port, reload=reload)