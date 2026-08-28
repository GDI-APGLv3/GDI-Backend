
from shared.logging import get_logger
from fastapi import APIRouter, Depends, Query
from auth import get_current_user
from shared.dependencies import get_tenant_schema
from shared.utils import get_authenticated_user
from shared.exceptions import (
    NotFoundError,
    AuthorizationError,
    ConflictError,
    exception_to_http_exception
)
from models.rlm.schemas import LinkCaseRequest, LinkResponse
from services.rlm.links import get_linked_cases, link_case, unlink_case

logger = get_logger(__name__)

router = APIRouter(tags=["rlm-cases"])


@router.get("/records/{record_id}/cases", response_model=LinkResponse)
async def get_record_cases(
    record_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """Listar expedientes vinculados a un legajo."""
    try:
        db_user_id = await get_authenticated_user(current_user.user_id, schema_name=schema_name)
        result = await get_linked_cases(
            record_id,
            db_user_id,
            schema_name=schema_name,
            page=page,
            page_size=page_size,
        )

        return LinkResponse(
            success=True,
            data=result,
            message=f"Se encontraron {result['total']} expedientes vinculados"
        )

    except Exception as e:
        logger.error(f"Error getting linked cases: {e}")
        raise exception_to_http_exception(e)


@router.post("/records/{record_id}/cases", response_model=LinkResponse)
async def link_case_to_record(
    record_id: str,
    request: LinkCaseRequest,
    current_user: dict = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """Vincular un expediente a un legajo."""
    try:
        db_user_id = await get_authenticated_user(current_user.user_id, schema_name=schema_name)

        result = await link_case(
            record_id=record_id,
            case_id=request.case_id,
            user_id=db_user_id,
            notes=request.notes,
            schema_name=schema_name
        )

        return LinkResponse(
            success=True,
            data=result,
            message="Expediente vinculado exitosamente"
        )

    except (NotFoundError, AuthorizationError, ConflictError) as e:
        raise exception_to_http_exception(e)
    except Exception as e:
        logger.error(f"Error linking case: {e}")
        raise exception_to_http_exception(e)


@router.delete("/records/{record_id}/cases/{link_id}", response_model=LinkResponse)
async def unlink_case_from_record(
    record_id: str,
    link_id: str,
    current_user: dict = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """Desvincular un expediente de un legajo."""
    try:
        db_user_id = await get_authenticated_user(current_user.user_id, schema_name=schema_name)

        result = await unlink_case(
            record_id=record_id,
            link_id=link_id,
            user_id=db_user_id,
            schema_name=schema_name
        )

        return LinkResponse(
            success=True,
            data=result,
            message="Expediente desvinculado exitosamente"
        )

    except (NotFoundError, AuthorizationError) as e:
        raise exception_to_http_exception(e)
    except Exception as e:
        logger.error(f"Error unlinking case: {e}")
        raise exception_to_http_exception(e)
