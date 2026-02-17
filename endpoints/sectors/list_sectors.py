"""
Endpoint para obtener todos los sectores con sus departamentos.
Optimizado siguiendo principios de Clean Code.
"""
from shared.logging import get_logger
from fastapi import APIRouter, Depends
from typing import Dict, Any
from auth import get_current_user
from models.schemas import AuthenticatedUser
from services.sector_service import SectorService
from services.cache import get_cached
from shared.exceptions import exception_to_http_exception, DatabaseError
from shared.dependencies import get_tenant_schema

# === CONFIGURACIÓN ===
logger = get_logger("list_sectors")

router = APIRouter()

@router.get("/")
async def get_all_sectors(
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
) -> Dict[str, Any]:
    """
    Obtener todos los sectores activos con sus respectivos departamentos.
    Solo devuelve los acrónimos de sector y departamento.

    Args:
        current_user: Usuario autenticado
        schema_name: Nombre del schema del tenant

    Returns:
        Dict con lista de sectores:
        {
            "sectors": [
                {
                    "sector_acronym": "SECT",
                    "department_acronym": "DEPT"
                }
            ],
            "total": 100
        }

    Raises:
        HTTPException 500: Si hay error interno del servidor
    """
    try:
        logger.info("Obteniendo sectores y departamentos desde cache")

        # Obtener sectores desde cache (TTL: 5 minutos)
        # Si Redis no está disponible, ejecuta directamente sin cache
        result = get_cached(
            cache_key=f"sectors:all:{schema_name}",
            fetch_func=lambda: SectorService().get_all_sectors_with_departments(schema_name=schema_name),
            ttl=300  # 5 minutos
        )

        logger.info(f"Sectores obtenidos: {result.get('total', 0)} sectores")
        return result

    except DatabaseError as e:
        logger.error(f"Error de base de datos al obtener sectores: {e}")
        raise exception_to_http_exception(e)

    except Exception as e:
        logger.error(f"Error inesperado al obtener sectores: {e}")
        raise exception_to_http_exception(e)