
from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, Field
from typing import Optional, List
from auth import get_current_user
from models.schemas import AuthenticatedUser
from shared.exceptions import NotFoundError, exception_to_http_exception
from shared.utils import get_authenticated_user
from shared.dependencies import get_tenant_schema
from services.case_service import CaseService
from shared.exceptions import AuthorizationError
from config.constants import CASE_NOT_FOUND_ERROR
from services.cases.responsibles import (
    get_case_responsibles,
    add_responsible,
    remove_responsible,
    get_available_responsibles,
)
from shared.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["expedientes"])


class AddResponsibleRequest(BaseModel):
    user_id: str = Field(..., description="ID del usuario a agregar como responsable")
    type: str = Field(..., description="Tipo: ADMIN o ADDITIONAL")
    sector_id: str = Field(..., description="ID del sector del usuario")
    reason: str = Field("Asignación de responsable", min_length=1, max_length=500)


class ResponsableItem(BaseModel):
    id: str
    user_id: str
    sector_id: str
    type: str
    full_name: str
    email: str
    sector_acronym: str
    department_name: str
    department_acronym: Optional[str] = None
    profile_picture_url: Optional[str] = None
    seal_name: Optional[str] = None
    added_at: Optional[str] = None


class ResponsablesData(BaseModel):
    admin: List[ResponsableItem] = Field(default_factory=list)
    additional: List[ResponsableItem] = Field(default_factory=list)


class ResponsablesResponse(BaseModel):
    success: bool = True
    data: ResponsablesData
    message: str


class AvailableResponsableItem(BaseModel):
    user_id: str
    full_name: str
    sector_id: str
    sector_acronym: str
    department_name: Optional[str] = None
    profile_picture_url: Optional[str] = None
    department_acronym: Optional[str] = None
    seal_name: Optional[str] = None


class AvailableResponsablesResponse(BaseModel):
    success: bool = True
    data: List[AvailableResponsableItem]
    total: int
    message: str


class AddResponsibleResponse(BaseModel):
    success: bool = True
    data: dict
    message: str


