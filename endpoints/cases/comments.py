
from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, Field
from typing import Optional, Literal

from auth import get_current_user
from models.schemas import AuthenticatedUser
from shared.exceptions import NotFoundError, AuthorizationError, exception_to_http_exception
from shared.utils import get_authenticated_user
from shared.dependencies import get_tenant_schema
from services.case_service import CaseService
from config.constants import CASE_NOT_FOUND_ERROR
from services.cases.comments import create_case_comment, complete_case_task
from shared.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["expedientes"])


class CreateCommentRequest(BaseModel):
    type: Literal["comment", "task"] = Field(
        "comment",
        description="'comment' = mensaje libre (is_active=False). 'task' = tarea pendiente (is_active=True).",
    )
    text: str = Field(..., min_length=1, max_length=1000, description="Texto del comentario o tarea")
    mentioned_user_id: Optional[str] = Field(
        None, description="UUID del usuario mencionado (@mención). Se guarda en assigned_user_id."
    )


class CreateCommentResponse(BaseModel):
    success: bool = True
    data: dict
    message: str


class CompleteTaskRequest(BaseModel):
    closing_reason: str = Field(
        "Tarea completada", min_length=1, max_length=200,
        description="Motivo del cierre / nota de completado",
    )


class CompleteTaskResponse(BaseModel):
    success: bool = True
    data: dict
    message: str


@router.post("/{case_id}/comments", response_model=CreateCommentResponse)
async def create_comment(
    request: Request,
    body: CreateCommentRequest,
    case_id: str = Path(..., description="ID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    """Crea un comentario (chat) o una tarea en el expediente. Requiere permiso de edición."""
    try:
        db_user_id = await get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        if not await CaseService.can_user_view_case(case_id, db_user_id, schema_name=schema_name):
            logger.warning(f"Access denied for create_comment: user={db_user_id[:8]}, case={case_id[:8]}")
            raise NotFoundError(CASE_NOT_FOUND_ERROR)

        if not await CaseService.can_user_edit_case(case_id, db_user_id, schema_name=schema_name):
            logger.warning(f"Edit denied for create_comment: user={db_user_id[:8]}, case={case_id[:8]}")
            raise AuthorizationError("Sin permisos para comentar en este expediente")

        result = await create_case_comment(
            case_id=case_id,
            user_id=db_user_id,
            movement_type=body.type,
            text=body.text,
            mentioned_user_id=body.mentioned_user_id,
            schema_name=schema_name,
        )
        return {"success": True, "data": result, "message": "Comentario creado correctamente"}
    except Exception as exc:
        logger.error(f"Error creando comentario para caso {case_id}: {exc}")
        raise exception_to_http_exception(exc)


@router.post("/{case_id}/tasks/{movement_id}/complete", response_model=CompleteTaskResponse)
async def complete_task(
    request: Request,
    body: CompleteTaskRequest,
    case_id: str = Path(..., description="ID del expediente"),
    movement_id: str = Path(..., description="ID del movimiento (tarea)"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    """Completa una tarea (is_active=False). Requiere permiso de edición."""
    try:
        db_user_id = await get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        if not await CaseService.can_user_view_case(case_id, db_user_id, schema_name=schema_name):
            logger.warning(f"Access denied for complete_task: user={db_user_id[:8]}, case={case_id[:8]}")
            raise NotFoundError(CASE_NOT_FOUND_ERROR)

        if not await CaseService.can_user_edit_case(case_id, db_user_id, schema_name=schema_name):
            logger.warning(f"Edit denied for complete_task: user={db_user_id[:8]}, case={case_id[:8]}")
            raise AuthorizationError("Sin permisos para completar tareas en este expediente")

        result = await complete_case_task(
            case_id=case_id,
            movement_id=movement_id,
            user_id=db_user_id,
            closing_reason=body.closing_reason,
            schema_name=schema_name,
        )
        return {"success": True, "data": result, "message": "Tarea completada correctamente"}
    except Exception as exc:
        logger.error(f"Error completando tarea {movement_id} del caso {case_id}: {exc}")
        raise exception_to_http_exception(exc)
