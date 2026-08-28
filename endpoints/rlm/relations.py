
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
from models.rlm.schemas import CreateRelationRequest, RelationResponse
from services.rlm.relations import get_relations, create_relation, delete_relation

logger = get_logger(__name__)

router = APIRouter(tags=["rlm-relations"])


@router.get("/records/{record_id}/relations", response_model=RelationResponse)
async def get_record_relations(
    record_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """Listar relaciones de un legajo con otros legajos."""
    try:
        db_user_id = await get_authenticated_user(current_user.user_id, schema_name=schema_name)
        result = await get_relations(
            record_id,
            db_user_id,
            schema_name=schema_name,
            page=page,
            page_size=page_size,
        )

        return RelationResponse(
            success=True,
            data=result,
            message=f"Se encontraron {result['total']} relaciones"
        )

    except Exception as e:
        logger.error(f"Error getting relations: {e}")
        raise exception_to_http_exception(e)


@router.post("/records/{record_id}/relations", response_model=RelationResponse)
async def create_record_relation(
    record_id: str,
    request: CreateRelationRequest,
    current_user: dict = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """Crear una relación entre dos legajos."""
    try:
        db_user_id = await get_authenticated_user(current_user.user_id, schema_name=schema_name)

        result = await create_relation(
            record_id=record_id,
            target_record_id=request.target_record_id,
            relation_type=request.relation_type,
            user_id=db_user_id,
            notes=request.notes,
            schema_name=schema_name
        )

        return RelationResponse(
            success=True,
            data=result,
            message="Relación creada exitosamente"
        )

    except (ValidationError, NotFoundError, AuthorizationError) as e:
        raise exception_to_http_exception(e)
    except Exception as e:
        logger.error(f"Error creating relation: {e}")
        raise exception_to_http_exception(e)


@router.delete("/records/{record_id}/relations/{relation_id}", response_model=RelationResponse)
async def delete_record_relation(
    record_id: str,
    relation_id: str,
    current_user: dict = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """Eliminar una relación entre legajos."""
    try:
        db_user_id = await get_authenticated_user(current_user.user_id, schema_name=schema_name)

        result = await delete_relation(
            record_id=record_id,
            relation_id=relation_id,
            user_id=db_user_id,
            schema_name=schema_name
        )

        return RelationResponse(
            success=True,
            data=result,
            message="Relación eliminada exitosamente"
        )

    except (NotFoundError, AuthorizationError) as e:
        raise exception_to_http_exception(e)
    except Exception as e:
        logger.error(f"Error deleting relation: {e}")
        raise exception_to_http_exception(e)
