
from fastapi import APIRouter, Depends, Path, Request, Response, status

from auth import get_current_user
from models.schemas import AuthenticatedUser
from shared.dependencies import get_tenant_schema
from shared.utils import get_authenticated_user
from shared.exceptions import exception_to_http_exception, ValidationError
from shared.logging import get_logger
from config.constants import USER_UNAUTHENTICATED_ERROR
from services.home.service import mark_case_viewed

logger = get_logger(__name__)
router = APIRouter(tags=["expedientes"])


@router.post("/{case_id}/view", status_code=status.HTTP_204_NO_CONTENT)
async def mark_case_view(
    request: Request,
    case_id: str = Path(..., description="UUID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
) -> Response:
    """
    Marca el expediente como visto AHORA por el usuario (UPSERT
    case_user_views, ON CONFLICT DO UPDATE last_seen_at = NOW()). Idempotente:
    no falla si ya existía. NO valida permisos de visualización (fire-and-forget
    de UX, llamado en cada navegación). Si `case_id` no existe, `mark_case_viewed`
    valida la existencia ANTES del UPSERT y lanza `NotFoundError` -> HTTP 404
    limpio (crack #3, evita el 500 por violación de FK).
    """
    try:
        tenant_user_id = getattr(request.state, "tenant_user_id", None)
        if not tenant_user_id:
            raise ValidationError(USER_UNAUTHENTICATED_ERROR)

        db_user_id = await get_authenticated_user(tenant_user_id, schema_name=schema_name)
        await mark_case_viewed(db_user_id, case_id, schema_name=schema_name)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as exc:
        logger.error(f"Error marcando visto expediente {case_id}: {exc}")
        raise exception_to_http_exception(exc)
