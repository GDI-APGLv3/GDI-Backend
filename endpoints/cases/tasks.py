
from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, List

from auth import get_current_user
from models.schemas import AuthenticatedUser
from shared.exceptions import exception_to_http_exception, BusinessLogicError, IsLastTaskError
from shared.utils import get_authenticated_user
from shared.dependencies import get_tenant_schema
from services.cases.tasks import (
    close_task,
    update_task,
    get_assignments_with_tasks,
    get_closed_timeline,
    get_assignable_users,
)
from shared.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["expedientes"])


class CloseTaskRequest(BaseModel):
    closing_reason: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=200,
        description=(
            "Motivo del cierre. Obligatorio (min 5 chars) si es la última tarea del sector "
            "(el servidor lo determina y devuelve 409 is_last_task=true si falta)."
        ),
    )
    create_official_doc: bool = Field(
        default=False,
        description=(
            "Si true y la tarea cierra el sector en cascada (assignment_closed=true), "
            "genera un PV de 'Cierre de Asignación'. Ignorado si no es la última tarea."
        ),
    )


class CloseTaskData(BaseModel):
    task_id: str
    closed: bool
    assignment_closed: bool
    official_document: Optional[dict] = None


class CloseTaskResponse(BaseModel):
    success: bool = True
    data: CloseTaskData
    message: str


class IsLastTaskResponse(BaseModel):
    success: bool = False
    is_last_task: bool = True
    message: str = (
        "Es la última tarea abierta del sector. "
        "Incluí closing_reason (min 5 caracteres) para confirmar el cierre del sector."
    )


class UpdateTaskRequest(BaseModel):
    assigned_user_id: Optional[str] = Field(
        default=None,
        description="UUID del nuevo responsable. Null para quitar el responsable.",
    )


class UpdateTaskData(BaseModel):
    task_id: str
    assigned_user_id: Optional[str]


class UpdateTaskResponse(BaseModel):
    success: bool = True
    data: UpdateTaskData
    message: str


class AssignedUserItem(BaseModel):
    id: str
    full_name: str
    profile_picture_url: Optional[str] = None


class TaskItem(BaseModel):
    id: str
    assigned_user: Optional[AssignedUserItem]
    reason: str
    created_at: Optional[str]
    status: str


class SectorInfo(BaseModel):
    id: str
    acronym: str
    color: Optional[str]
    department_name: str
    department_acronym: str


class AssignmentItem(BaseModel):
    id: str
    sector: SectorInfo
    created_at: Optional[str]
    can_close: Optional[bool] = None
    tasks: List[TaskItem]


class AssignmentsData(BaseModel):
    assignments: List[AssignmentItem]
    total: int


class AssignmentsResponse(BaseModel):
    success: bool = True
    data: AssignmentsData
    message: str


class ClosedTaskItem(BaseModel):
    id: str
    assigned_user: Optional[AssignedUserItem]
    reason: str
    created_at: Optional[str]
    closed_at: Optional[str]
    status: str


class CloserUserItem(BaseModel):
    id: str
    full_name: Optional[str]
    profile_picture_url: Optional[str] = None


class ClosedSectorItem(BaseModel):
    id: str
    sector: SectorInfo
    created_at: Optional[str]
    closed_at: Optional[str]
    closing_reason: Optional[str]
    closed_by: Optional[CloserUserItem]
    tasks: List[ClosedTaskItem]


class TransferSectorInfo(BaseModel):
    id: str
    acronym: str
    color: Optional[str] = None
    department_name: Optional[str] = None
    department_acronym: Optional[str] = None


class TransferItem(BaseModel):
    id: str
    from_sector: Optional[TransferSectorInfo]
    to_sector: Optional[TransferSectorInfo]
    reason: Optional[str]
    created_at: Optional[str]
    user: Optional[CloserUserItem]


class CreationItem(BaseModel):
    id: str
    sector: Optional[TransferSectorInfo]
    reason: Optional[str]
    created_at: Optional[str]
    user: Optional[CloserUserItem]


