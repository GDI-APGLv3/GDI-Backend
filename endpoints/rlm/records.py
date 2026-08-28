
from uuid import UUID

from shared.logging import get_logger
from fastapi import APIRouter, Depends, Query
from typing import Optional
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
    CreateRecordRequest,
    UpdateRecordRequest,
    RecordResponse,
    RecordListResponse,
)
from services.rlm.records import (
    create_record,
    get_record,
    list_records,
    update_record,
    autocomplete_records,
)
from services.rlm.report import generate_ifrlm
from services.shared.resume_trigger import enqueue_record_resume_fire_and_forget

logger = get_logger(__name__)

router = APIRouter(tags=["rlm-records"])


@router.post("/records", response_model=RecordResponse)
async def create_new_record(
    request: CreateRecordRequest,
    current_user: dict = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """Crear un nuevo legajo."""
    try:
        db_user_id = await get_authenticated_user(current_user.user_id, schema_name=schema_name)

        result = await create_record(
            registry_code=request.registry_code,
            data=request.data,
            display_name=request.display_name,
            user_id=db_user_id,
            schema_name=schema_name
        )

        logger.info(f"Record created: {result['record_number']}")

        enqueue_record_resume_fire_and_forget(result['id'], schema_name)

        return RecordResponse(
            success=True,
            data=result,
            message=f"Legajo {result['record_number']} creado exitosamente"
        )

    except (ValidationError, NotFoundError, AuthorizationError) as e:
        logger.error(f"Error creating record: {e}")
        raise exception_to_http_exception(e)
    except Exception as e:
        logger.error(f"Unexpected error creating record: {e}", exc_info=True)
        raise exception_to_http_exception(e)


@router.get("/records", response_model=RecordListResponse)
async def list_all_records(
    registry: Optional[str] = Query(None, description="Filtrar por código de registro (ARQ, LUM, ORD)"),
    state: Optional[str] = Query(None, description="Filtrar por estado"),
    search: Optional[str] = Query(None, description="Buscar por número o datos"),
    page: int = Query(1, ge=1, description="Página"),
    page_size: int = Query(10, ge=1, le=100, description="Items por página"),
    current_user: dict = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """Listar legajos con filtros y paginación."""
    try:
        db_user_id = await get_authenticated_user(current_user.user_id, schema_name=schema_name)

        result = await list_records(
            user_id=db_user_id,
            schema_name=schema_name,
            registry_code=registry,
            state=state,
            search=search,
            page=page,
            page_size=page_size,
        )

        return RecordListResponse(
            success=True,
            data=result,
            message=f"Se encontraron {result['total']} legajos"
        )

    except Exception as e:
        logger.error(f"Error listing records: {e}")
        raise exception_to_http_exception(e)


@router.get("/records/autocomplete", response_model=RecordResponse)
async def autocomplete_records_endpoint(
    q: str = Query(..., min_length=2, max_length=50, description="Texto de búsqueda"),
    limit: int = Query(10, ge=1, le=50, description="Máximo de resultados"),
    current_user: dict = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """Autocompletado de legajos por número."""
    try:
        db_user_id = await get_authenticated_user(current_user.user_id, schema_name=schema_name)
        result = await autocomplete_records(
            user_id=db_user_id,
            query=q,
            schema_name=schema_name,
            limit=limit,
        )
        return RecordResponse(
            success=True,
            data=result,
            message=f"Se encontraron {result['total']} legajos"
        )
    except Exception as e:
        logger.error(f"Error in records autocomplete: {e}")
        raise exception_to_http_exception(e)


@router.get("/records/{record_id}", response_model=RecordResponse)
async def get_record_detail_endpoint(
    record_id: UUID,
    current_user: dict = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """Obtener detalle completo de un legajo."""
    try:
        db_user_id = await get_authenticated_user(current_user.user_id, schema_name=schema_name)
        result = await get_record(str(record_id), db_user_id, schema_name=schema_name)

        return RecordResponse(
            success=True,
            data=result,
            message=f"Legajo {result['record_number']} encontrado"
        )

    except NotFoundError as e:
        raise exception_to_http_exception(e)
    except Exception as e:
        logger.error(f"Error getting record detail: {e}")
        raise exception_to_http_exception(e)


@router.patch("/records/{record_id}", response_model=RecordResponse)
async def update_record_endpoint(
    record_id: UUID,
    request: UpdateRecordRequest,
    current_user: dict = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """Actualizar estado y/o nombre de un legajo."""
    try:
        db_user_id = await get_authenticated_user(current_user.user_id, schema_name=schema_name)
        result = await update_record(
            record_id=str(record_id),
            user_id=db_user_id,
            schema_name=schema_name,
            new_state=request.state,
            new_display_name=request.display_name,
            reason=request.reason,
        )
        parts = []
        if request.state:
            parts.append(f"Estado: '{request.state}'")
        if request.display_name:
            parts.append(f"Nombre: '{request.display_name}'")
        msg = "Legajo actualizado - " + ", ".join(parts)
        return RecordResponse(success=True, data=result, message=msg)
    except (ValidationError, NotFoundError, AuthorizationError) as e:
        raise exception_to_http_exception(e)
    except Exception as e:
        logger.error(f"Error updating record: {e}")
        raise exception_to_http_exception(e)


@router.post("/records/{record_id}/report", response_model=RecordResponse)
async def generate_record_report(
    record_id: UUID,
    current_user: dict = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Genera un informe IFRLM (snapshot) del legajo.

    Crea un documento tipo IFRLM con los datos actuales del legajo,
    lo vincula automaticamente y registra en historial.

    Requiere permiso can_edit sobre la familia del registro.
    """
    try:
        db_user_id = await get_authenticated_user(current_user.user_id, schema_name=schema_name)

        result = await generate_ifrlm(
            record_id=str(record_id),
            user_id=db_user_id,
            schema_name=schema_name,
            is_initial=False,
        )

        logger.info(f"IFRLM generated for record {str(record_id)[:8]}")

        return RecordResponse(
            success=True,
            data=result,
            message=f"Informe IFRLM generado exitosamente para legajo {result.get('record_number', record_id)}"
        )

    except (ValidationError, NotFoundError, AuthorizationError) as e:
        logger.error(f"Error generating IFRLM report: {e}")
        raise exception_to_http_exception(e)
    except Exception as e:
        logger.error(f"Unexpected error generating IFRLM report: {e}", exc_info=True)
        raise exception_to_http_exception(e)
