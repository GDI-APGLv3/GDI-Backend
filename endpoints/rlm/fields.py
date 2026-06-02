"""Endpoints para campos enriquecidos y historial del módulo RLM."""

from uuid import UUID

from shared.logging import get_logger
from fastapi import APIRouter, Depends, Query
from auth import get_current_user
from shared.dependencies import get_tenant_schema
from shared.utils import get_authenticated_user
from shared.exceptions import (
    ValidationError,
    NotFoundError,
    AuthorizationError,
    exception_to_http_exception
)
from models.rlm.schemas import (
    UpdateFieldRequest,
    VerifyFieldRequest,
    FieldResponse,
    HistoryResponse,
)
from services.rlm.fields import update_field, verify_field
from services.rlm.history import get_history

logger = get_logger(__name__)

router = APIRouter(tags=["rlm-fields"])


@router.patch("/records/{record_id}/fields/{field_name}", response_model=FieldResponse)
async def update_record_field(
    record_id: UUID,
    field_name: str,
    request: UpdateFieldRequest,
    current_user: dict = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """Actualizar un campo enriquecido específico de un legajo."""
    try:
        db_user_id = await get_authenticated_user(current_user.user_id, schema_name=schema_name)

        result = await update_field(
            record_id=str(record_id),
            field_name=field_name,
            user_id=db_user_id,
            value=request.value,
            expiration_date=request.expiration_date,
            document_id=request.document_id,
            notes=request.notes,
            document_reference=request.document_reference,
            document_resume=request.document_resume,
            schema_name=schema_name
        )

        return FieldResponse(
            success=True,
            data=result,
            message=f"Campo '{field_name}' actualizado"
        )

    except (ValidationError, NotFoundError, AuthorizationError) as e:
        raise exception_to_http_exception(e)
    except Exception as e:
        logger.error(f"Error updating field: {e}")
        raise exception_to_http_exception(e)


@router.post("/records/{record_id}/fields/{field_name}/verify", response_model=FieldResponse)
async def verify_record_field(
    record_id: UUID,
    field_name: str,
    request: VerifyFieldRequest,
    current_user: dict = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """Marcar un campo como verificado."""
    try:
        db_user_id = await get_authenticated_user(current_user.user_id, schema_name=schema_name)

        result = await verify_field(
            record_id=str(record_id),
            field_name=field_name,
            user_id=db_user_id,
            document_id=request.document_id,
            notes=request.notes,
            schema_name=schema_name
        )

        return FieldResponse(
            success=True,
            data=result,
            message=f"Campo '{field_name}' verificado"
        )

    except (ValidationError, NotFoundError, AuthorizationError) as e:
        raise exception_to_http_exception(e)
    except Exception as e:
        logger.error(f"Error verifying field: {e}")
        raise exception_to_http_exception(e)


@router.get("/records/{record_id}/history", response_model=HistoryResponse)
async def get_record_history(
    record_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """Obtener historial de cambios de un legajo."""
    try:
        db_user_id = await get_authenticated_user(current_user.user_id, schema_name=schema_name)

        result = await get_history(
            record_id=str(record_id),
            user_id=db_user_id,
            schema_name=schema_name,
            page=page,
            page_size=page_size,
        )

        return HistoryResponse(
            success=True,
            data=result,
            message=f"Se encontraron {result['total']} entradas en el historial"
        )

    except Exception as e:
        logger.error(f"Error getting history: {e}")
        raise exception_to_http_exception(e)
