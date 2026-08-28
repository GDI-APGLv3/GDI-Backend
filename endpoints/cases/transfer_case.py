
from fastapi import APIRouter, Depends, Path, Request, HTTPException
from pydantic import BaseModel, Field, model_validator
from typing import Optional, List
from auth import get_current_user
from models.schemas import AuthenticatedUser
from services.case_service import CaseService
from services.cases.transfer_document_creator import create_transfer_document
from shared.exceptions import exception_to_http_exception
from shared.utils import get_authenticated_user
from shared.dependencies import get_tenant_schema
from config.constants import (
    CLOSE_ASSIGNMENT_SUCCESS, AVAILABLE_SECTORS_SUCCESS,
    SECTOR_USERS_SUCCESS, TRANSFER_DOCUMENT_CREATION_ERROR
)
from shared.logging import get_logger
logger = get_logger(__name__)

router = APIRouter(tags=["expedientes"])


class TransferCaseRequest(BaseModel):
    target_sector_id: str = Field(..., description="ID del sector destino", example="684c7cde-03dd-4e0f-be84-38905e6c7b51")
    reason: str = Field(..., min_length=5, max_length=500, description="Motivo de la transferencia", example="Transferencia por competencia")
    transfer_ownership: bool = Field(True, description="Si true, transfiere propiedad. Si false, solo asigna tarea", example=True)
    assigned_user_id: Optional[str] = Field(default=None, description="ID del usuario específico asignado (opcional). Dejar vacío o null si no se asigna a nadie específico.")
    create_official_doc: bool = Field(False, description="Si true, genera documento oficial automáticamente", example=False)


class AssignTaskRequest(BaseModel):
    target_sector_id: str = Field(..., description="ID del sector que debe realizar la tarea", example="684c7cde-03dd-4e0f-be84-38905e6c7b51")
    reason: str = Field(..., min_length=5, max_length=500, description="Descripción de la tarea solicitada", example="Asignación de tarea")
    assigned_user_id: Optional[str] = Field(default=None, description="ID del usuario específico asignado (opcional). Dejar vacío o null si no se asigna a nadie específico.")
    create_official_doc: bool = Field(False, description="Si true, genera documento oficial PV automáticamente", example=False)


class CloseAssignmentRequest(BaseModel):
    movement_id: Optional[str] = Field(default=None, description="ID del movimiento a cerrar (o usar assignment_id)", example="550e8400-e29b-41d4-a716-446655440000")
    assignment_id: Optional[str] = Field(default=None, description="Alias de movement_id (GDI-119): el mismo case_movements.id devuelto por POST /assign", example="550e8400-e29b-41d4-a716-446655440000")
    reason: str = Field(..., min_length=5, max_length=500, description="Razón del cierre", example="Tarea completada")
    create_official_doc: bool = Field(False, description="Si true, genera documento PV de cierre aunque no haya PV de apertura")

    @model_validator(mode="after")
    def _require_movement_or_assignment_id(self):
        if not self.movement_id and not self.assignment_id:
            raise ValueError("movement_id (o assignment_id) es requerido")
        if not self.movement_id:
            self.movement_id = self.assignment_id
        return self


class OfficialDocumentInfo(BaseModel):
    document_id: str = Field(..., example="doc-123")
    official_number: str = Field(..., example="PV-2024-001")
    message: str = Field(..., example="Documento creado")


class TransferResponseData(BaseModel):
    movement_id: str = Field(..., example="mov-123")
    case_number: str = Field(..., example="EXP-2024-001-SMG")
    action_type: str = Field(..., example="transferido")
    target_sector: str = Field(..., example="SEC")
    target_department: str = Field(..., example="Secretaría General")
    transferred_by: str = Field(..., example="Juan Pérez")
    assigned_user: Optional[str] = Field(None, example="María López")
    official_document: Optional[OfficialDocumentInfo] = None


class TransferResponse(BaseModel):
    success: bool = Field(True, example=True)
    data: TransferResponseData
    message: str = Field(..., example="Expediente transferido exitosamente")


