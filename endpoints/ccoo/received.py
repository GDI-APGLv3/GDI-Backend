"""
Endpoint para obtener CCOO recibidas (notas + memos unificadas).
Combina notas de los sectores del usuario y memos dirigidos al usuario.
"""

from fastapi import APIRouter, Depends, Query
from shared.logging import get_logger
from shared.dependencies import get_tenant_schema
from shared.exceptions import exception_to_http_exception, ValidationError
from auth import get_current_user
from models.schemas import AuthenticatedUser
from models.ccoo.responses import CcooReceivedListResponse
from services.ccoo import get_received_ccoo
from endpoints.notes.helpers import get_viewable_sector_ids

logger = get_logger("ccoo_received")

router = APIRouter()


@router.get(
    "/received",
    response_model=CcooReceivedListResponse,
    summary="CCOO recibidas",
    description="""Obtiene las comunicaciones oficiales recibidas (Notas + Memos) unificadas con paginacion server-side.

## Descripcion

Lista todas las comunicaciones oficializadas (firmadas) donde:
- CUALQUIERA de los sectores del usuario es destinatario de una **NOTA** (TO, CC o BCC)
- El usuario es destinatario directo de un **MEMO** (TO, CC o BCC)

Los resultados se mezclan y ordenan por fecha de firma (signed_at DESC).

## Campos unificados

- **ccoo_type**: 'NOTA' o 'MEMO' para diferenciar el tipo
- **sender.type**: 'sector' (NOTA) o 'user' (MEMO) para diferenciar rendering
- **read_status**: Estado de lectura unificado

## Filtros disponibles

- **search**: Busca en numero oficial, asunto y contenido (ILIKE + similarity)
- **date_filter**: Filtros predefinidos (hoy, ayer, ultimos_7_dias, ultimos_30_dias)
- **date_from/date_to**: Rango de fechas personalizado
"""
)
async def list_received_ccoo(
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
    Lista las CCOO recibidas (notas + memos) unificadas con paginacion real.
    Solo incluye comunicaciones oficializadas (firmadas) NO archivadas.
    """
    try:
        viewable_sector_ids = get_viewable_sector_ids(current_user)
        if not viewable_sector_ids:
            raise ValidationError("No se pudo determinar los sectores del usuario")

        logger.info(
            f"Usuario {current_user.user_id} consultando CCOO recibidas en "
            f"{len(viewable_sector_ids)} sectores"
            f"{' search=' + search if search else ''}"
        )

        result = await get_received_ccoo(
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

        return CcooReceivedListResponse(**result)

    except Exception as e:
        logger.error(f"Error al obtener CCOO recibidas: {e}")
        raise exception_to_http_exception(e)
