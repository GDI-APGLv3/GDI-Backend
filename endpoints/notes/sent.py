"""
Endpoint para obtener notas enviadas desde TODOS los sectores del usuario con can_view=true.
"""

from fastapi import APIRouter, Depends, Query
from shared.logging import get_logger
from shared.dependencies import get_tenant_schema
from shared.exceptions import exception_to_http_exception, ValidationError
from auth import get_current_user
from models.schemas import AuthenticatedUser
from models.notes.responses import NoteSentListResponse
from services.notes import get_sent_notes_multi_sector
from .helpers import get_viewable_sector_ids

logger = get_logger("notes_sent")

router = APIRouter()


@router.get(
    "/sent",
    response_model=NoteSentListResponse,
    summary="Notas enviadas",
    description="""Obtiene las notas oficiales enviadas desde TODOS los sectores del usuario con can_view=true.

## Descripcion

Lista todas las NOTAS oficializadas (firmadas) donde CUALQUIERA de los sectores del usuario es el **sender** (remitente).

## Filtrado Automatico

- Solo muestra notas con estado **official** (ya firmadas por todos los firmantes)
- Filtra por **TODOS los sectores** del usuario con can_view=true como sender
- Elimina duplicados

## Campos de Respuesta

Cada nota incluye:
- **document_id**: UUID del documento
- **reference**: Asunto de la nota
- **official_number**: Numero oficial (ej: NOTA-2025-0001234-MUNI)
- **created_at**: Fecha de creacion
- **recipients_count**: Total de destinatarios (TO + CC + BCC)
- **opened_count**: Cuantos recipients han abierto la nota

## Tracking de Aperturas

Como sender, puedes ver:
- Cuantos recipients han abierto la nota
- En el detalle (GET /notes/{id}), se muestra quien abrio y cuando
- Los BCC son visibles solo para ti como sender

## Flujo de Creacion de NOTAS

1. **POST /documents** con recipients (TO, CC, BCC)
2. **PATCH /documents/{id}/save** para contenido y firmantes
3. **POST /documents/{id}/start-signing-process** para enviar a firma
4. **POST /documents/{id}/super-sign** para firmar
5. La nota aparece en esta lista una vez oficializada
"""
)
async def list_sent_notes(
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
    Lista las notas enviadas desde TODOS los sectores del usuario con can_view=true.
    Solo incluye notas oficializadas (firmadas).
    Incluye información de cuántos recipients han abierto la nota.
    """
    try:
        viewable_sector_ids = get_viewable_sector_ids(current_user)
        if not viewable_sector_ids:
            raise ValidationError("No se pudo determinar los sectores del usuario")

        logger.info(
            f"Usuario {current_user.user_id} consultando notas enviadas desde "
            f"{len(viewable_sector_ids)} sectores: {viewable_sector_ids}"
            f"{' search=' + search if search else ''}"
        )

        result = get_sent_notes_multi_sector(
            viewable_sector_ids,
            schema_name=schema_name,
            page=page,
            page_size=page_size,
            search=search,
            date_filter=date_filter,
            date_from=date_from,
            date_to=date_to
        )

        return NoteSentListResponse(**result)

    except Exception as e:
        logger.error(f"Error al obtener notas enviadas: {e}")
        raise exception_to_http_exception(e)
