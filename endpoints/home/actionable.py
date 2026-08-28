
from fastapi import APIRouter, Depends, Query, Request

from auth import get_current_user
from models.schemas import AuthenticatedUser
from shared.dependencies import get_tenant_schema
from shared.utils import get_authenticated_user
from shared.exceptions import exception_to_http_exception, ValidationError
from shared.logging import get_logger
from config.constants import USER_UNAUTHENTICATED_ERROR
from services.home.service import get_home_actionable
from schemas.home_schemas import ActionableResponse

logger = get_logger(__name__)
router = APIRouter()


@router.get("/actionable", response_model=ActionableResponse)
async def get_actionable(
    request: Request,
    limit: int = Query(5, ge=1, le=50, description="Máximo de items POR CAJA (el front muestra 5)"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
) -> ActionableResponse:
    """
    Detalle de firmas pendientes, memos sin leer y notas sin abrir, cortado en
    `limit` por caja. El total real de cada caja lo da /home/count (`by_source`):
    traer 200 firmas para mostrar 5 era trabajo puro de descarte.
    """
    try:
        tenant_user_id = getattr(request.state, "tenant_user_id", None)
        if not tenant_user_id:
            raise ValidationError(USER_UNAUTHENTICATED_ERROR)

        db_user_id = await get_authenticated_user(tenant_user_id, schema_name=schema_name)
        result = await get_home_actionable(db_user_id, limit, schema_name=schema_name)
        return ActionableResponse(**result)
    except Exception as exc:
        logger.error(f"Error obteniendo /home/actionable: {exc}")
        raise exception_to_http_exception(exc)
