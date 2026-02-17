"""
Endpoint para vincular documentos oficiales a expedientes
POST /cases/{case_id}/documents/link - Vincular documento oficial
"""

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, Field
from typing import Optional
from auth import get_current_user
from models.schemas import AuthenticatedUser
from services.case_service import CaseService
from shared.exceptions import exception_to_http_exception
from shared.utils import get_authenticated_user
from shared.dependencies import get_tenant_schema
from config.constants import LINK_DOCUMENT_SUCCESS
from shared.logging import get_logger
logger = get_logger(__name__)

# Inicializar router
router = APIRouter(tags=["expedientes"])


# ============================================================================
# REQUEST MODELS
# ============================================================================

class LinkDocumentRequest(BaseModel):
    """Modelo para solicitud de vinculación de documento"""
    official_document_id: str = Field(..., description="UUID del documento oficial a vincular", example="550e8400-e29b-41d4-a716-446655440000")


# ============================================================================
# RESPONSE MODELS
# ============================================================================

class LinkDocumentData(BaseModel):
    """Datos del documento vinculado"""
    link_id: str = Field(..., example="link-123")
    case_id: str = Field(..., example="case-456")
    case_number: str = Field(..., example="EXP-2024-001-SMG")
    official_document_id: str = Field(..., example="doc-789")
    official_number: str = Field(..., example="PV-2024-001")
    document_reference: Optional[str] = Field(default=None, example="Referencia del documento")
    order_number: str = Field(..., example="001")
    linking_date: str = Field(..., example="2024-01-15T10:30:00")
    linked_by: str = Field(..., example="Juan Pérez")


class LinkDocumentResponse(BaseModel):
    """Modelo para respuesta de vinculación de documento"""
    success: bool = Field(True, example=True)
    data: LinkDocumentData
    message: str = Field(..., example="Documento vinculado exitosamente al expediente")


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.post("/{case_id}/documents/link", response_model=LinkDocumentResponse)
async def link_document_to_case(
    request: Request,
    case_id: str = Path(..., description="UUID del expediente"),
    body: LinkDocumentRequest = ...,
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Vincular documento oficial a expediente.

    **Parámetros:**
    - **case_id**: UUID del expediente (en la URL)
    - **official_document_id**: UUID del documento oficial a vincular

    **Funcionamiento automático:**
    - Asigna order_number correlativo por expediente (001, 002, 003...)
    - Registra fecha y usuario que vincula
    - Valida permisos (solo ADMIN o sectores ASIGNADOS)
    - Previene duplicados (mismo documento en mismo expediente)

    **Permisos requeridos:**
    - Sector ADMIN (propietario actual del expediente), O
    - Sector con asignación activa
    """
    try:
        logger.info(f"Link document request: case={case_id}, doc={body.official_document_id}")

        # Validar usuario autenticado y obtener sus datos
        db_user_id = get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        # Obtener sector del usuario para la vinculación
        from database import execute_query
        user_query = "SELECT sector_id, full_name FROM users WHERE id = %s"
        user_result = execute_query(user_query, (db_user_id,), schema_name=schema_name)
        
        if not user_result:
            from config.constants import LINK_DOCUMENT_USER_NOT_FOUND
            from shared.exceptions import NotFoundError
            logger.error(f"User not found: {db_user_id}")
            raise NotFoundError(LINK_DOCUMENT_USER_NOT_FOUND)
        
        user_sector_id = user_result[0]['sector_id']
        user_full_name = user_result[0]['full_name']
        
        logger.info(f"User {db_user_id} ({user_full_name}) linking document to case {case_id}")
        
        # Vincular documento usando el servicio
        try:
            link_result = CaseService.link_official_document(
                case_id=case_id,
                official_document_id=body.official_document_id,
                linking_user_id=db_user_id,
                user_sector_id=str(user_sector_id),
                schema_name=schema_name
            )
            logger.info(f"Service returned: {link_result}")
        except Exception as service_error:
            logger.error(f"Service error: {type(service_error).__name__}: {str(service_error)}")
            raise
        
        # Formatear order_number a 3 dígitos y linking_date a string
        formatted_order = f"{link_result['order_number']:03d}"
        linking_date_str = link_result['linking_date'].isoformat() if hasattr(link_result['linking_date'], 'isoformat') else str(link_result['linking_date'])
        
        logger.info(f"Document linked successfully: {link_result['official_number']} as #{formatted_order}")
        
        return {
            "success": True,
            "data": {
                "link_id": link_result['link_id'],
                "case_id": link_result['case_id'],
                "case_number": link_result['case_number'],
                "official_document_id": link_result['official_document_id'],
                "official_number": link_result['official_number'],
                "document_reference": link_result.get('document_reference'),
                "order_number": formatted_order,
                "linking_date": linking_date_str,
                "linked_by": user_full_name
            },
            "message": f"{LINK_DOCUMENT_SUCCESS}: {link_result['official_number']} (#{formatted_order})"
        }
        
    except Exception as e:
        logger.error(f"Error in link_document_to_case endpoint: {str(e)}")
        raise exception_to_http_exception(e)
