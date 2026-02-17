"""Endpoint para obtener estadísticas del Dashboard."""

from shared.logging import get_logger
from fastapi import APIRouter, Depends, Request

from auth import get_current_user
from models.schemas import AuthenticatedUser
from services.dashboard_service import DashboardService
from schemas.dashboard_schemas import StatsResponse, StatsData
from shared.exceptions import exception_to_http_exception, ValidationError
from shared.utils import get_authenticated_user
from shared.dependencies import get_tenant_schema
from config.constants import USER_UNAUTHENTICATED_ERROR

logger = get_logger(__name__)
router = APIRouter(tags=["dashboard"])


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
) -> StatsResponse:
    """
    Obtiene estadísticas del dashboard para el usuario.

    V1: Solo retorna el conteo de expedientes donde el sector del usuario
    es el sector administrativo activo.

    Otros KPIs (documentos pendientes, etc.) se agregarán en futuras versiones.
    """
    try:
        # Validar usuario autenticado
        tenant_user_id = getattr(request.state, 'tenant_user_id', None)
        if not tenant_user_id:
            raise ValidationError(USER_UNAUTHENTICATED_ERROR)

        logger.info(f"Getting stats - User: {tenant_user_id[:8]}")

        # Obtener usuario validado
        db_user_id = get_authenticated_user(tenant_user_id, schema_name=schema_name)

        result = DashboardService.get_stats(
            user_id=db_user_id,
            schema_name=schema_name
        )

        logger.info(f"Stats returned: cases_in_sector={result['cases_in_sector']}")

        return StatsResponse(
            success=True,
            data=StatsData(**result),
            message="Estadísticas obtenidas correctamente"
        )

    except ValidationError as e:
        logger.error(f"Validation error getting stats: {str(e)}")
        raise exception_to_http_exception(e)
    except Exception as e:
        logger.error(f"Error getting stats: {str(e)}")
        raise exception_to_http_exception(e)
