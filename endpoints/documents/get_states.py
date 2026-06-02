"""
Endpoint para obtener estados visuales de documentos.
Optimizado siguiendo principios de Clean Code.
"""
from shared.logging import get_logger
from fastapi import APIRouter, Depends, Request
from models.documents.states import DocumentStatesResponse
from services.documents.catalog.states import get_all_display_states
from shared.exceptions import exception_to_http_exception, DatabaseError
from models.tags import Tags
from services.cache import get_cached
from auth import get_current_user
from shared.dependencies import get_tenant_schema

# === CONFIGURACIÓN ===
logger = get_logger("get_states")

router = APIRouter(tags=[Tags.DOCUMENTOS])

@router.get(
    "/document-states",
    response_model=DocumentStatesResponse,
    summary="Obtener estados de documentos",
    description="""Recupera todos los estados visuales disponibles para documentos.
    
    **Uso en frontend:**
    - **Filtros desplegables en tabla de documentos** (uso principal)
    - Selectores de estado en formularios
    - Badges/chips de estado en cards
    
    **Estados disponibles:**
    - En edición (draft, editing)
    - En proceso de firma (pending_signature, signing_process)
    - Firmar ahora (sent_to_sign, sign_now)
    - Firmado (signed, completed)
    - Rechazado (rejected)
    
    **Casos de uso:**
    1. Usuario abre tabla de documentos → carga filtro de estados desde este endpoint
    2. Usuario selecciona estado del dropdown → filtra documentos por ese estado
    """,
    dependencies=[Depends(get_current_user)]
)
async def get_document_states(
    request: Request,
    schema_name: str = Depends(get_tenant_schema)
) -> DocumentStatesResponse:
    """
    Obtiene todos los estados visuales disponibles para documentos.

    Returns:
        DocumentStatesResponse: Lista de estados con cache

    Raises:
        HTTPException: Para errores de base de datos
    """
    try:
        logger.info("Obteniendo estados de documentos desde cache")

        states = await get_cached(
            cache_key=f"document_states:all:{schema_name}",
            fetch_func=lambda: get_all_display_states(schema_name=schema_name),
            ttl=3600  # 1 hora - los estados raramente cambian
        )

        logger.info(f"Estados de documentos obtenidos: {len(states)} estados")
        return DocumentStatesResponse(display_states=states)

    except DatabaseError as e:
        logger.error(f"Error de base de datos al obtener estados: {e}")
        raise exception_to_http_exception(e)