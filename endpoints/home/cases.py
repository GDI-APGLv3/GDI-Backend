
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from auth import get_current_user
from models.schemas import AuthenticatedUser
from shared.dependencies import get_tenant_schema
from shared.utils import get_authenticated_user
from shared.exceptions import exception_to_http_exception, ValidationError
from shared.logging import get_logger
from config.constants import USER_UNAUTHENTICATED_ERROR
from services.home.service import get_home_cases
from schemas.home_schemas import CasesResponse

logger = get_logger(__name__)
router = APIRouter()


@router.get("/cases", response_model=CasesResponse)
async def get_cases(
    request: Request,
    scope: str = Query("mine", pattern="^(mine|all)$", description="'mine' = soy responsable activo; 'all' = viewable_cases"),
    limit: int = Query(30, ge=1, le=100),
    cursor: Optional[str] = Query(None, description="Cursor opaco de keyset (solo aplica a case_movements)"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
) -> CasesResponse:
    """
    Si `scope=mine` viene vacío, el FRONT decide el auto-jump a `scope=all`
    (este endpoint solo responde el scope pedido, no decide fallback).
    """
    try:
        tenant_user_id = getattr(request.state, "tenant_user_id", None)
        if not tenant_user_id:
            raise ValidationError(USER_UNAUTHENTICATED_ERROR)

        db_user_id = await get_authenticated_user(tenant_user_id, schema_name=schema_name)
        result = await get_home_cases(db_user_id, scope, limit, cursor, schema_name=schema_name)
        return CasesResponse(**result)
    except Exception as exc:
        logger.error(f"Error obteniendo /home/cases: {exc}")
        raise exception_to_http_exception(exc)
