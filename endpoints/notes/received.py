
from fastapi import APIRouter, Depends, Query
from shared.logging import get_logger
from shared.dependencies import get_tenant_schema
from shared.exceptions import exception_to_http_exception, ValidationError
from auth import get_current_user
from models.schemas import AuthenticatedUser
from models.notes.responses import NoteReceivedListResponse
from services.notes import get_received_notes_multi_sector
from .helpers import get_viewable_sector_ids

logger = get_logger("notes_received")

router = APIRouter()


@router.get(
    "/received",
    response_model=NoteReceivedListResponse,
    summary="Notas recibidas",
    description="""Obtiene las notas oficiales recibidas en TODOS los sectores del usuario con can_view=true.

## Descripcion

Lista todas las NOTAS oficializadas (firmadas) donde CUALQUIERA de los sectores del usuario es destinatario (TO, CC o BCC).

## Filtrado Automatico

- Solo muestra notas con estado **official** (ya firmadas por todos los firmantes)
- Filtra por **TODOS los sectores** del usuario con can_view=true
- Incluye notas donde cualquiera de esos sectores es destinatario TO, CC o BCC
- Elimina duplicados (si una nota va a 2 de tus sectores, aparece una sola vez)

## Campos de Respuesta

Cada nota incluye:
- **document_id**: UUID del documento
- **reference**: Asunto de la nota
- **official_number**: Numero oficial (ej: NOTA-2025-0001234-MUNI)
- **sender_sector_name**: Sector que envio la nota
- **recipient_type**: Tipo de destinatario (TO, CC, BCC)
- **created_at**: Fecha de creacion
- **first_opened_at**: Fecha de primera apertura (si aplica)

## Notas

- Las notas BCC solo son visibles para el recipient y el sender (no para otros recipients)
- Al abrir el detalle de una nota (GET /notes/{id}), se registra la apertura automaticamente
"""
)
async def list_received_notes(
    page: int = Query(1, ge=1, description="Número de página"),
    page_size: int = Query(20, ge=1, le=100, description="Elementos por página"),
    search: str | None = Query(None, min_length=2, description="Buscar en número oficial, asunto o contenido"),
    date_filter: str | None = Query(None, description="Filtro predefinido: hoy, ayer, ultimos_7_dias, ultimos_30_dias"),
    date_from: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    date_to: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Lista las notas recibidas en TODOS los sectores del usuario con can_view=true.
    Solo incluye notas oficializadas (firmadas).
    """
    try:
        viewable_sector_ids = get_viewable_sector_ids(current_user)
        if not viewable_sector_ids:
            raise ValidationError("No se pudo determinar los sectores del usuario")

        logger.info(
            f"Usuario {current_user.user_id} consultando notas recibidas en "
            f"{len(viewable_sector_ids)} sectores: {viewable_sector_ids}"
            f"{' search=' + search if search else ''}"
        )

        result = await get_received_notes_multi_sector(
            viewable_sector_ids,
            schema_name=schema_name,
            page=page,
            page_size=page_size,
            search=search,
            date_filter=date_filter,
            date_from=date_from,
            date_to=date_to
        )

        return NoteReceivedListResponse(**result)

    except Exception as e:
        logger.error(f"Error al obtener notas recibidas: {e}")
        raise exception_to_http_exception(e)
