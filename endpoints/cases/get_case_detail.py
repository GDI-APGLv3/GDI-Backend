"""Endpoint para obtener detalles completos de un expediente"""

from shared.logging import get_logger
from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

from auth import get_current_user
from models.schemas import AuthenticatedUser
from services.case_service import CaseService
from shared.exceptions import (
    exception_to_http_exception,
    NotFoundError,
    ValidationError,
    BusinessLogicError
)
from shared.utils import get_authenticated_user
from shared.dependencies import get_tenant_schema
from config.constants import (
    CASE_DETAIL_SUCCESS_MESSAGE,
    CASE_DETAIL_ERROR,
    CASE_NOT_FOUND_ERROR,
    USER_NOT_FOUND_ERROR,
    USER_UNAUTHENTICATED_ERROR
)

logger = get_logger(__name__)
router = APIRouter(tags=["expedientes"])


class SectorInfo(BaseModel):
    """Información de un sector"""
    acronym: str = Field(..., example="SMG#LEGAL")
    department: str = Field(..., example="Secretaría Municipal de Gobierno")


class TemplateInfo(BaseModel):
    """Información de plantilla de expediente"""
    name: str = Field(..., example="Expediente Administrativo")
    acronym: str = Field(..., example="EXP-ADM")


class CaseDetailData(BaseModel):
    """Datos detallados de un expediente"""
    id: str = Field(..., example="550e8400-e29b-41d4-a716-446655440000")
    case_number: str = Field(..., example="EXP-2024-001-SMG")
    reference: str = Field(..., example="Expediente de prueba")
    template: TemplateInfo
    access_reason: str = Field(..., example="ADMINSECTOR", description="Nivel de acceso: ADMINSECTOR, ASSIGNEDSECTOR, VIEW")
    admin_sector: Optional[SectorInfo] = Field(None, example={"acronym": "SMG#LEGAL", "department": "Secretaría Municipal de Gobierno"})
    assigned_sectors: List[SectorInfo] = Field(default_factory=list, example=[
        {"acronym": "SMG#CONTABLE", "department": "Contaduría"},
        {"acronym": "SMG#RRHH", "department": "Recursos Humanos"}
    ])


class CaseDetailResponse(BaseModel):
    """Modelo para respuesta de detalle de expediente"""
    success: bool = Field(..., example=True)
    data: Optional[CaseDetailData]
    message: str = Field(..., example="Detalle del expediente EXP-2024-001-SMG obtenido correctamente")


