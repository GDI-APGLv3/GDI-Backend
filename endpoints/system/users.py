from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field

from auth import get_current_user
from models.schemas import AuthenticatedUser
from models.tags import Tags
from shared.dependencies import get_tenant_schema
from shared.exceptions import NotFoundError, exception_to_http_exception
from shared.logging import get_logger
from services.users.info import get_user_info

logger = get_logger(__name__)
router = APIRouter(tags=[Tags.SISTEMA])


class UserSector(BaseModel):
    id: Optional[str] = Field(None, example="660e8400-e29b-41d4-a716-446655440001")
    acronym: Optional[str] = Field(None, example="TESO")
    department_id: Optional[str] = Field(None, example="770e8400-e29b-41d4-a716-446655440002")
    department_name: Optional[str] = Field(None, example="Tesorería")
    department_acronym: Optional[str] = Field(None, example="TESO")


class AdditionalSector(BaseModel):
    sector_id: str = Field(..., example="660e8400-e29b-41d4-a716-446655440003")
    sector_acronym: str = Field(..., example="ADGEN")
    department_acronym: str = Field(..., example="ADGEN")
    department_name: str = Field(..., example="Administración General")
    can_view: bool = Field(..., example=True)
    can_edit: bool = Field(..., example=False)


class SystemUserResponse(BaseModel):
    user_id: str = Field(..., example="a1000000-0000-0000-0000-000000000001")
    full_name: str = Field(..., example="Juan Pérez")
    email: Optional[str] = Field(None, example="juan.perez@municipio.gob.ar")
    profile_picture_url: Optional[str] = Field(None, example="https://storage.com/p.jpg")
    estado: Optional[int] = Field(None, example=1)
    last_access: Optional[str] = Field(None, example="2025-12-02T14:45:00")
    created_at: Optional[str] = Field(None, example="2024-01-15T10:30:00")
    sector: Optional[UserSector] = Field(None)
    roles: List[str] = Field(default_factory=list, example=["admin"])
    additional_sectors: List[AdditionalSector] = Field(default_factory=list)


@router.get(
    "/api/v1/system/users/{user_id}",
    response_model=SystemUserResponse,
    summary="Información de un usuario del tenant",
    description=(
        "Obtiene perfil, sector, roles y sectores adicionales de un usuario. "
        "**SEC-12**: un usuario sólo puede consultar su propia información. "
        "Equivalente al endpoint del Gateway (SET B) `GET /api/v1/system/users/{user_id}`. "
        "Accesible por JWT y por API Key."
    ),
    responses={
        403: {"description": "Solo puede consultar su propia información"},
        404: {"description": "Usuario no encontrado"},
    },
)
async def get_system_user(
    request: Request,
    user_id: str = Path(..., description="UUID del usuario a consultar"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
) -> Dict[str, Any]:
    """
    Devuelve información del usuario indicado.

    SEC-12: solo permite consultar la propia información (target == auth user).
    Aplica tanto para JWT como para API Key.
    """
    auth_user_id = request.state.tenant_user_id

    if user_id != auth_user_id:
        raise HTTPException(
            status_code=403,
            detail="Solo puede consultar su propia información de usuario",
        )

    try:
        logger.info(
            f"GET /api/v1/system/users/{user_id} - auth_user={auth_user_id}"
        )
        return await get_user_info(user_id, schema_name=schema_name)

    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error in get_system_user endpoint: {str(e)}")
        raise exception_to_http_exception(e)