@router.get("/{case_id}/responsibles", response_model=ResponsablesResponse)
async def list_responsibles(
    request: Request,
    case_id: str = Path(..., description="ID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    """
    Listar responsables activos de un expediente.
    Retorna los responsables administradores (lista) y los adicionales (lista).
    """
    try:
        db_user_id = await get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        if not await CaseService.can_user_view_case(case_id, db_user_id, schema_name=schema_name):
            logger.warning(f"Access denied for list_responsibles: user={db_user_id[:8]}, case={case_id[:8]}")
            raise NotFoundError(CASE_NOT_FOUND_ERROR)

        result = await get_case_responsibles(case_id, schema_name=schema_name)
        return {
            "success": True,
            "data": result,
            "message": "Responsables obtenidos correctamente",
        }
    except Exception as exc:
        logger.error(f"Error listando responsables para caso {case_id}: {exc}")
        raise exception_to_http_exception(exc)


@router.get("/{case_id}/available-responsibles", response_model=AvailableResponsablesResponse)
async def list_available_responsibles(
    request: Request,
    case_id: str = Path(..., description="ID del expediente"),
    type: str = Query(..., description="Tipo de responsable: ADMIN o ADDITIONAL"),
    sector_id: str | None = Query(None, description="Filtrar por sector destino (Transferencia/Asignación)"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    """
    Listar usuarios disponibles para ser asignados como responsables.

    - type=ADMIN: usuarios del sector administrador actual del expediente.
    - type=ADDITIONAL: usuarios de sectores admin o actuantes activos del expediente
      (sector principal o can_edit=true en user_sector_permissions).
    - sector_id (opcional): filtra directamente por ese sector (Transferencia/Asignación).
    """
    try:
        db_user_id = await get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        if not await CaseService.can_user_view_case(case_id, db_user_id, schema_name=schema_name):
            logger.warning(f"Access denied for list_available_responsibles: user={db_user_id[:8]}, case={case_id[:8]}")
            raise NotFoundError(CASE_NOT_FOUND_ERROR)

        responsible_type = type.upper()
        if responsible_type not in ("ADMIN", "ADDITIONAL"):
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail={"message": "type debe ser ADMIN o ADDITIONAL", "type": "ValidationError"})

        users = await get_available_responsibles(case_id, responsible_type, sector_id=sector_id, schema_name=schema_name)
        return {
            "success": True,
            "data": users,
            "total": len(users),
            "message": f"{len(users)} usuario(s) disponible(s)",
        }
    except Exception as exc:
        logger.error(f"Error obteniendo responsables disponibles para caso {case_id}: {exc}")
        raise exception_to_http_exception(exc)


@router.post("/{case_id}/responsibles", response_model=AddResponsibleResponse)
async def add_case_responsible(
    request: Request,
    body: AddResponsibleRequest,
    case_id: str = Path(..., description="ID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    """
    Agregar un responsable al expediente.

    - type=ADMIN: agrega un nuevo responsable admin (coexiste con los existentes, no reemplaza).
    - type=ADDITIONAL: agrega como responsable adicional sin tocar los admins.

    Requiere ser admin o actuante con can_edit=true y expediente activo.
    """
    try:
        db_user_id = await get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        if not await CaseService.can_user_view_case(case_id, db_user_id, schema_name=schema_name):
            logger.warning(f"Access denied for add_case_responsible: user={db_user_id[:8]}, case={case_id[:8]}")
            raise NotFoundError(CASE_NOT_FOUND_ERROR)

        if not await CaseService.can_user_edit_case(case_id, db_user_id, schema_name=schema_name):
            logger.warning(f"Edit denied for add_case_responsible: user={db_user_id[:8]}, case={case_id[:8]}")
            raise AuthorizationError("Sin permisos para modificar responsables de este expediente")

        responsible_type = body.type.upper()
        if responsible_type not in ("ADMIN", "ADDITIONAL"):
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail={"message": "type debe ser ADMIN o ADDITIONAL", "type": "ValidationError"})

        result = await add_responsible(
            case_id=case_id,
            user_id=body.user_id,
            responsible_type=responsible_type,
            sector_id=body.sector_id,
            added_by=db_user_id,
            movement_reason=body.reason,
            schema_name=schema_name,
        )
        return {
            "success": True,
            "data": result,
            "message": "Responsable agregado correctamente",
        }
    except Exception as exc:
        logger.error(f"Error agregando responsable al caso {case_id}: {exc}")
        raise exception_to_http_exception(exc)


@router.delete("/{case_id}/responsibles/{responsible_id}", response_model=AddResponsibleResponse)
async def remove_case_responsible(
    request: Request,
    case_id: str = Path(..., description="ID del expediente"),
    responsible_id: str = Path(..., description="ID del registro en case_responsibles"),
    reason: Optional[str] = Query(None, description="Motivo de la remoción"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    """
    Quitar un responsable del expediente (soft delete: is_active = false).
    Crea movimiento de tipo 'responsible_remove' en el historial.
    El motivo se pasa como query param: ?reason=texto
    """
    try:
        db_user_id = await get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        if not await CaseService.can_user_view_case(case_id, db_user_id, schema_name=schema_name):
            logger.warning(f"Access denied for remove_case_responsible: user={db_user_id[:8]}, case={case_id[:8]}")
            raise NotFoundError(CASE_NOT_FOUND_ERROR)

        if not await CaseService.can_user_edit_case(case_id, db_user_id, schema_name=schema_name):
            logger.warning(f"Edit denied for remove_case_responsible: user={db_user_id[:8]}, case={case_id[:8]}")
            raise AuthorizationError("Sin permisos para modificar responsables de este expediente")

        await remove_responsible(
            responsible_id=responsible_id,
            removed_by=db_user_id,
            movement_reason=reason or "Remoción de responsable",
            schema_name=schema_name,
        )
        return {
            "success": True,
            "data": {"responsible_id": responsible_id},
            "message": "Responsable removido correctamente",
        }
    except Exception as exc:
        logger.error(f"Error removiendo responsable {responsible_id} del caso {case_id}: {exc}")
        raise exception_to_http_exception(exc)
