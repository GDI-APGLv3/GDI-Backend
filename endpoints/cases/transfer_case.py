"""
Endpoints para transferir y asignar expedientes
POST /cases/{case_id}/transfer - Transferir expediente a otro sector
POST /cases/{case_id}/assign - Asignar tarea a otro sector
POST /cases/{case_id}/close-assign - Cerrar asignación
GET /cases/{case_id}/available-sectors - Sectores disponibles para transferencia
GET /sectors/{sector_id}/users - Usuarios de un sector
"""

from fastapi import APIRouter, Depends, Path, Request, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List
from auth import get_current_user
from models.schemas import AuthenticatedUser
from services.case_service import CaseService
from services.cases.transfer_document_creator import create_transfer_document
from shared.exceptions import exception_to_http_exception
from shared.utils import get_authenticated_user
from shared.dependencies import get_tenant_schema
from config.constants import (
    TRANSFER_SUCCESS_MESSAGE, ASSIGN_SUCCESS_MESSAGE,
    CLOSE_ASSIGNMENT_SUCCESS, AVAILABLE_SECTORS_SUCCESS,
    SECTOR_USERS_SUCCESS, TRANSFER_DOCUMENT_CREATION_ERROR
)
from shared.logging import get_logger
logger = get_logger(__name__)

# Inicializar router
router = APIRouter(tags=["expedientes"])


# ============================================================================
# REQUEST MODELS
# ============================================================================

class TransferCaseRequest(BaseModel):
    """Modelo para transferencia de expediente"""
    target_sector_id: str = Field(..., description="ID del sector destino", example="684c7cde-03dd-4e0f-be84-38905e6c7b51")
    reason: str = Field(..., min_length=5, max_length=500, description="Motivo de la transferencia", example="Transferencia por competencia")
    transfer_ownership: bool = Field(True, description="Si true, transfiere propiedad. Si false, solo asigna tarea", example=True)
    assigned_user_id: Optional[str] = Field(default=None, description="ID del usuario específico asignado (opcional). Dejar vacío o null si no se asigna a nadie específico.")
    create_official_doc: bool = Field(False, description="Si true, genera documento oficial automáticamente", example=False)


class AssignTaskRequest(BaseModel):
    """Modelo para asignación de tarea"""
    target_sector_id: str = Field(..., description="ID del sector que debe realizar la tarea", example="684c7cde-03dd-4e0f-be84-38905e6c7b51")
    reason: str = Field(..., min_length=5, max_length=500, description="Descripción de la tarea solicitada", example="Asignación de tarea")
    assigned_user_id: Optional[str] = Field(default=None, description="ID del usuario específico asignado (opcional). Dejar vacío o null si no se asigna a nadie específico.")
    create_official_doc: bool = Field(False, description="Si true, genera documento oficial PV automáticamente", example=False)


class CloseAssignmentRequest(BaseModel):
    """Modelo para cerrar asignación"""
    movement_id: str = Field(..., description="ID del movimiento a cerrar", example="550e8400-e29b-41d4-a716-446655440000")
    reason: str = Field(..., min_length=5, max_length=500, description="Razón del cierre", example="Tarea completada")


# ============================================================================
# RESPONSE MODELS
# ============================================================================

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


# ============================================================================
# ENDPOINTS
# ============================================================================

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

        # Validar usuario autenticado
        db_user_id = await get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        # === VALIDACIÓN TEMPRANA DE ASIGNACIÓN DUPLICADA ===
        # Solo aplica para asignaciones (transfer_ownership=False)
        if not body.transfer_ownership:
            from database import fetch_all
            from services.case_queries import check_duplicate_assignment_query

            duplicate_check = await fetch_all(
                check_duplicate_assignment_query(),
                case_id, body.target_sector_id,
                schema_name=schema_name
            )
            if duplicate_check:
                logger.warning(
                    f"Duplicate assignment blocked: case={case_id}, "
                    f"sector={body.target_sector_id}"
                )
                from shared.exceptions import ValidationError
                raise ValidationError(
                    "Ya existe una asignación activa a este sector. "
                    "Cierre la asignación existente antes de crear una nueva."
                )

        # Generar documento oficial si se solicita
        document_result = None
        if body.create_official_doc:
            logger.info(f"Creating official document for transfer")
            movement_type_text = "Transferencia" if body.transfer_ownership else "Asignación"

            try:
                # Obtener case_number y user_sector primero
                from database import fetch_all
                case_query = "SELECT case_number, owner_sector_id FROM cases WHERE id = $1"
                case_info = await fetch_all(case_query, case_id, schema_name=schema_name)

                user_query = "SELECT sector_id FROM users WHERE id = $1"
                user_info = await fetch_all(user_query, db_user_id, schema_name=schema_name)

                if not case_info or not user_info:
                    raise ValueError("Case or user not found for document creation")

                document_result = await create_transfer_document(
                    case_id=case_id,
                    case_number=case_info[0]['case_number'],
                    movement_type=movement_type_text,
                    movement_reason=body.reason.strip(),
                    requesting_sector_id=str(user_info[0]['sector_id']),
                    receiving_sector_id=body.target_sector_id,
                    user_id=db_user_id,
                    connection=None,
                    schema_name=schema_name
                )

                logger.info(f"Official document created: {document_result['official_number']}")

                # Vincular documento al expediente
                link_result = await CaseService.link_official_document(
                    case_id=case_id,
                    official_document_id=document_result['document_id'],
                    linking_user_id=db_user_id,
                    user_sector_id=str(user_info[0]['sector_id']),
                    schema_name=schema_name
                )

                logger.info(f"Document linked with order_number={link_result['order_number']}")
                
            except Exception as e:
                logger.error(f"Error creating official document: {str(e)}")
                from shared.exceptions import BusinessLogicError
                raise BusinessLogicError(f"{TRANSFER_DOCUMENT_CREATION_ERROR}: {str(e)}")
        
        # Ejecutar transferencia
        result = await CaseService.transfer_case(
            case_id=case_id,
            target_sector_id=body.target_sector_id,
            reason=body.reason,
            user_id=db_user_id,
            transfer_ownership=body.transfer_ownership,
            assigned_user_id=body.assigned_user_id,
            supporting_document_id=document_result['document_id'] if document_result else None,
            schema_name=schema_name
        )

        # Agregar info del documento oficial si fue creado
        if document_result:
            result["official_document"] = {
                "document_id": document_result['document_id'],
                "official_number": document_result['official_number'],
                "message": document_result['message']
            }

        action_type = "transferido" if body.transfer_ownership else "asignado"
        message = f"Expediente {action_type} exitosamente a {result['target_sector']} - {result['target_department']}"
        
        if document_result:
            message += f". Documento oficial: {document_result['official_number']}"
        
        logger.info(f"Transfer completed successfully: movement_id={result['movement_id']}")
        
        return {
            "success": True,
            "data": result,
            "message": message
        }
        
    except Exception as e:
        logger.error(f"Error in transfer_case endpoint: {str(e)}")
        raise exception_to_http_exception(e)


