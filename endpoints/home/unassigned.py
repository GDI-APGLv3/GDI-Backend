
from fastapi import APIRouter, Depends, Query, Request

from auth import get_current_user
from models.schemas import AuthenticatedUser
from shared.dependencies import get_tenant_schema
from shared.utils import get_authenticated_user
from shared.exceptions import exception_to_http_exception, ValidationError
from shared.logging import get_logger
from config.constants import USER_UNAUTHENTICATED_ERROR
from services.home.service import get_home_unassigned
from schemas.home_schemas import UnassignedResponse

logger = get_logger(__name__)
router = APIRouter()


@router.get("/unassigned", response_model=UnassignedResponse)
async def get_unassigned(
    request: Request,
    limit: int = Query(3, ge=1, le=20),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
) -> UnassignedResponse:
    """
    ① `unowned`: expedientes de mi sector (viewable_cases) sin responsable
       activo -> botón "Hacerme responsable" (POST /cases/{id}/responsibles).
    ② `tasks`: tareas de mi sector sin tomar -> botón "Tomar tarea"
       (PATCH /cases/{id}/tasks/{task_id}).
    Ninguna de las dos suma al contador rojo (decisión A: informativas).
    """
    try:
        tenant_user_id = getattr(request.state, "tenant_user_id", None)
        if not tenant_user_id:
            raise ValidationError(USER_UNAUTHENTICATED_ERROR)

        db_user_id = await get_authenticated_user(tenant_user_id, schema_name=schema_name)
        result = await get_home_unassigned(db_user_id, limit, schema_name=schema_name)
        return UnassignedResponse(**result)
    except Exception as exc:
        logger.error(f"Error obteniendo /home/unassigned: {exc}")
        raise exception_to_http_exception(exc)
