"""
Endpoint para obtener memos enviados por el usuario.
"""

from fastapi import APIRouter, Depends, Query
from shared.logging import get_logger
from shared.dependencies import get_tenant_schema
from shared.exceptions import exception_to_http_exception
from auth import get_current_user
from models.schemas import AuthenticatedUser
from models.memos.responses import MemoSentListResponse
from services.memos import get_sent_memos

logger = get_logger("memos_sent")

router = APIRouter()


@router.get(
    "/sent",
    response_model=MemoSentListResponse,
    summary="Memos enviados",
    description="""Obtiene los memos oficiales enviados por el usuario.

## Descripcion

Lista todos los MEMOS oficializados (firmados) donde el usuario es el **sender** (remitente).

## Campos de Respuesta

Cada memo incluye:
- **document_id**: UUID del documento
- **reference**: Asunto del memo
- **official_number**: Numero oficial
- **recipients**: Lista de destinatarios
- **openings_count**: Cuantos recipients han abierto el memo
"""
)
async def list_sent_memos(
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
    Lista los memos enviados por el usuario actual.
    Solo incluye memos oficializados (firmados).
    """
    try:
        logger.info(
            f"Usuario {current_user.user_id} consultando memos enviados"
            f"{' search=' + search if search else ''}"
        )

        result = await get_sent_memos(
            current_user.user_id,
            schema_name=schema_name,
            page=page,
            page_size=page_size,
            search=search,
            date_filter=date_filter,
            date_from=date_from,
            date_to=date_to
        )

        return MemoSentListResponse(**result)

    except Exception as e:
        logger.error(f"Error al obtener memos enviados: {e}")
        raise exception_to_http_exception(e)
