"""
Endpoint para archivar/desarchivar una nota.
"""

from fastapi import APIRouter, Depends, Path
from shared.logging import get_logger
from shared.dependencies import get_tenant_schema
from shared.exceptions import exception_to_http_exception, ValidationError, AuthorizationError
from auth import get_current_user
from models.schemas import AuthenticatedUser
from models.notes.responses import ArchiveNoteRequest, ArchiveNoteResponse
from services.notes import toggle_note_archive
from .helpers import get_editable_sector_ids

logger = get_logger("notes_archive")

router = APIRouter()


@router.patch(
    "/{document_id}/archive",
    response_model=ArchiveNoteResponse,
    summary="Archivar o desarchivar nota",
    description="""Archiva o desarchiva una nota recibida.

## Descripcion

Permite archivar una nota para sacarla de la bandeja de entrada (Recibidas) y moverla a la bandeja de archivo (Archivadas). También permite desarchivar para volver a mostrarla en Recibidas.

## Reglas de Negocio

- Solo **recipients** (TO/CC/BCC) pueden archivar
- El **sender** (emisor) NO puede archivar su propia nota
- El archivado es **por sector**: si la nota fue enviada a múltiples sectores del usuario, debe archivar en cada uno individualmente
- Se requiere **can_edit** en el sector especificado

## Request Body

- `archived`: `true` para archivar, `false` para desarchivar
- `sector_id`: UUID del sector desde el cual se archiva (debe ser un sector donde el usuario tenga can_edit y sea recipient)

## Errores

- **403**: Si el sector no tiene can_edit o es el sender
- **404**: Si el documento no existe o el sector no es recipient
"""
)
async def archive_note(
    document_id: str = Path(..., description="UUID del documento a archivar"),
    request: ArchiveNoteRequest = ...,
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Archiva o desarchiva una nota para un sector específico.
    """
    try:
        # Validar que el sector_id esté en los sectores del usuario con can_edit
        editable_sector_ids = get_editable_sector_ids(current_user)

        if request.sector_id not in editable_sector_ids:
            raise AuthorizationError(
                f"No tienes permiso de edición en el sector {request.sector_id}. "
                "Solo puedes archivar notas en sectores donde tengas can_edit=true."
            )

        action = "archivar" if request.archived else "desarchivar"
        logger.info(
            f"Usuario {current_user.user_id} intentando {action} nota {document_id} "
            f"en sector {request.sector_id}"
        )

        result = await toggle_note_archive(
            document_id=document_id,
            sector_id=request.sector_id,
            archived=request.archived,
            schema_name=schema_name
        )

        action_past = "archivada" if request.archived else "desarchivada"
        logger.info(
            f"Nota {document_id} {action_past} exitosamente para sector {request.sector_id}"
        )

        return ArchiveNoteResponse(**result)

    except Exception as e:
        logger.error(f"Error al archivar/desarchivar nota {document_id}: {e}", exc_info=True)
        raise exception_to_http_exception(e)
