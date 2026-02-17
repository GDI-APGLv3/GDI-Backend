"""
Endpoints para aceptar/rechazar documentos propuestos en expedientes
POST /cases/{case_id}/proposed-documents/{proposed_id}/accept
POST /cases/{case_id}/proposed-documents/{proposed_id}/reject
"""

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, Field
from typing import Optional
from auth import get_current_user
from models.schemas import AuthenticatedUser
from services.case_service import CaseService
from shared.exceptions import exception_to_http_exception, NotFoundError
from shared.utils import get_authenticated_user
from shared.dependencies import get_tenant_schema
from config.constants import (
    PROPOSED_DOCUMENT_ACCEPT_SUCCESS,
    PROPOSED_DOCUMENT_REJECT_SUCCESS,
    LINK_DOCUMENT_USER_NOT_FOUND,
)
from shared.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["expedientes"])


# ============================================================================
# RESPONSE MODELS
# ============================================================================

class AcceptProposedDocumentData(BaseModel):
    link_id: str = Field(..., example="link-123")
    case_id: str = Field(..., example="case-456")
    case_number: str = Field(..., example="EXP-2024-001-SMG")
    official_document_id: str = Field(..., example="doc-789")
    official_number: str = Field(..., example="INF-2025-0000001-MUNI")
    document_reference: Optional[str] = Field(default=None, example="Informe de situacion")
    order_number: str = Field(..., example="001")
    linking_date: str = Field(..., example="2025-01-15T10:30:00")
    linked_by: str = Field(..., example="Juan Perez")


class AcceptProposedDocumentResponse(BaseModel):
    success: bool = Field(True, example=True)
    data: AcceptProposedDocumentData
    message: str = Field(..., example="Documento propuesto aceptado y vinculado exitosamente")


class RejectProposedDocumentData(BaseModel):
    proposed_id: str = Field(..., example="proposed-123")
    document_reference: str = Field(..., example="Borrador de informe")
    action: str = Field("rejected", example="rejected")


class RejectProposedDocumentResponse(BaseModel):
    success: bool = Field(True, example=True)
    data: RejectProposedDocumentData
    message: str = Field(..., example="Documento propuesto rechazado exitosamente")


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.post(
    "/{case_id}/proposed-documents/{proposed_id}/accept",
    response_model=AcceptProposedDocumentResponse
)
async def accept_proposed_document(
    request: Request,
    case_id: str = Path(..., description="UUID del expediente"),
    proposed_id: str = Path(..., description="UUID de la propuesta"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Aceptar documento propuesto: vincula el documento oficial al expediente y desactiva la propuesta.

    Solo se pueden aceptar documentos con estado 'signed' (oficiales).
    Requiere permisos de edicion sobre el expediente (ADMIN o sector ASIGNADO).
    """
    try:
        logger.info(f"Accept proposed document: case={case_id}, proposed={proposed_id}")

        db_user_id = get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        # Obtener sector y nombre del usuario
        from database import execute_query
        user_query = "SELECT sector_id, full_name FROM users WHERE id = %s"
        user_result = execute_query(user_query, (db_user_id,), schema_name=schema_name)

        if not user_result:
            raise NotFoundError(LINK_DOCUMENT_USER_NOT_FOUND)

        user_sector_id = str(user_result[0]['sector_id'])
        user_full_name = user_result[0]['full_name']

        # Aceptar propuesta (vincula + desactiva)
        link_result = CaseService.accept_proposed_document(
            case_id=case_id,
            proposed_id=proposed_id,
            user_id=db_user_id,
            user_sector_id=user_sector_id,
            schema_name=schema_name
        )

        formatted_order = f"{link_result['order_number']:03d}"
        linking_date_str = (
            link_result['linking_date'].isoformat()
            if hasattr(link_result['linking_date'], 'isoformat')
            else str(link_result['linking_date'])
        )

        logger.info(f"Proposed document accepted: {link_result['official_number']} as #{formatted_order}")

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
            "message": f"{PROPOSED_DOCUMENT_ACCEPT_SUCCESS}: {link_result['official_number']} (#{formatted_order})"
        }

    except Exception as e:
        logger.error(f"Error in accept_proposed_document endpoint: {str(e)}")
        raise exception_to_http_exception(e)


@router.post(
    "/{case_id}/proposed-documents/{proposed_id}/reject",
    response_model=RejectProposedDocumentResponse
)
async def reject_proposed_document(
    request: Request,
    case_id: str = Path(..., description="UUID del expediente"),
    proposed_id: str = Path(..., description="UUID de la propuesta"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Rechazar documento propuesto: desactiva la propuesta sin vincular al expediente.

    Se puede rechazar cualquier propuesta activa, independientemente del estado del documento.
    Requiere permisos de edicion sobre el expediente (ADMIN o sector ASIGNADO).
    """
    try:
        logger.info(f"Reject proposed document: case={case_id}, proposed={proposed_id}")

        db_user_id = get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        # Obtener sector del usuario para historial
        from database import execute_query
        user_query = "SELECT sector_id FROM users WHERE id = %s"
        user_result = execute_query(user_query, (db_user_id,), schema_name=schema_name)
        user_sector_id = str(user_result[0]['sector_id']) if user_result else None

        # Rechazar propuesta (desactiva + registra historial)
        result = CaseService.reject_proposed_document(
            case_id=case_id,
            proposed_id=proposed_id,
            user_id=db_user_id,
            user_sector_id=user_sector_id,
            schema_name=schema_name
        )

        logger.info(f"Proposed document rejected: {proposed_id}")

        return {
            "success": True,
            "data": result,
            "message": PROPOSED_DOCUMENT_REJECT_SUCCESS
        }

    except Exception as e:
        logger.error(f"Error in reject_proposed_document endpoint: {str(e)}")
        raise exception_to_http_exception(e)
