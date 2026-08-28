
from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, Field

from auth import get_current_user
from models.schemas import AuthenticatedUser
from database import execute
from shared.exceptions import NotFoundError, exception_to_http_exception
from shared.utils import get_authenticated_user
from shared.dependencies import get_tenant_schema
from shared.logging import get_logger
from services.case_service import CaseService
from config.constants import CASE_NOT_FOUND_ERROR

logger = get_logger(__name__)

router = APIRouter(tags=["expedientes"])


class FavoriteResponse(BaseModel):
    is_favorite: bool = Field(..., example=True)


@router.post("/{case_id}/favorite", response_model=FavoriteResponse)
async def add_favorite(
    request: Request,
    case_id: str = Path(..., description="UUID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Marcar un expediente como favorito.

    Inserta en case_favorites (user_id, case_id). Si ya existe no falla
    (ON CONFLICT DO NOTHING). Verifica que el expediente existe antes
    de operar (sin revelar detalles si no existe).

    Returns: `{"is_favorite": true}`
    """
    try:
        db_user_id = await get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        logger.info(f"Add favorite: case={case_id[:8]}, user={db_user_id[:8]}")

        if not await CaseService.can_user_view_case(case_id, db_user_id, schema_name=schema_name):
            logger.warning(f"Access denied for add_favorite: user={db_user_id[:8]}, case={case_id[:8]}")
            raise NotFoundError(CASE_NOT_FOUND_ERROR)

        await execute(
            """
            INSERT INTO case_favorites (user_id, case_id)
            VALUES ($1, $2)
            ON CONFLICT (user_id, case_id) DO NOTHING
            """,
            db_user_id, case_id,
            schema_name=schema_name,
        )

        logger.info(f"Favorite added: case={case_id[:8]}, user={db_user_id[:8]}")
        return FavoriteResponse(is_favorite=True)

    except Exception as e:
        logger.error(f"Error in add_favorite: {str(e)}")
        raise exception_to_http_exception(e)


@router.delete("/{case_id}/favorite", response_model=FavoriteResponse)
async def remove_favorite(
    request: Request,
    case_id: str = Path(..., description="UUID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Quitar un expediente de favoritos.

    Elimina de case_favorites WHERE user_id=? AND case_id=?.
    Verifica que el expediente existe antes de operar.

    Returns: `{"is_favorite": false}`
    """
    try:
        db_user_id = await get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        logger.info(f"Remove favorite: case={case_id[:8]}, user={db_user_id[:8]}")

        if not await CaseService.can_user_view_case(case_id, db_user_id, schema_name=schema_name):
            logger.warning(f"Access denied for remove_favorite: user={db_user_id[:8]}, case={case_id[:8]}")
            raise NotFoundError(CASE_NOT_FOUND_ERROR)

        await execute(
            """
            DELETE FROM case_favorites
            WHERE user_id = $1 AND case_id = $2
            """,
            db_user_id, case_id,
            schema_name=schema_name,
        )

        logger.info(f"Favorite removed: case={case_id[:8]}, user={db_user_id[:8]}")
        return FavoriteResponse(is_favorite=False)

    except Exception as e:
        logger.error(f"Error in remove_favorite: {str(e)}")
        raise exception_to_http_exception(e)
