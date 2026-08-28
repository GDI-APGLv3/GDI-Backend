
from uuid import UUID

from shared.logging import get_logger
from fastapi import APIRouter, Depends
from auth import get_current_user
from shared.dependencies import get_tenant_schema
from shared.utils import get_authenticated_user
from shared.exceptions import NotFoundError, exception_to_http_exception
from models.rlm.schemas import RegistryListResponse, RegistryDetailResponse
from services.rlm.registries import list_registries, get_registry_detail

logger = get_logger(__name__)

router = APIRouter(tags=["rlm-registries"])


@router.get("/registries", response_model=RegistryListResponse)
async def get_registries(
    current_user: dict = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """Listar registros disponibles con permisos del usuario."""
    try:
        db_user_id = await get_authenticated_user(current_user.user_id, schema_name=schema_name)
        result = await list_registries(db_user_id, schema_name=schema_name)

        return RegistryListResponse(
            success=True,
            data=result,
            message=f"Se encontraron {result['total']} registros"
        )

    except Exception as e:
        logger.error(f"Error listing registries: {e}")
        raise exception_to_http_exception(e)


@router.get("/registries/{registry_id}", response_model=RegistryDetailResponse)
async def get_registry(
    registry_id: UUID,
    current_user: dict = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """Obtener detalle de un registro con su data_schema."""
    try:
        db_user_id = await get_authenticated_user(current_user.user_id, schema_name=schema_name)
        result = await get_registry_detail(str(registry_id), db_user_id, schema_name=schema_name)

        return RegistryDetailResponse(
            success=True,
            data=result,
            message=f"Registro '{result['code']}' encontrado"
        )

    except NotFoundError as e:
        raise exception_to_http_exception(e)
    except Exception as e:
        logger.error(f"Error getting registry detail: {e}")
        raise exception_to_http_exception(e)
