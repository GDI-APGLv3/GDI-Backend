"""
Endpoint para obtener tipos de documentos.
Optimizado siguiendo principios de Clean Code.
"""
from shared.logging import get_logger
from fastapi import APIRouter, Request, Depends
from models.documents.types import DocumentTypesResponse
from services.documents.catalog.types import get_all_document_types
from shared.exceptions import exception_to_http_exception, DatabaseError
from models.tags import Tags
from services.cache import get_cached
from shared.dependencies import get_tenant_schema

# === CONFIGURACIÓN ===
logger = get_logger("get_types")

router = APIRouter(tags=[Tags.DOCUMENTOS])

@router.get(
    "/document-types",
    response_model=DocumentTypesResponse,
    summary="Obtener tipos de documentos",
    description="""Recupera todos los tipos de documentos activos del sistema.

    **Uso en frontend:**
    - **Hook useDocumentTypes**: Carga tipos para selectores y filtros
    - Selector al crear nuevo documento
    - Filtros en tabla de documentos

    **Características:**
    - Cache de 1 hora (los tipos raramente cambian)
    - Solo tipos activos (is_active = true)
    - Ordenados alfabéticamente por nombre

    **Casos de uso:**
    1. Usuario crea documento → selecciona tipo del dropdown
    2. Usuario filtra documentos → usa tipos para filtrar
    """
)
async def get_document_types(
    request: Request,
    schema_name: str = Depends(get_tenant_schema)
) -> DocumentTypesResponse:
    """
    Obtiene todos los tipos de documentos activos.

    Returns:
        DocumentTypesResponse: Lista de tipos de documentos con cache

    Raises:
        HTTPException: Para errores de base de datos
    """
    try:
        logger.info(f"Obteniendo tipos de documentos para schema: {schema_name}")

        # Cache key incluye schema para multi-tenant
        cache_key = f"document_types:{schema_name or 'default'}"

        document_types = await get_cached(
            cache_key=cache_key,
            fetch_func=lambda: get_all_document_types(schema_name),
            ttl=3600
        )

        logger.info(f"Tipos de documentos obtenidos: {len(document_types)} tipos")
        return DocumentTypesResponse(document_types=document_types)

    except DatabaseError as e:
        logger.error(f"Error de base de datos al obtener tipos: {e}")
        raise exception_to_http_exception(e)