@router.post("/{case_id}/assign", response_model=TransferResponse)
async def assign_task(
    request: Request,
    body: AssignTaskRequest,
    case_id: str = Path(..., description="ID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Asignar tarea específica sin transferir propiedad.

    **Parámetros:**
    - **target_sector_id**: Sector que debe realizar la tarea
    - **reason**: Descripción detallada de la tarea
    - **assigned_user_id**: Usuario específico responsable (opcional)
    - **create_official_doc**: Genera documento oficial PV automáticamente

    **Funcionamiento:**
    - Crea un movimiento de asignación sin cambiar el propietario
    - El sector asignado puede trabajar en el expediente
    - El propietario mantiene control del expediente
    """
    try:
        logger.info(f"Assign task request: case={case_id}, target={body.target_sector_id}")

        # Reutilizar endpoint de transferencia con ownership=False
        transfer_request = TransferCaseRequest(
            target_sector_id=body.target_sector_id,
            reason=body.reason,
            transfer_ownership=False,
            assigned_user_id=body.assigned_user_id,
            create_official_doc=body.create_official_doc
        )

        return await transfer_case(request, transfer_request, case_id, current_user, schema_name)

    except HTTPException:
        # Re-lanzar HTTPException directamente (ya viene convertida de transfer_case)
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
    - **movement_id**: ID del movimiento a cerrar
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

        # Validar usuario autenticado
        db_user_id = await get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        # Verificar si la asignación tiene PV respaldatorio ANTES de cerrar
        # (después de cerrar, el usuario pierde permisos de asignación activa)
        document_result = None
        from database import fetch_all

        mov_query = "SELECT supporting_document_id, assigned_sector_id FROM case_movements WHERE id = $1 AND case_id = $2"
        mov_check = await fetch_all(mov_query, body.movement_id, case_id, schema_name=schema_name)
        has_supporting_doc = mov_check and mov_check[0]['supporting_document_id'] is not None

        if has_supporting_doc:
            try:
                case_query = "SELECT case_number FROM cases WHERE id = $1"
                case_info = await fetch_all(case_query, case_id, schema_name=schema_name)

                user_query = "SELECT sector_id FROM users WHERE id = $1"
                user_info = await fetch_all(user_query, db_user_id, schema_name=schema_name)

                if case_info and user_info:
                    assigned_sector = str(mov_check[0]['assigned_sector_id']) if mov_check[0]['assigned_sector_id'] else str(user_info[0]['sector_id'])

                    document_result = await create_transfer_document(
                        case_id=case_id,
                        case_number=case_info[0]['case_number'],
                        movement_type="Cierre de Asignación",
                        movement_reason=body.reason.strip(),
                        requesting_sector_id=assigned_sector,
                        receiving_sector_id=assigned_sector,
                        user_id=db_user_id,
                        schema_name=schema_name
                    )

                    await CaseService.link_official_document(
                        case_id=case_id,
                        official_document_id=document_result['document_id'],
                        linking_user_id=db_user_id,
                        user_sector_id=str(user_info[0]['sector_id']),
                        schema_name=schema_name
                    )

                    logger.info(f"Closing PV created and linked: {document_result['official_number']}")

            except Exception as e:
                logger.error(f"Error creating closing PV document: {str(e)}")
                document_result = None
                # No falla el cierre si el PV falla

        # Cerrar asignación
        result = await CaseService.close_assignment(
            case_id=case_id,
            movement_id=body.movement_id,
            reason=body.reason,
            user_id=db_user_id,
            schema_name=schema_name
        )

        logger.info(f"Assignment closed successfully: movement_id={body.movement_id}")

        # Construir respuesta
        response_data = {
            "movement_id": result["movement_id"],
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

        # Validar usuario autenticado
        db_user_id = await get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        # Obtener sectores disponibles
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

        # Validar usuario autenticado
        await get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        # Obtener usuarios del sector
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

