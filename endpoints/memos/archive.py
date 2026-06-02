"""
Endpoint para archivar/desarchivar un memo.
A diferencia de NOTAS, no requiere sector_id (archiva por user_id).
"""

from uuid import UUID
from fastapi import APIRouter, Depends, Path
from shared.logging import get_logger
from shared.dependencies import get_tenant_schema
from shared.exceptions import exception_to_http_exception
from auth import get_current_user
from models.schemas import AuthenticatedUser
from models.memos.responses import ArchiveMemoRequest, ArchiveMemoResponse
from services.memos import toggle_memo_archive

logger = get_logger("memos_archive")

router = APIRouter()


@router.patch(
    "/{document_id}/archive",
    response_model=ArchiveMemoResponse,
    summary="Archivar o desarchivar memo",
    description="""Archiva o desarchiva un memo recibido.

## Reglas de Negocio

- Solo **recipients** (TO/CC/BCC) pueden archivar
- El **sender** (emisor) NO puede archivar su propio memo
- El archivado es **por usuario**: cada recipient archiva independientemente

## Request Body

- `archived`: `true` para archivar, `false` para desarchivar

## Errores

- **403**: Si es el sender (no puede archivar)
- **404**: Si el documento no existe o el usuario no es recipient
"""
)
async def archive_memo(
    document_id: UUID = Path(..., description="UUID del documento a archivar"),
    request: ArchiveMemoRequest = ...,
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Archiva o desarchiva un memo para el usuario actual.
    """
    try:
        action = "archivar" if request.archived else "desarchivar"
        logger.info(
            f"Usuario {current_user.user_id} intentando {action} memo {document_id}"
        )

        result = await toggle_memo_archive(
            document_id=str(document_id),
            user_id=current_user.user_id,
            archived=request.archived,
            schema_name=schema_name
        )

        action_past = "archivado" if request.archived else "desarchivado"
        logger.info(
            f"Memo {document_id} {action_past} exitosamente para usuario {current_user.user_id}"
        )

        return ArchiveMemoResponse(**result)

    except Exception as e:
        logger.error(f"Error al archivar/desarchivar memo {document_id}: {e}")
        raise exception_to_http_exception(e)
