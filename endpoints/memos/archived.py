
from fastapi import APIRouter, Depends, Query
from shared.logging import get_logger
from shared.dependencies import get_tenant_schema
from shared.exceptions import exception_to_http_exception
from auth import get_current_user
from models.schemas import AuthenticatedUser
from models.memos.responses import MemoArchivedListResponse
from services.memos import get_archived_memos

logger = get_logger("memos_archived")

router = APIRouter()


@router.get(
    "/archived",
    response_model=MemoArchivedListResponse,
    summary="Memos archivados",
    description="""Obtiene los memos oficiales ARCHIVADOS por el usuario.

## Descripcion

Lista todos los MEMOS oficializados (firmados) que fueron ARCHIVADOS por el usuario.

## Diferencia con Recibidos

- `/memos/received` muestra memos **NO archivados** (bandeja de entrada activa)
- `/memos/archived` muestra memos **ARCHIVADOS** (bandeja de archivo)

## Notas

- Un memo archivado puede ser desarchivado desde el detalle
- Al desarchivar, el memo vuelve a aparecer en "Recibidos"
"""
)
async def list_archived_memos(
    page: int = Query(1, ge=1, description="Numero de pagina"),
    page_size: int = Query(20, ge=1, le=100, description="Elementos por pagina"),
    search: str | None = Query(None, min_length=2, description="Buscar en numero oficial, asunto o contenido"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Lista los memos ARCHIVADOS por el usuario actual.
    Solo incluye memos oficializados (firmados) que fueron archivados.
    """
    try:
        logger.info(
            f"Usuario {current_user.user_id} consultando memos archivados"
            f"{' search=' + search if search else ''}"
        )

        result = await get_archived_memos(
            current_user.user_id,
            schema_name=schema_name,
            page=page,
            page_size=page_size,
            search=search
        )

        return MemoArchivedListResponse(**result)

    except Exception as e:
        logger.error(f"Error al obtener memos archivados: {e}")
        raise exception_to_http_exception(e)