@router.get("/{case_id}", response_model=CaseDetailResponse)
async def get_case_detail(
    request: Request,
    case_id: str = Path(..., description="ID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
) -> CaseDetailResponse:
    """Obtener detalles completos de un expediente."""
    try:
        # Validar usuario autenticado primero (antes de logging)
        tenant_user_id = getattr(request.state, 'tenant_user_id', None)
        if not tenant_user_id:
            raise ValidationError(USER_UNAUTHENTICATED_ERROR)

        logger.info(f"Get case detail request - Case: {case_id[:8]}, User: {tenant_user_id[:8]}")

        # Obtener y validar usuario
        db_user_id = get_authenticated_user(tenant_user_id, schema_name=schema_name)

        # Obtener detalle del expediente
        case_detail = CaseService.get_case_detail(case_id, db_user_id, schema_name=schema_name)

        if not case_detail:
            logger.warning(f"Case not found or access denied: {case_id[:8]}")
            raise NotFoundError(CASE_NOT_FOUND_ERROR)
        
        logger.info(f"Case detail retrieved successfully - {case_detail['case_number']}")
        
        return CaseDetailResponse(
            success=True,
            data=case_detail,
            message=CASE_DETAIL_SUCCESS_MESSAGE.format(case_number=case_detail['case_number'])
        )
        
    except (ValidationError, NotFoundError, BusinessLogicError) as e:
        logger.error(f"Error in get_case_detail: {str(e)}")
        raise exception_to_http_exception(e)
    except Exception as e:
        logger.error(f"Unexpected error in get_case_detail: {str(e)}", exc_info=True)
        raise exception_to_http_exception(
            BusinessLogicError(CASE_DETAIL_ERROR.format(error=str(e)))
        )

# Pydantic models for movements endpoint
class UserInfo(BaseModel):
    id: Optional[str] = Field(None, example="123e4567-e89b-12d3-a456-426614174000")
    name: Optional[str] = Field(None, example="Juan")
    lastname: Optional[str] = Field(None, example="Pérez")
    email: Optional[str] = Field(None, example="juan.perez@example.com")

class SectorBasicInfo(BaseModel):
    id: str = Field(..., example="123e4567-e89b-12d3-a456-426614174001")
    name: str = Field(..., example="Secretaría General")

class MovementItem(BaseModel):
    id: str = Field(..., example="123e4567-e89b-12d3-a456-426614174002")
    type: str = Field(..., example="creation")
    reason: str = Field(..., example="Expediente creado")
    created_at: Optional[str] = Field(None, example="2024-01-15T10:30:00")
    is_active: bool = Field(..., example=True)
    closed_at: Optional[str] = Field(None, example="2024-01-20T15:45:00")
    closing_reason: Optional[str] = Field(None, example="Movimiento cerrado")
    user: Optional[UserInfo] = None
    creator_sector: SectorBasicInfo
    admin_sector: SectorBasicInfo
    assigned_sector: Optional[SectorBasicInfo] = None

class MovementsData(BaseModel):
    movements: List[MovementItem]
    total: int = Field(..., example=5)

class MovementsResponse(BaseModel):
    success: bool = Field(True, example=True)
    data: MovementsData
    message: str = Field(..., example="Movimientos obtenidos exitosamente")

@router.get("/{case_id}/movements", response_model=MovementsResponse)
async def get_case_movements(
    request: Request,
    case_id: str = Path(..., description="ID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Obtener el historial de movimientos del expediente con información de usuarios y sectores.
    """
    from shared.exceptions import exception_to_http_exception
    from config.constants import MOVEMENTS_SUCCESS_MESSAGE, MOVEMENTS_ACCESS_DENIED
    import logging

    logger = get_logger(__name__)

    try:
        logger.info(f"Fetching movements for case: {case_id}")

        # Obtener usuario autenticado
        db_user_id = get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        logger.info(f"User {db_user_id} requesting movements for case {case_id}")

        # Verificar permisos (404 para no revelar existencia del expediente)
        if not CaseService.can_user_view_case(case_id, db_user_id, schema_name=schema_name):
            logger.warning(f"Access denied: User {db_user_id} cannot view case {case_id}")
            raise NotFoundError(CASE_NOT_FOUND_ERROR)

        # Obtener movimientos
        movements = CaseService.get_case_movements(case_id, schema_name=schema_name)
        
        logger.info(f"Successfully fetched {len(movements)} movements for case {case_id}")
        
        return {
            "success": True,
            "data": {
                "movements": movements,
                "total": len(movements)
            },
            "message": MOVEMENTS_SUCCESS_MESSAGE
        }
        
    except Exception as e:
        logger.error(f"Error in get_case_movements endpoint: {str(e)}")
        raise exception_to_http_exception(e)

# Pydantic models for documents endpoint
class OfficialDocumentItem(BaseModel):
    id: str = Field(..., example="123e4567-e89b-12d3-a456-426614174000")
    document_id: str = Field(..., example="123e4567-e89b-12d3-a456-426614174001")
    order: int = Field(..., example=1)
    official_number: str = Field(..., example="OF-2024-001")
    reference: str = Field(..., example="Resolución de aprobación")
    linked_date: Optional[str] = Field(None, example="2024-01-15T10:30:00")
    is_active: bool = Field(..., example=True)
    pdf_url: Optional[str] = Field(None, example="https://r2.example.com/OF-2024-001.pdf")
    short_resume: Optional[str] = Field(None, example="Resumen del documento")
    linked_by: Optional[str] = Field(None, example="Juan Pérez")
    linked_sector: Optional[str] = Field(None, example="GOB#SGOB")

class ProposedDocumentItem(BaseModel):
    id: str = Field(..., example="123e4567-e89b-12d3-a456-426614174002")
    document_draft_id: str = Field(..., example="123e4567-e89b-12d3-a456-426614174003")
    reference: str = Field(..., example="Borrador de informe")
    status: str = Field(..., example="pending")
    document_number: Optional[str] = Field(None, example="INF-2025-0000001-MUNI")
    document_type_name: Optional[str] = Field(None, example="Informe")
    document_type_acronym: Optional[str] = Field(None, example="INF")
    can_link: bool = Field(False, example=True, description="true si el documento está firmado (oficial)")
    proposed_date: Optional[str] = Field(None, example="2024-01-15T10:30:00")
    proposed_by: Optional[str] = Field(None, example="Juan Pérez")
    short_resume: Optional[str] = Field(None, example="Resumen del documento propuesto")

class DocumentsData(BaseModel):
    official: List[OfficialDocumentItem]
    proposed: List[ProposedDocumentItem]
    total_official: int = Field(..., example=5)
    total_proposed: int = Field(..., example=2)

class DocumentsResponse(BaseModel):
    success: bool = Field(True, example=True)
    data: DocumentsData
    message: str = Field(..., example="Documentos obtenidos exitosamente")

@router.get("/{case_id}/documents", response_model=DocumentsResponse)
async def get_case_documents(
    request: Request,
    case_id: str = Path(..., description="ID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Obtener documentos vinculados al expediente (oficiales y propuestos).
    Los documentos oficiales incluyen pdf_url con URL firmada de Cloudflare R2.
    """
    from shared.exceptions import exception_to_http_exception
    from config.constants import DOCUMENTS_SUCCESS_MESSAGE, DOCUMENTS_ACCESS_DENIED
    import logging

    logger = get_logger(__name__)

    try:
        # Validar usuario autenticado primero
        tenant_user_id = getattr(request.state, 'tenant_user_id', None)
        if not tenant_user_id:
            raise ValidationError(USER_UNAUTHENTICATED_ERROR)

        logger.info(f"Fetching documents for case: {case_id}")

        # Obtener usuario autenticado
        db_user_id = get_authenticated_user(tenant_user_id, schema_name=schema_name)

        logger.info(f"User {db_user_id} requesting documents for case {case_id}")

        # Verificar permisos (404 para no revelar existencia del expediente)
        if not CaseService.can_user_view_case(case_id, db_user_id, schema_name=schema_name):
            logger.warning(f"Access denied: User {db_user_id} cannot view case {case_id}")
            raise NotFoundError(CASE_NOT_FOUND_ERROR)

        # Obtener documentos (incluye URLs de PDFs)
        documents = CaseService.get_case_documents(case_id, schema_name=schema_name)

        # Filtrar proposed docs: solo usuarios con permiso de edición los ven
        permissions = CaseService.get_user_case_permissions(case_id, db_user_id, schema_name=schema_name)
        if not permissions.get('can_link_documents', False):
            documents['proposed'] = []
            documents['total_proposed'] = 0

        total_docs = documents["total_official"] + documents["total_proposed"]
        logger.info(f"Successfully fetched {total_docs} documents for case {case_id} ({documents['total_official']} official, {documents['total_proposed']} proposed)")
        
        return {
            "success": True,
            "data": documents,
            "message": DOCUMENTS_SUCCESS_MESSAGE
        }
        
    except Exception as e:
        logger.error(f"Error in get_case_documents endpoint: {str(e)}")
        raise exception_to_http_exception(e)

# Pydantic models for permissions endpoint
class PermissionsData(BaseModel):
    can_view: bool = Field(..., example=True)
    can_transfer: bool = Field(..., example=True)
    can_assign: bool = Field(..., example=True)
    can_archive: bool = Field(..., example=False)
    can_link_documents: bool = Field(..., example=True)
    can_create_movements: bool = Field(..., example=True)
    can_subsanar: bool = Field(..., example=True, description="Solo ADMIN del expediente puede subsanar")
    ownership_level: str = Field(..., example="owner", description="owner, creator, department_member, o participant")

class PermissionsResponse(BaseModel):
    success: bool = Field(True, example=True)
    data: PermissionsData
    message: str = Field(..., example="Permisos obtenidos exitosamente")

@router.get("/{case_id}/permissions", response_model=PermissionsResponse)
async def get_user_case_permissions(
    request: Request,
    case_id: str = Path(..., description="ID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Obtener permisos específicos del usuario sobre el expediente.

    Retorna qué acciones puede realizar el usuario:
    - can_view: Puede ver el expediente
    - can_transfer: Puede transferir a otro sector (solo ADMIN)
    - can_assign: Puede asignar tareas
    - can_archive: Puede archivar
    - can_link_documents: Puede vincular documentos
    - can_create_movements: Puede crear movimientos
    - can_subsanar: Puede subsanar documentos (solo ADMIN)
    - ownership_level: Nivel de relación (owner, creator, department_member, participant)
    """
    from shared.exceptions import exception_to_http_exception
    from config.constants import PERMISSIONS_SUCCESS_MESSAGE, PERMISSIONS_ACCESS_DENIED
    import logging

    logger = get_logger(__name__)

    try:
        logger.info(f"Fetching permissions for case: {case_id}")

        # Obtener usuario autenticado
        db_user_id = get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        logger.info(f"User {db_user_id} requesting permissions for case {case_id}")

        # Verificar permisos (404 para no revelar existencia del expediente)
        if not CaseService.can_user_view_case(case_id, db_user_id, schema_name=schema_name):
            logger.warning(f"Access denied: User {db_user_id} cannot view case {case_id}")
            raise NotFoundError(CASE_NOT_FOUND_ERROR)

        # Obtener permisos calculados
        permissions = CaseService.get_user_case_permissions(case_id, db_user_id, schema_name=schema_name)
        
        logger.info(f"Successfully calculated permissions for user {db_user_id} on case {case_id}")
        
        return {
            "success": True,
            "data": permissions,
            "message": PERMISSIONS_SUCCESS_MESSAGE
        }
        
    except Exception as e:
        logger.error(f"Error in get_user_case_permissions endpoint: {str(e)}")
        raise exception_to_http_exception(e)

# Pydantic models for case-history endpoint
class HistoryUserInfo(BaseModel):
    name: str = Field(..., example="Juan Pérez")
    sector_department: str = Field(..., example="ADGEN#SEC")
    photo_url: str = Field(..., example="")

class HistoryMovementItem(BaseModel):
    user: HistoryUserInfo
    created_at: Optional[str] = Field(None, example="2024-01-15T10:30:00")
    message: str = Field(..., example="Juan Pérez creó el expediente")
    type: str = Field(..., example="creation")
    is_active: bool = Field(..., example=True)
    closed_at: Optional[str] = Field(None, example="2024-01-20T15:45:00")
    closing_reason: Optional[str] = Field(None, example="Movimiento cerrado")
    resume: Optional[str] = Field(None, example="- Punto 1\n- Punto 2", description="Resume del documento (solo para document_link)")

class CaseHistoryData(BaseModel):
    case_number: str = Field(..., example="EXP-2024-001-SMG")
    ai_summary: Optional[str] = Field(None, description="Resumen IA del expediente")
    ai_summary_updated_at: Optional[str] = Field(None, description="Timestamp de actualización del resumen")
    short_ai_summary: Optional[str] = Field(None, description="Resumen corto del expediente generado por IA (1-2 oraciones)")
    movements: List[HistoryMovementItem]

class CaseHistoryResponse(BaseModel):
    success: bool = Field(True, example=True)
    data: CaseHistoryData
    message: str = Field(..., example="Historial obtenido exitosamente")

@router.get("/{case_id}/case-history", response_model=CaseHistoryResponse)
async def get_case_history(
    request: Request,
    case_id: str = Path(..., description="ID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Obtener historial completo de movimientos del expediente.

    Retorna todos los movimientos del expediente con mensajes formateados
    que describen qué acción se realizó, quién la hizo y cuándo.
    """
    from shared.exceptions import exception_to_http_exception
    from config.constants import CASE_HISTORY_SUCCESS_MESSAGE
    import logging

    logger = get_logger(__name__)

    try:
        logger.info(f"Fetching history for case: {case_id}")

        # Validar usuario autenticado
        db_user_id = get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        logger.info(f"User {db_user_id} requesting history for case {case_id}")

        # Verificar permisos (404 para no revelar existencia del expediente)
        if not CaseService.can_user_view_case(case_id, db_user_id, schema_name=schema_name):
            logger.warning(f"Access denied: User {db_user_id} cannot view case {case_id}")
            raise NotFoundError(CASE_NOT_FOUND_ERROR)

        # Obtener historial del expediente
        history = CaseService.get_case_history(case_id, schema_name=schema_name)
        
        logger.info(f"Successfully fetched history for case {case_id}: {len(history['movements'])} movements")
        
        return {
            "success": True,
            "data": history,
            "message": CASE_HISTORY_SUCCESS_MESSAGE
        }
        
    except Exception as e:
        logger.error(f"Error in get_case_history endpoint: {str(e)}")
        raise exception_to_http_exception(e)