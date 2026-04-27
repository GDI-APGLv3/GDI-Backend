"""
Endpoint para obtener CCOO enviadas (notas + memos unificadas).
Combina notas enviadas desde los sectores del usuario y memos enviados por el usuario.
"""

from fastapi import APIRouter, Depends, Query
from shared.logging import get_logger
from shared.dependencies import get_tenant_schema
from shared.exceptions import exception_to_http_exception, ValidationError
from auth import get_current_user
from models.schemas import AuthenticatedUser
from models.ccoo.responses import CcooSentListResponse
from services.ccoo import get_sent_ccoo
from endpoints.notes.helpers import get_viewable_sector_ids

logger = get_logger("ccoo_sent")

router = APIRouter()


@router.get(
    "/sent",
    response_model=CcooSentListResponse,
    summary="CCOO enviadas",
    description="""Obtiene las comunicaciones oficiales enviadas (Notas + Memos) unificadas con paginacion server-side.

## Descripcion

Lista todas las comunicaciones oficializadas (firmadas) donde:
- CUALQUIERA de los sectores del usuario es el **sender** de una NOTA
- El usuario es el **sender** de un MEMO

## Campos unificados

- **ccoo_type**: 'NOTA' o 'MEMO'
- **recipients_label**: Primer destinatario (acronym sector o full_name)
- **recipients_count**: Total de destinatarios
- **openings_count**: Cantidad de aperturas registradas
"""
)
async def list_sent_ccoo(
    page: int = Query(1, ge=1, description="Numero de pagina"),
    page_size: int = Query(10, ge=1, le=100, description="Elementos por pagina"),
    search: str | None = Query(None, min_length=2, description="Buscar en numero oficial, asunto o contenido"),
    date_filter: str | None = Query(None, description="Filtro predefinido: hoy, ayer, ultimos_7_dias, ultimos_30_dias"),
    date_from: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    date_to: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Lista las CCOO enviadas (notas + memos) unificadas con paginacion real.
    Solo incluye comunicaciones oficializadas (firmadas).
    """
    try:
        viewable_sector_ids = get_viewable_sector_ids(current_user)
        if not viewable_sector_ids:
            raise ValidationError("No se pudo determinar los sectores del usuario")

        logger.info(
            f"Usuario {current_user.user_id} consultando CCOO enviadas desde "
            f"{len(viewable_sector_ids)} sectores"
            f"{' search=' + search if search else ''}"
        )

        result = get_sent_ccoo(
            viewable_sector_ids,
            current_user.user_id,
            schema_name=schema_name,
            page=page,
            page_size=page_size,
            search=search,
            date_filter=date_filter,
            date_from=date_from,
            date_to=date_to
        )

        return CcooSentListResponse(**result)

    except Exception as e:
        logger.error(f"Error al obtener CCOO enviadas: {e}")
        raise exception_to_http_exception(e)
