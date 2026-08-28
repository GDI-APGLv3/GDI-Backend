
from fastapi import APIRouter, Depends, Request, Response, status

from auth import get_current_user
from models.schemas import AuthenticatedUser
from shared.dependencies import get_tenant_schema
from shared.utils import get_authenticated_user
from shared.exceptions import exception_to_http_exception, ValidationError
from shared.logging import get_logger
from config.constants import USER_UNAUTHENTICATED_ERROR
from services.home.service import dismiss_notification
from schemas.home_schemas import DismissRequest

logger = get_logger(__name__)
router = APIRouter()


@router.post("/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def post_dismiss(
    request: Request,
    body: DismissRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
) -> Response:
    """
    Dismiss idempotente (UNIQUE user_id+notification_key). `key` va por BODY
    (no por query/path) porque las keys llevan ':' (ej. `mention:{movement_id}`).
    Regex validado en el schema: `^(responsible|mention):.+`.
    """
    try:
        tenant_user_id = getattr(request.state, "tenant_user_id", None)
        if not tenant_user_id:
            raise ValidationError(USER_UNAUTHENTICATED_ERROR)

        db_user_id = await get_authenticated_user(tenant_user_id, schema_name=schema_name)
        await dismiss_notification(db_user_id, body.key, schema_name=schema_name)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as exc:
        logger.error(f"Error en /home/dismiss: {exc}")
        raise exception_to_http_exception(exc)
