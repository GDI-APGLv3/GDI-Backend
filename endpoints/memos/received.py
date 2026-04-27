"""
Endpoint para obtener memos recibidos por el usuario.
A diferencia de NOTAS, no usa sector_ids sino user_id directo.
"""

from fastapi import APIRouter, Depends, Query
from shared.logging import get_logger
from shared.dependencies import get_tenant_schema
from shared.exceptions import exception_to_http_exception
from auth import get_current_user
from models.schemas import AuthenticatedUser
from models.memos.responses import MemoReceivedListResponse
from services.memos import get_received_memos

logger = get_logger("memos_received")

router = APIRouter()


@router.get(
    "/received",
    response_model=MemoReceivedListResponse,
    summary="Memos recibidos",
    description="""Obtiene los memos oficiales recibidos por el usuario.

## Descripcion

Lista todos los MEMOS oficializados (firmados) donde el usuario es destinatario (TO, CC o BCC).

## Filtrado Automatico

- Solo muestra memos con estado **official** (ya firmados por todos los firmantes)
- Filtra por **user_id** del usuario actual
- Incluye memos donde el usuario es destinatario TO, CC o BCC
- Las notas BCC solo son visibles para el recipient y el sender

## Campos de Respuesta

Cada memo incluye:
- **document_id**: UUID del documento
- **reference**: Asunto del memo
- **official_number**: Numero oficial (ej: MEMO-2025-0001234-MUNI)
- **sender**: Informacion del usuario emisor (nombre, sector)
- **recipient_type**: Tipo de destinatario (TO, CC, BCC)
- **read_status**: Estado de lectura (opened_at)
"""
)
async def list_received_memos(
    page: int = Query(1, ge=1, description="Numero de pagina"),
    page_size: int = Query(20, ge=1, le=100, description="Elementos por pagina"),
    search: str | None = Query(None, min_length=2, description="Buscar en numero oficial, asunto o contenido"),
    date_filter: str | None = Query(None, description="Filtro predefinido: hoy, ayer, ultimos_7_dias, ultimos_30_dias"),
    date_from: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    date_to: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Lista los memos recibidos por el usuario actual.
    Solo incluye memos oficializados (firmados).
    """
    try:
        logger.info(
            f"Usuario {current_user.user_id} consultando memos recibidos"
            f"{' search=' + search if search else ''}"
        )

        result = get_received_memos(
            current_user.user_id,
            schema_name=schema_name,
            page=page,
            page_size=page_size,
            search=search,
            date_filter=date_filter,
            date_from=date_from,
            date_to=date_to
        )

        return MemoReceivedListResponse(**result)

    except Exception as e:
        logger.error(f"Error al obtener memos recibidos: {e}")
        raise exception_to_http_exception(e)