class CloseAssignmentData(BaseModel):
    movement_id: str = Field(..., example="mov-123")
    assignment_id: str = Field(..., example="mov-123", description="Alias de movement_id (GDI-119), simétrico al de POST /assign")
    case_id: str = Field(..., example="case-123")
    movement_type: str = Field(..., example="assignment")
    closing_reason: str = Field(..., example="Tarea completada")
    official_document: Optional[OfficialDocumentInfo] = None


class CloseAssignmentResponse(BaseModel):
    success: bool = Field(True, example=True)
    data: CloseAssignmentData
    message: str = Field(..., example="Asignación cerrada exitosamente")


class SectorItem(BaseModel):
    sector_id: str = Field(..., example="sector-123")
    sector_name: str = Field(..., example="SEC")
    department_name: str = Field(..., example="Secretaría General")
    department_acronym: str = Field(..., example="ADGEN")
    user_count: int = Field(..., example=5)
    display_name: str = Field(..., example="SEC - Secretaría General")


class AvailableSectorsData(BaseModel):
    sectors: List[SectorItem]
    total: int = Field(..., example=10)


class AvailableSectorsResponse(BaseModel):
    success: bool = Field(True, example=True)
    data: AvailableSectorsData
    message: str = Field(..., example="Sectores disponibles obtenidos exitosamente")


class UserItem(BaseModel):
    user_id: str = Field(..., example="user-123")
    full_name: str = Field(..., example="Juan Pérez")


class SectorUsersData(BaseModel):
    users: List[UserItem]
    total: int = Field(..., example=3)


class SectorUsersResponse(BaseModel):
    success: bool = Field(True, example=True)
    data: SectorUsersData
    message: str = Field(..., example="Usuarios del sector obtenidos exitosamente")


class AssignResponseData(BaseModel):
    movement_id: str = Field(..., example="mov-123", description="Alias legacy de assignment_id (GDI-119), mismo case_movements.id — para clientes que encadenan con POST /close-assign")
    assignment_id: str = Field(..., example="mov-123", description="ID del case_movements de asignación (nuevo o reutilizado)")
    task_id: str = Field(..., example="task-456", description="ID de la tarea creada en case_assignment_tasks")
    sector_acronym: str = Field(..., example="DPTO#SEC", description="Acronimo DPTO#SECTOR del sector asignado")
    department_name: str = Field(..., example="Secretaría General")
    is_new_assignment: bool = Field(..., description="True si se creó una nueva asignación; False si el sector ya tenía una activa")
    official_document: Optional[OfficialDocumentInfo] = None


class AssignResponse(BaseModel):
    success: bool = Field(True, example=True)
    data: AssignResponseData
    message: str = Field(..., example="Tarea asignada exitosamente")


PV_EMISSION_WARNING = (
    "No se pudo emitir el documento oficial (PV): reintentá desde el expediente."
)


async def _emit_and_link_pv(
    *,
    case_id: str,
    case_number: str,
    movement_type: str,
    movement_reason: str,
    requesting_sector_id: str,
    receiving_sector_id: str,
    user_id: str,
    user_sector_id: str,
    movement_id: Optional[str] = None,
    schema_name: str,
) -> Optional[dict]:
    try:
        document_result = await create_transfer_document(
            case_id=case_id,
            case_number=case_number,
            movement_type=movement_type,
            movement_reason=movement_reason,
            requesting_sector_id=requesting_sector_id,
            receiving_sector_id=receiving_sector_id,
            user_id=user_id,
            connection=None,
            schema_name=schema_name
        )

        link_result = await CaseService.link_official_document(
            case_id=case_id,
            official_document_id=document_result['document_id'],
            linking_user_id=user_id,
            user_sector_id=user_sector_id,
            system_generated=True,
            schema_name=schema_name
        )
        logger.info(
            f"Official document {document_result['official_number']} linked "
            f"with order_number={link_result['order_number']}"
        )

        if movement_id:
            from database import execute
            await execute(
                """
                UPDATE case_movements
                SET supporting_document_id = $1
                WHERE id = $2 AND supporting_document_id IS NULL
                """,
                document_result['document_id'], movement_id,
                schema_name=schema_name
            )

        return document_result

    except Exception as e:
        logger.error(
            f"GDI-318: operación completada pero falló la emisión del PV — "
            f"case_id={case_id}, movement_id={movement_id}, "
            f"movement_type={movement_type}: {str(e)}"
        )
        return None


