"""
Endpoint para obtener CCOO archivadas (notas + memos unificadas).
"""

from fastapi import APIRouter, Depends, Query
from shared.logging import get_logger
from shared.dependencies import get_tenant_schema
from shared.exceptions import exception_to_http_exception, ValidationError
from auth import get_current_user
from models.schemas import AuthenticatedUser
from models.ccoo.responses import CcooArchivedListResponse
from services.ccoo import get_archived_ccoo
from endpoints.notes.helpers import get_viewable_sector_ids

logger = get_logger("ccoo_archived")

router = APIRouter()


@router.get(
    "/archived",
    response_model=CcooArchivedListResponse,
    summary="CCOO archivadas",
    description="""Obtiene las comunicaciones oficiales ARCHIVADAS (Notas + Memos) unificadas con paginacion server-side.

## Descripcion

Lista todas las comunicaciones oficializadas (firmadas) que fueron ARCHIVADAS:
- Notas archivadas en cualquiera de los sectores del usuario
- Memos archivados por el usuario

Ordenadas por fecha de archivado (archived_at DESC).

## Diferencia con Recibidas

- `/ccoo/received` muestra comunicaciones **NO archivadas** (bandeja activa)
- `/ccoo/archived` muestra comunicaciones **ARCHIVADAS** (bandeja de archivo)
"""
)
async def list_archived_ccoo(
    page: int = Query(1, ge=1, description="Numero de pagina"),
    page_size: int = Query(10, ge=1, le=100, description="Elementos por pagina"),
    search: str | None = Query(None, min_length=2, description="Buscar en numero oficial, asunto o contenido"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Lista las CCOO archivadas (notas + memos) unificadas con paginacion real.
    Solo incluye comunicaciones oficializadas (firmadas) que fueron archivadas.
    """
    try:
        viewable_sector_ids = get_viewable_sector_ids(current_user)
        if not viewable_sector_ids:
            raise ValidationError("No se pudo determinar los sectores del usuario")

        logger.info(
            f"Usuario {current_user.user_id} consultando CCOO archivadas en "
            f"{len(viewable_sector_ids)} sectores"
            f"{' search=' + search if search else ''}"
        )

        result = get_archived_ccoo(
            viewable_sector_ids,
            current_user.user_id,
            schema_name=schema_name,
            page=page,
            page_size=page_size,
            search=search
        )

        return CcooArchivedListResponse(**result)

    except Exception as e:
        logger.error(f"Error al obtener CCOO archivadas: {e}")
        raise exception_to_http_exception(e)
