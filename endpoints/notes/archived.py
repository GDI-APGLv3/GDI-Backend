"""
Endpoint para obtener notas ARCHIVADAS por TODOS los sectores del usuario con can_view=true.
"""

from fastapi import APIRouter, Depends, Query
from shared.logging import get_logger
from shared.dependencies import get_tenant_schema
from shared.exceptions import exception_to_http_exception, ValidationError
from auth import get_current_user
from models.schemas import AuthenticatedUser
from models.notes.responses import NoteArchivedListResponse
from services.notes import get_archived_notes_multi_sector
from .helpers import get_viewable_sector_ids

logger = get_logger("notes_archived")

router = APIRouter()


@router.get(
    "/archived",
    response_model=NoteArchivedListResponse,
    summary="Notas archivadas",
    description="""Obtiene las notas oficiales ARCHIVADAS en TODOS los sectores del usuario con can_view=true.

## Descripcion

Lista todas las NOTAS oficializadas (firmadas) que fueron ARCHIVADAS por el usuario en cualquiera de sus sectores.

## Diferencia con Recibidas

- `/notes/received` muestra notas **NO archivadas** (bandeja de entrada activa)
- `/notes/archived` muestra notas **ARCHIVADAS** (bandeja de archivo)

## Notas

- Una nota archivada puede ser desarchivada desde el detalle
- Al desarchivar, la nota vuelve a aparecer en "Recibidas"
- El archivado es POR SECTOR: si tenés acceso a múltiples sectores y la nota fue enviada a varios, cada sector puede archivarla independientemente
"""
)
async def list_archived_notes(
    page: int = Query(1, ge=1, description="Número de página"),
    page_size: int = Query(20, ge=1, le=100, description="Elementos por página"),
    search: str | None = Query(None, min_length=2, description="Buscar en número oficial, asunto o contenido"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Lista las notas ARCHIVADAS en TODOS los sectores del usuario con can_view=true.
    Solo incluye notas oficializadas (firmadas) que fueron archivadas.
    """
    try:
        viewable_sector_ids = get_viewable_sector_ids(current_user)
        if not viewable_sector_ids:
            raise ValidationError("No se pudo determinar los sectores del usuario")

        logger.info(
            f"Usuario {current_user.user_id} consultando notas archivadas en "
            f"{len(viewable_sector_ids)} sectores: {viewable_sector_ids}"
            f"{' search=' + search if search else ''}"
        )

        result = await get_archived_notes_multi_sector(
            viewable_sector_ids,
            schema_name=schema_name,
            page=page,
            page_size=page_size,
            search=search
        )

        return NoteArchivedListResponse(**result)

    except Exception as e:
        logger.error(f"Error al obtener notas archivadas: {e}")
        raise exception_to_http_exception(e)
