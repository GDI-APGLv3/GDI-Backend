
from fastapi import APIRouter, Depends, Request

from auth import get_current_user
from models.schemas import AuthenticatedUser
from shared.dependencies import get_tenant_schema
from shared.utils import get_authenticated_user
from shared.exceptions import exception_to_http_exception, ValidationError
from shared.logging import get_logger
from config.constants import USER_UNAUTHENTICATED_ERROR
from services.home.service import get_home_count
from schemas.home_schemas import CountResponse

logger = get_logger(__name__)
router = APIRouter()


@router.get("/count", response_model=CountResponse)
async def get_count(
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
) -> CountResponse:
    """
    Contador de las 3 fuentes accionables (firmas + memos + notas al sector).
    Es lo único que polea el front cada 30s (SWR) para el badge del menú.
    """
    try:
        tenant_user_id = getattr(request.state, "tenant_user_id", None)
        if not tenant_user_id:
            raise ValidationError(USER_UNAUTHENTICATED_ERROR)

        db_user_id = await get_authenticated_user(tenant_user_id, schema_name=schema_name)
        result = await get_home_count(db_user_id, schema_name=schema_name)
        return CountResponse(**result)
    except Exception as exc:
        logger.error(f"Error obteniendo /home/count: {exc}")
        raise exception_to_http_exception(exc)
