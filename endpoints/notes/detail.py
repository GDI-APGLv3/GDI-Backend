"""
Endpoint para obtener el detalle de una nota.
Registra la apertura si el usuario es recipient (no sender) Y tiene can_edit.
Usuarios con solo can_view pueden ver pero NO registran apertura.
"""

from fastapi import APIRouter, Depends, Path
from shared.logging import get_logger
from shared.dependencies import get_tenant_schema
from shared.exceptions import exception_to_http_exception, ValidationError
from auth import get_current_user
from models.schemas import AuthenticatedUser
from models.notes.responses import NoteDetailResponse
from services.notes import get_note_detail_multi_sector
from .helpers import get_user_permissions_for_notes

logger = get_logger("notes_detail")

router = APIRouter()


@router.get(
    "/{document_id}",
    response_model=NoteDetailResponse,
    summary="Detalle de nota",
    description="""Obtiene el detalle completo de una nota oficial.

Verifica acceso en TODOS los sectores del usuario con can_view=true.
Si el usuario tiene can_edit en el sector con acceso, registra la apertura.
Si solo tiene can_view (sin can_edit), ve la nota pero NO registra apertura.
Si el usuario es sender, muestra las aperturas de todos los recipients.
"""
)
async def get_note(
    document_id: str = Path(..., description="UUID del documento"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Obtiene el detalle de una nota y registra la apertura si corresponde.

    Seguridad:
    - Verifica acceso en TODOS los sectores con can_view=true
    - Solo el sender y los recipients pueden ver la nota
    - BCC solo es visible para el sender
    - Las aperturas solo son visibles para el sender
    - Solo se registra apertura si el usuario tiene can_edit en el sector
    """
    try:
        user_permissions = get_user_permissions_for_notes(current_user)
        if not user_permissions:
            raise ValidationError("No se pudo determinar los permisos del usuario")

        viewable_count = sum(1 for p in user_permissions if p.get('can_view'))
        if viewable_count == 0:
            raise ValidationError("No tienes sectores con permiso de visualización")

        logger.info(
            f"Usuario {current_user.user_id} accediendo a nota {document_id} "
            f"(tiene {viewable_count} sectores viewable)"
        )

        result = get_note_detail_multi_sector(
            document_id=document_id,
            requesting_user_id=current_user.user_id,
            user_permissions=user_permissions,
            schema_name=schema_name
        )

        return NoteDetailResponse(**result)

    except Exception as e:
        logger.error(f"Error al obtener detalle de nota {document_id}: {e}")
        raise exception_to_http_exception(e)
