"""
Endpoint para obtener el contador de memos no leidos.
Util para el badge en el menu lateral del frontend.
"""

from fastapi import APIRouter, Depends
from shared.logging import get_logger
from shared.dependencies import get_tenant_schema
from shared.exceptions import exception_to_http_exception
from auth import get_current_user
from models.schemas import AuthenticatedUser
from models.memos.responses import MemoUnreadCountResponse
from services.memos import get_unread_memo_count

logger = get_logger("memos_unread_count")

router = APIRouter()


@router.get(
    "/unread-count",
    response_model=MemoUnreadCountResponse,
    summary="Contador de memos no leidos",
    description="""Obtiene la cantidad de memos no leidos para el usuario actual.

Util para mostrar un badge/contador en el menu lateral.

Cuenta memos donde:
- El usuario es recipient
- No esta archivado
- No ha sido abierto (opened_at IS NULL)
- El documento esta oficializado
"""
)
async def get_unread_count(
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Retorna la cantidad de memos no leidos para el usuario actual.
    """
    try:
        count = await get_unread_memo_count(
            current_user.user_id,
            schema_name=schema_name
        )

        return MemoUnreadCountResponse(unread_count=count)

    except Exception as e:
        logger.error(f"Error al obtener contador de memos no leidos: {e}")
        raise exception_to_http_exception(e)