@router.post("/{case_id}/transfer", response_model=TransferResponse)
async def transfer_case(
    request: Request,
    body: TransferCaseRequest,
    case_id: str = Path(..., description="ID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Transferir expediente a otro sector o asignar tarea.

    **Parámetros:**
    - **target_sector_id**: Sector que recibirá el expediente
    - **reason**: Motivo detallado de la transferencia/asignación
    - **transfer_ownership**: Si transfiere propiedad (true) o solo asigna tarea (false)
    - **assigned_user_id**: Usuario específico asignado (opcional)
    - **create_official_doc**: Genera documento oficial automáticamente

    **Permisos:**
    - Solo el sector administrador puede transferir propiedad
    - Transferir propiedad cambia el owner_sector_id del expediente
    """
    try:
        logger.info(f"Transfer request: case={case_id}, target={body.target_sector_id}, ownership={body.transfer_ownership}")

        db_user_id = await get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        if not body.transfer_ownership:
            from services.cases.tasks import ensure_assignment_and_create_task
            from database import fetch_all

            case_rows = await fetch_all(
                "SELECT case_number FROM cases WHERE id = $1",
                case_id, schema_name=schema_name
            )
            case_number = case_rows[0]['case_number'] if case_rows else ""

            user_sector_id = None
            if body.create_official_doc:
                user_info = await fetch_all(
                    "SELECT sector_id FROM users WHERE id = $1",
                    db_user_id, schema_name=schema_name
                )
                if not case_number or not user_info:
                    from shared.exceptions import BusinessLogicError
                    raise BusinessLogicError(
                        f"{TRANSFER_DOCUMENT_CREATION_ERROR}: expediente o usuario no encontrado"
                    )
                user_sector_id = str(user_info[0]['sector_id'])

            result = await ensure_assignment_and_create_task(
                case_id=case_id,
                target_sector_id=body.target_sector_id,
                reason=body.reason,
                user_id=db_user_id,
                assigned_user_id=body.assigned_user_id,
                create_official_doc=False,
                supporting_document_id=None,
                schema_name=schema_name,
            )

            document_result = None
            if user_sector_id:
                document_result = await _emit_and_link_pv(
                    case_id=case_id,
                    case_number=case_number,
                    movement_type="Asignación",
                    movement_reason=body.reason.strip(),
                    requesting_sector_id=user_sector_id,
                    receiving_sector_id=body.target_sector_id,
                    user_id=db_user_id,
                    user_sector_id=user_sector_id,
                    movement_id=result["assignment_id"],
                    schema_name=schema_name
                )

            message = (
                f"Tarea asignada a {result['sector_acronym']} ({result['department_name']}) — "
                + ("nueva asignación creada" if result["is_new_assignment"] else "tarea agregada a asignación existente")
                + "."
            )
            if document_result:
                message += f" Documento oficial: {document_result['official_number']}."
            elif body.create_official_doc:
                message += f" {PV_EMISSION_WARNING}"

            response_data = {
                "movement_id": result["assignment_id"],
                "case_number": case_number,
                "action_type": "asignado",
                "target_sector": result["sector_acronym"],
                "target_department": result["department_name"],
                "transferred_by": "",
                "assigned_user": None,
            }
            if document_result:
                response_data["official_document"] = {
                    "document_id": document_result['document_id'],
                    "official_number": document_result['official_number'],
                    "message": document_result.get('message', "Documento creado"),
                }

            logger.info(f"Transfer(assign) completed via Opción C: assignment={result['assignment_id']}, task={result['task_id']}")
            return {"success": True, "data": response_data, "message": message}

        pv_context = None
        if body.create_official_doc:
            from database import fetch_all
            case_query = "SELECT case_number, owner_sector_id FROM cases WHERE id = $1"
            case_info = await fetch_all(case_query, case_id, schema_name=schema_name)

            user_query = "SELECT sector_id FROM users WHERE id = $1"
            user_info = await fetch_all(user_query, db_user_id, schema_name=schema_name)

            if not case_info or not user_info:
                from shared.exceptions import BusinessLogicError
                raise BusinessLogicError(
                    f"{TRANSFER_DOCUMENT_CREATION_ERROR}: expediente o usuario no encontrado"
                )

            pv_context = {
                "case_number": case_info[0]['case_number'],
                "user_sector_id": str(user_info[0]['sector_id']),
            }

        result = await CaseService.transfer_case(
            case_id=case_id,
            target_sector_id=body.target_sector_id,
            reason=body.reason,
            user_id=db_user_id,
            transfer_ownership=True,
            assigned_user_id=body.assigned_user_id,
            supporting_document_id=None,
            schema_name=schema_name
        )

        document_result = None
        if pv_context:
            document_result = await _emit_and_link_pv(
                case_id=case_id,
                case_number=pv_context["case_number"],
                movement_type="Transferencia",
                movement_reason=body.reason.strip(),
                requesting_sector_id=pv_context["user_sector_id"],
                receiving_sector_id=body.target_sector_id,
                user_id=db_user_id,
                user_sector_id=pv_context["user_sector_id"],
                movement_id=result["movement_id"],
                schema_name=schema_name
            )

        if document_result:
            result["official_document"] = {
                "document_id": document_result['document_id'],
                "official_number": document_result['official_number'],
                "message": document_result['message']
            }

        message = f"Expediente transferido exitosamente a {result['target_sector']} - {result['target_department']}"
        if document_result:
            message += f". Documento oficial: {document_result['official_number']}"
        elif pv_context:
            message += f". {PV_EMISSION_WARNING}"

        logger.info(f"Transfer completed successfully: movement_id={result['movement_id']}")

        return {
            "success": True,
            "data": result,
            "message": message
        }

    except Exception as e:
        logger.error(f"Error in transfer_case endpoint: {str(e)}")
        raise exception_to_http_exception(e)


@router.post("/{case_id}/assign", response_model=AssignResponse)
async def assign_task(
    request: Request,
    body: AssignTaskRequest,
    case_id: str = Path(..., description="ID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Asignar tarea a un sector (Opción C — Hito 3).

    **Comportamiento idempotente sobre el assignment:**
    - Si el sector **no tiene** asignación activa → crea `case_movements type='assignment'`
      **y** una tarea (`case_assignment_tasks`) en 1 transacción.
      Devuelve `is_new_assignment: true`.
    - Si el sector **ya tiene** asignación activa → **no** rechaza; solo agrega una
      tarea nueva bajo la asignación existente.
      Devuelve `is_new_assignment: false`.

    **Parámetros:**
    - **target_sector_id**: Sector que recibirá la tarea
    - **reason**: Descripción de la tarea (min 5, max 500)
    - **assigned_user_id**: Responsable específico dentro del sector (opcional)
    - **create_official_doc**: Genera documento oficial PV (opcional)

    **Permisos:** admin del expediente o sectores con `can_edit`.
    """
    try:
        logger.info(f"Assign task request (Opción C): case={case_id}, target={body.target_sector_id}")

        db_user_id = await get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        pv_context = None
        if body.create_official_doc:
            from database import fetch_all
            case_query = "SELECT case_number, owner_sector_id FROM cases WHERE id = $1"
            case_info = await fetch_all(case_query, case_id, schema_name=schema_name)

            user_query = "SELECT sector_id FROM users WHERE id = $1"
            user_info = await fetch_all(user_query, db_user_id, schema_name=schema_name)

            if not case_info or not user_info:
                from shared.exceptions import BusinessLogicError
                raise BusinessLogicError(
                    f"{TRANSFER_DOCUMENT_CREATION_ERROR}: expediente o usuario no encontrado"
                )

            pv_context = {
                "case_number": case_info[0]['case_number'],
                "user_sector_id": str(user_info[0]['sector_id']),
            }

        from services.cases.tasks import ensure_assignment_and_create_task
        result = await ensure_assignment_and_create_task(
            case_id=case_id,
            target_sector_id=body.target_sector_id,
            reason=body.reason,
            user_id=db_user_id,
            assigned_user_id=body.assigned_user_id,
            create_official_doc=False,
            supporting_document_id=None,
            schema_name=schema_name,
        )

        document_result = None
        if pv_context:
            document_result = await _emit_and_link_pv(
                case_id=case_id,
                case_number=pv_context["case_number"],
                movement_type="Asignación",
                movement_reason=body.reason.strip(),
                requesting_sector_id=pv_context["user_sector_id"],
                receiving_sector_id=body.target_sector_id,
                user_id=db_user_id,
                user_sector_id=pv_context["user_sector_id"],
                movement_id=result["assignment_id"],
                schema_name=schema_name
            )

        result["movement_id"] = result["assignment_id"]

        if document_result:
            result["official_document"] = {
                "document_id": document_result['document_id'],
                "official_number": document_result['official_number'],
                "message": document_result['message'],
            }

        action = "nueva asignación creada" if result["is_new_assignment"] else "tarea agregada a asignación existente"
        message = (
            f"Tarea asignada a {result['sector_acronym']} "
            f"({result['department_name']}) — {action}."
        )
        if document_result:
            message += f" Documento oficial: {document_result['official_number']}."
        elif pv_context:
            message += f" {PV_EMISSION_WARNING}"

        logger.info(
            f"assign_task OK: case={case_id}, assignment={result['assignment_id']}, "
            f"task={result['task_id']}, is_new={result['is_new_assignment']}"
        )

        return {
            "success": True,
            "data": result,
            "message": message,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in assign_task endpoint: {str(e)}")
        raise exception_to_http_exception(e)


@router.post("/{case_id}/close-assign", response_model=CloseAssignmentResponse)
async def close_assignment(
    request: Request,
    body: CloseAssignmentRequest,
    case_id: str = Path(..., description="ID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Cerrar asignación de expediente.

    **Parámetros:**
    - **movement_id** (o **assignment_id**, alias GDI-119 — mismo case_movements.id
      devuelto por POST /assign): ID del movimiento a cerrar
    - **reason**: Razón del cierre

    **Funcionamiento:**
    - Establece closed_at = NOW()
    - Establece closing_reason = reason
    - Cambia is_active = false

    **Permisos:**
    - Sector administrador del expediente (último movimiento cerrado creation/transfer)
    - Sector asignado del movimiento (assigned_sector_id)
    """
    try:
        logger.info(f"Close assignment request: case={case_id}, movement={body.movement_id}")

        db_user_id = await get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        from database import fetch_all

        mov_query = "SELECT supporting_document_id, assigned_sector_id FROM case_movements WHERE id = $1 AND case_id = $2"
        mov_check = await fetch_all(mov_query, body.movement_id, case_id, schema_name=schema_name)
        has_supporting_doc = mov_check and mov_check[0]['supporting_document_id'] is not None

        pv_context = None
        if has_supporting_doc or body.create_official_doc:
            case_query = "SELECT case_number FROM cases WHERE id = $1"
            case_info = await fetch_all(case_query, case_id, schema_name=schema_name)

            user_query = "SELECT sector_id FROM users WHERE id = $1"
            user_info = await fetch_all(user_query, db_user_id, schema_name=schema_name)

            if case_info and user_info:
                assigned_sector = str(mov_check[0]['assigned_sector_id']) if mov_check[0]['assigned_sector_id'] else str(user_info[0]['sector_id'])
                pv_context = {
                    "case_number": case_info[0]['case_number'],
                    "assigned_sector": assigned_sector,
                    "user_sector_id": str(user_info[0]['sector_id']),
                }

        result = await CaseService.close_assignment(
            case_id=case_id,
            movement_id=body.movement_id,
            reason=body.reason,
            user_id=db_user_id,
            schema_name=schema_name
        )

        document_result = None
        if pv_context:
            document_result = await _emit_and_link_pv(
                case_id=case_id,
                case_number=pv_context["case_number"],
                movement_type="Cierre de Asignación",
                movement_reason=body.reason.strip(),
                requesting_sector_id=pv_context["assigned_sector"],
                receiving_sector_id=pv_context["assigned_sector"],
                user_id=db_user_id,
                user_sector_id=pv_context["user_sector_id"],
                movement_id=None,
                schema_name=schema_name
            )

        logger.info(f"Assignment closed successfully: movement_id={body.movement_id}")

        response_data = {
            "movement_id": result["movement_id"],
            "assignment_id": result["movement_id"],
            "case_id": result["case_id"],
            "movement_type": result["movement_type"],
            "closing_reason": result["closing_reason"]
        }

        if document_result:
            response_data["official_document"] = {
                "document_id": document_result['document_id'],
                "official_number": document_result['official_number'],
                "message": document_result['message']
            }

        message = CLOSE_ASSIGNMENT_SUCCESS
        if document_result:
            message += f". Documento oficial: {document_result['official_number']}"
        elif pv_context:
            message += f". {PV_EMISSION_WARNING}"

        return {
            "success": True,
            "data": response_data,
            "message": message
        }
        
    except Exception as e:
        logger.error(f"Error in close_assignment endpoint: {str(e)}")
        raise exception_to_http_exception(e)


@router.get("/{case_id}/available-sectors", response_model=AvailableSectorsResponse)
async def get_available_sectors_for_transfer(
    request: Request,
    case_id: str = Path(..., description="ID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Obtener sectores disponibles para transferencia o asignación.

    **Retorna:**
    - Sectores del mismo municipio que pueden recibir el expediente
    - Excluye el sector propietario actual
    - Incluye información de usuarios activos por sector

    **Permisos:**
    - Usuario debe tener acceso de visualización al expediente
    """
    try:
        logger.info(f"Fetching available sectors for case: {case_id}")

        db_user_id = await get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        sectors = await CaseService.get_available_sectors_for_transfer(case_id, db_user_id, schema_name=schema_name)
        
        logger.info(f"Found {len(sectors)} available sectors")
        
        return {
            "success": True,
            "data": {
                "sectors": sectors,
                "total": len(sectors)
            },
            "message": f"{AVAILABLE_SECTORS_SUCCESS}. {len(sectors)} sectores disponibles"
        }
        
    except Exception as e:
        logger.error(f"Error in get_available_sectors endpoint: {str(e)}")
        raise exception_to_http_exception(e)


@router.get("/sectors/{sector_id}/users", response_model=SectorUsersResponse)
async def get_sector_users(
    request: Request,
    sector_id: str = Path(..., description="ID del sector"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Obtener usuarios activos de un sector específico.

    **Retorna:**
    - Lista de usuarios activos (estado=1) del sector
    - Ordenados alfabéticamente por nombre completo

    **Uso:**
    - Útil para asignaciones específicas de usuario
    - Permite seleccionar un responsable concreto dentro del sector
    """
    try:
        logger.info(f"Fetching users for sector: {sector_id}")

        await get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        users = await CaseService.get_sector_users(sector_id, schema_name=schema_name)
        
        logger.info(f"Found {len(users)} users in sector")
        
        return {
            "success": True,
            "data": {
                "users": users,
                "total": len(users)
            },
            "message": f"{SECTOR_USERS_SUCCESS}. {len(users)} usuarios encontrados"
        }
        
    except Exception as e:
        logger.error(f"Error in get_sector_users endpoint: {str(e)}")
        raise exception_to_http_exception(e)

