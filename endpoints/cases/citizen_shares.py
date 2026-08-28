from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, Field

from auth import get_current_user
from models.schemas import AuthenticatedUser
from shared.exceptions import NotFoundError, AuthorizationError, exception_to_http_exception
from shared.utils import get_authenticated_user
from shared.dependencies import get_tenant_schema
from services.case_service import CaseService
from config.constants import CASE_NOT_FOUND_ERROR
from services.cases.citizen_shares import (
    share_case_with_citizen,
    unshare_case_from_citizen,
    list_shares_of_case,
    can_user_manage_citizen_shares,
)
from shared.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["expedientes"])


class ShareCitizenRequest(BaseModel):
    citizen_id: str = Field(..., description="UUID del ciudadano a compartir")


class CitizenShareItem(BaseModel):
    id: str
    citizen_id: str
    full_name: str
    country_id: str
    citizen_estado: str
    shared_by: str | None = None
    shared_at: str | None = None


class CitizenSharesResponse(BaseModel):
    success: bool = True
    data: list[CitizenShareItem]
    total: int
    message: str


class ShareCitizenResponse(BaseModel):
    success: bool = True
    data: dict
    message: str


@router.get("/{case_id}/citizen-shares", response_model=CitizenSharesResponse)
async def list_citizen_shares(
    request: Request,
    case_id: str = Path(..., description="ID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    """Ciudadanos con share ACTIVO en el expediente ("TAD (n)" de la caja del Panel)."""
    try:
        db_user_id = await get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        if not await CaseService.can_user_view_case(case_id, db_user_id, schema_name=schema_name):
            logger.warning(f"Access denied for list_citizen_shares: user={db_user_id[:8]}, case={case_id[:8]}")
            raise NotFoundError(CASE_NOT_FOUND_ERROR)

        shares = await list_shares_of_case(case_id, schema_name=schema_name)
        return {
            "success": True,
            "data": shares,
            "total": len(shares),
            "message": f"{len(shares)} ciudadano(s) compartido(s)",
        }
    except Exception as exc:
        logger.error(f"Error listando citizen-shares del caso {case_id}: {exc}")
        raise exception_to_http_exception(exc)


@router.post("/{case_id}/citizen-shares", response_model=ShareCitizenResponse)
async def add_citizen_share(
    request: Request,
    body: ShareCitizenRequest,
    case_id: str = Path(..., description="ID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    """Comparte manualmente el expediente con un ciudadano (pop-up "agregar" de la caja TAD)."""
    try:
        db_user_id = await get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        if not await CaseService.can_user_view_case(case_id, db_user_id, schema_name=schema_name):
            logger.warning(f"Access denied for add_citizen_share: user={db_user_id[:8]}, case={case_id[:8]}")
            raise NotFoundError(CASE_NOT_FOUND_ERROR)

        if not await can_user_manage_citizen_shares(case_id, db_user_id, schema_name=schema_name):
            logger.warning(f"Edit denied for add_citizen_share: user={db_user_id[:8]}, case={case_id[:8]}")
            raise AuthorizationError("Sin permisos para compartir este expediente con ciudadanos")

        result = await share_case_with_citizen(
            case_id=case_id,
            citizen_id=body.citizen_id,
            shared_by=db_user_id,
            schema_name=schema_name,
        )
        return {
            "success": True,
            "data": result,
            "message": "Ciudadano ya tenía el expediente compartido" if result.get("already_shared") else "Expediente compartido con el ciudadano",
        }
    except Exception as exc:
        logger.error(f"Error compartiendo caso {case_id} con ciudadano: {exc}")
        raise exception_to_http_exception(exc)


@router.delete("/{case_id}/citizen-shares/{citizen_id}", response_model=ShareCitizenResponse)
async def remove_citizen_share(
    request: Request,
    case_id: str = Path(..., description="ID del expediente"),
    citizen_id: str = Path(..., description="UUID del ciudadano a quitar"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    """Quita a un ciudadano del expediente (soft-delete, identificado por el par case_id+citizen_id)."""
    try:
        db_user_id = await get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        if not await CaseService.can_user_view_case(case_id, db_user_id, schema_name=schema_name):
            logger.warning(f"Access denied for remove_citizen_share: user={db_user_id[:8]}, case={case_id[:8]}")
            raise NotFoundError(CASE_NOT_FOUND_ERROR)

        if not await can_user_manage_citizen_shares(case_id, db_user_id, schema_name=schema_name):
            logger.warning(f"Edit denied for remove_citizen_share: user={db_user_id[:8]}, case={case_id[:8]}")
            raise AuthorizationError("Sin permisos para modificar los ciudadanos compartidos de este expediente")

        await unshare_case_from_citizen(
            case_id=case_id,
            citizen_id=citizen_id,
            removed_by=db_user_id,
            schema_name=schema_name,
        )
        return {
            "success": True,
            "data": {"case_id": case_id, "citizen_id": citizen_id},
            "message": "Ciudadano quitado del expediente",
        }
    except Exception as exc:
        logger.error(f"Error quitando ciudadano {citizen_id} del caso {case_id}: {exc}")
        raise exception_to_http_exception(exc)