class ClosedTimelineData(BaseModel):
    closed_sectors: List[ClosedSectorItem]
    transfers: List[TransferItem]
    creations: List[CreationItem] = []


class ClosedTimelineResponse(BaseModel):
    success: bool = True
    data: ClosedTimelineData
    message: str


class SectorItem(BaseModel):
    sector_id: str
    acronym: str
    department: str
    can_edit: bool


class AssignableUserItem(BaseModel):
    user_id: str
    full_name: str
    profile_picture_url: Optional[str]
    sectors: List[SectorItem]


class AssignableUsersData(BaseModel):
    users: List[AssignableUserItem]
    total: int


class AssignableUsersResponse(BaseModel):
    success: bool = True
    data: AssignableUsersData
    message: str


@router.post("/{case_id}/tasks/{task_id}/close", response_model=CloseTaskResponse)
async def close_task_endpoint(
    request: Request,
    body: CloseTaskRequest,
    case_id: str = Path(..., description="ID del expediente"),
    task_id: str = Path(..., description="ID de la tarea"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    """
    Cierra una tarea de asignación.

    - Si la tarea tiene otras tareas abiertas en el mismo assignment → solo cierra esa tarea.
    - Si es la **última** tarea abierta del assignment y `closing_reason` no es válido
      (ausente o < 5 chars) → devuelve **409** `{is_last_task: true}` para que el front
      muestre el pop-up de confirmación obligatorio.
    - Si es la última tarea y `closing_reason` es válido → cierra la tarea **y** el
      assignment (is_active=false) en 1 transacción.

    **Permisos:** sector asignado de la tarea o admin del expediente.
    """
    try:
        db_user_id = await get_authenticated_user(
            request.state.tenant_user_id, schema_name=schema_name
        )

        result = await close_task(
            case_id=case_id,
            task_id=task_id,
            user_id=db_user_id,
            closing_reason=body.closing_reason,
            create_official_doc=body.create_official_doc,
            schema_name=schema_name,
        )

        msg = "Tarea cerrada."
        if result.get("assignment_closed"):
            msg = "Tarea cerrada. El sector cerró su intervención en el expediente."
            if result.get("official_document"):
                msg += f" Documento oficial: {result['official_document']['official_number']}."

        return {
            "success": True,
            "data": result,
            "message": msg,
        }

    except IsLastTaskError:
        return JSONResponse(
            status_code=409,
            content=IsLastTaskResponse().model_dump(),
        )
    except BusinessLogicError as exc:
        raise exception_to_http_exception(exc)
    except Exception as exc:
        logger.error(f"Error cerrando tarea {task_id} del expediente {case_id}: {exc}")
        raise exception_to_http_exception(exc)


@router.patch("/{case_id}/tasks/{task_id}", response_model=UpdateTaskResponse)
async def update_task_endpoint(
    request: Request,
    body: UpdateTaskRequest,
    case_id: str = Path(..., description="ID del expediente"),
    task_id: str = Path(..., description="ID de la tarea"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    """
    Reasigna el responsable de una tarea sin cerrarla.

    - `assigned_user_id`: UUID del nuevo responsable, o `null` para quitar el responsable.
    - El usuario debe pertenecer al sector de la tarea.

    **Permisos:** sector asignado de la tarea o admin del expediente.
    """
    try:
        db_user_id = await get_authenticated_user(
            request.state.tenant_user_id, schema_name=schema_name
        )

        result = await update_task(
            case_id=case_id,
            task_id=task_id,
            user_id=db_user_id,
            assigned_user_id=body.assigned_user_id,
            schema_name=schema_name,
        )

        return {
            "success": True,
            "data": result,
            "message": "Responsable de tarea actualizado.",
        }

    except Exception as exc:
        logger.error(f"Error actualizando tarea {task_id} del expediente {case_id}: {exc}")
        raise exception_to_http_exception(exc)


@router.get("/{case_id}/assignments", response_model=AssignmentsResponse)
async def get_assignments_endpoint(
    request: Request,
    case_id: str = Path(..., description="ID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    """
    Devuelve los assignments activos del expediente con sus tareas anidadas.

    Estructura de respuesta:
    ```
    assignments: [
      { id, sector{id,acronym,color,...}, created_at,
        tasks: [{ id, assigned_user|null, reason, created_at, status }] }
    ]
    ```

    **Permisos:** cualquier usuario con acceso de visualización al expediente.
    """
    try:
        db_user_id = await get_authenticated_user(
            request.state.tenant_user_id, schema_name=schema_name
        )

        assignments = await get_assignments_with_tasks(
            case_id=case_id,
            user_id=db_user_id,
            schema_name=schema_name,
        )

        return {
            "success": True,
            "data": {
                "assignments": assignments,
                "total": len(assignments),
            },
            "message": f"{len(assignments)} asignaciones activas.",
        }

    except Exception as exc:
        logger.error(f"Error obteniendo assignments del expediente {case_id}: {exc}")
        raise exception_to_http_exception(exc)


@router.get("/{case_id}/assignments/closed", response_model=ClosedTimelineResponse)
async def get_closed_timeline_endpoint(
    request: Request,
    case_id: str = Path(..., description="ID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    """
    Devuelve los eventos FINALIZADOS del expediente para la línea de tiempo del Panel:

    - `closed_sectors`: sectores cuya intervención ya cerró (con sus tareas cerradas,
      motivo y fecha de cierre, y quién cerró).
    - `transfers`: transferencias del expediente (sector emisor → receptor, motivo,
      fecha y usuario).

    **Permisos:** cualquier usuario con acceso de visualización al expediente.
    """
    try:
        db_user_id = await get_authenticated_user(
            request.state.tenant_user_id, schema_name=schema_name
        )

        data = await get_closed_timeline(
            case_id=case_id,
            user_id=db_user_id,
            schema_name=schema_name,
        )

        n = len(data["closed_sectors"]) + len(data["transfers"]) + len(data.get("creations", []))
        return {
            "success": True,
            "data": data,
            "message": f"{n} eventos finalizados.",
        }

    except Exception as exc:
        logger.error(f"Error obteniendo timeline de finalizados del expediente {case_id}: {exc}")
        raise exception_to_http_exception(exc)


@router.get("/{case_id}/assignable-users", response_model=AssignableUsersResponse)
async def get_assignable_users_endpoint(
    request: Request,
    case_id: str = Path(..., description="ID del expediente"),
    q: str = Query("", description="Texto de búsqueda (min 2 caracteres)"),
    sector_id: Optional[str] = Query(None, description="Filtra usuarios al sector de la tarea"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    """
    Typeahead de usuarios asignables para tareas.

    - `q`: texto de búsqueda sobre nombre, acronimo de sector o departamento.
    - `sector_id`: (opcional) UUID del sector — cuando se indica, devuelve solo
      usuarios que pertenecen a ese sector (sector principal o membresía en
      user_sector_permissions). Replica la regla de validación de update_task,
      eliminando el 400 "El usuario asignado no pertenece al sector de la tarea".
    - Mínimo 2 caracteres (si `q` < 2 devuelve lista vacía sin consultar la BD).
    - Máximo 50 resultados.
    - Cada usuario incluye sus sectores (principal + sectores adicionales con `can_edit`).

    **Permisos:** usuarios con permiso de edición en el expediente.
    """
    try:
        db_user_id = await get_authenticated_user(
            request.state.tenant_user_id, schema_name=schema_name
        )

        users = await get_assignable_users(
            case_id=case_id,
            q=q,
            user_id=db_user_id,
            schema_name=schema_name,
            sector_id=sector_id,
        )

        return {
            "success": True,
            "data": {
                "users": users,
                "total": len(users),
            },
            "message": f"{len(users)} usuarios encontrados.",
        }

    except Exception as exc:
        logger.error(
            f"Error en typeahead de usuarios asignables "
            f"(case={case_id}, q={q!r}): {exc}"
        )
        raise exception_to_http_exception(exc)
