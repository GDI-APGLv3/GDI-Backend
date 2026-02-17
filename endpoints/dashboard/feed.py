"""Endpoint para obtener el feed de actividad del Dashboard."""

from shared.logging import get_logger
from fastapi import APIRouter, Depends, Query, Request

from auth import get_current_user
from models.schemas import AuthenticatedUser
from services.dashboard_service import DashboardService
from schemas.dashboard_schemas import FeedResponse, FeedData
from shared.exceptions import exception_to_http_exception, ValidationError
from shared.utils import get_authenticated_user
from shared.dependencies import get_tenant_schema
from config.constants import (
    DEFAULT_PAGE,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    USER_UNAUTHENTICATED_ERROR
)

logger = get_logger(__name__)
router = APIRouter(tags=["dashboard"])


@router.get("/feed", response_model=FeedResponse)
async def get_feed(
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
    page: int = Query(DEFAULT_PAGE, ge=1, description="Número de página"),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Elementos por página")
) -> FeedResponse:
    """
    Obtiene el feed de actividad del usuario.

    Retorna los movimientos (case_movements) de todos los expedientes donde
    el usuario tiene permisos de VIEW, ordenados por fecha descendente.

    Cada movimiento incluye:
    - Datos del expediente (número, referencia, tipo)
    - Datos del usuario que realizó el movimiento
    - Resumen IA del documento de soporte (si aplica)
    """
    try:
        # Validar usuario autenticado
        tenant_user_id = getattr(request.state, 'tenant_user_id', None)
        if not tenant_user_id:
            raise ValidationError(USER_UNAUTHENTICATED_ERROR)

        logger.info(f"Getting feed - User: {tenant_user_id[:8]}, Page: {page}")

        # Obtener usuario validado
        db_user_id = get_authenticated_user(tenant_user_id, schema_name=schema_name)

        result = DashboardService.get_feed(
            user_id=db_user_id,
            page=page,
            page_size=page_size,
            schema_name=schema_name
        )

        logger.info(f"Feed returned {result['total']} total movements")

        return FeedResponse(
            success=True,
            data=FeedData(**result),
            message=f"Se encontraron {result['total']} movimientos"
        )

    except ValidationError as e:
        logger.error(f"Validation error getting feed: {str(e)}")
        raise exception_to_http_exception(e)
    except Exception as e:
        logger.error(f"Error getting feed: {str(e)}")
        raise exception_to_http_exception(e)
