"""
Endpoint para obtener el detalle de un memo.
Registra la apertura automaticamente si el usuario es recipient.
"""

from uuid import UUID
from fastapi import APIRouter, Depends, Path
from shared.logging import get_logger
from shared.dependencies import get_tenant_schema
from shared.exceptions import exception_to_http_exception
from auth import get_current_user
from models.schemas import AuthenticatedUser
from models.memos.responses import MemoDetailResponse
from services.memos import get_memo_detail

logger = get_logger("memos_detail")

router = APIRouter()


@router.get(
    "/{document_id}",
    response_model=MemoDetailResponse,
    summary="Detalle de memo",
    description="""Obtiene el detalle completo de un memo oficial.

Verifica acceso del usuario (debe ser sender o recipient).
Registra la apertura automaticamente si es recipient.
Si el usuario es sender, muestra las aperturas de todos los recipients.
"""
)
async def get_memo(
    document_id: UUID = Path(..., description="UUID del documento"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Obtiene el detalle de un memo y registra la apertura si corresponde.

    Seguridad:
    - Solo el sender y los recipients pueden ver el memo
    - BCC solo es visible para el sender
    - Las aperturas solo son visibles para el sender
    """
    try:
        logger.info(
            f"Usuario {current_user.user_id} accediendo a memo {document_id}"
        )

        result = await get_memo_detail(
            document_id=str(document_id),
            requesting_user_id=current_user.user_id,
            schema_name=schema_name
        )

        return MemoDetailResponse(**result)

    except Exception as e:
        logger.error(f"Error al obtener detalle de memo {document_id}: {e}")
        raise exception_to_http_exception(e)
