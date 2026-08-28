
from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, Field
from typing import Optional
from auth import get_current_user
from models.schemas import AuthenticatedUser
from database import fetch_one
from services.case_service import CaseService
from services.cases.documents import propose_document_to_case
from services.cases.permissions import can_user_view_case
from shared.exceptions import exception_to_http_exception, NotFoundError, AuthorizationError
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


class ProposeDocumentRequest(BaseModel):
    document_draft_id: str = Field(
        ...,
        description="UUID del documento borrador a proponer",
        example="550e8400-e29b-41d4-a716-446655440000",
    )


class ProposeDocumentData(BaseModel):
    case_id: str = Field(..., example="case-456")
    document_draft_id: str = Field(..., example="doc-789")


class ProposeDocumentResponse(BaseModel):
    success: bool = Field(True, example=True)
    data: ProposeDocumentData
    message: str = Field(..., example="Documento propuesto para vincular al expediente")


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

        db_user_id = await get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        user_query = "SELECT sector_id, full_name FROM users WHERE id = $1"
        user_result = await fetch_one(user_query, db_user_id, schema_name=schema_name)

        if not user_result:
            raise NotFoundError(LINK_DOCUMENT_USER_NOT_FOUND)

        user_sector_id = str(user_result['sector_id'])
        user_full_name = user_result['full_name']

        link_result = await CaseService.accept_proposed_document(
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
                "document_reference": link_result['document_reference'],
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

        db_user_id = await get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        user_query = "SELECT sector_id FROM users WHERE id = $1"
        user_result = await fetch_one(user_query, db_user_id, schema_name=schema_name)
        user_sector_id = str(user_result['sector_id']) if user_result else None

        result = await CaseService.reject_proposed_document(
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


@router.post(
    "/{case_id}/documents/propose",
    response_model=ProposeDocumentResponse,
)
async def propose_document(
    request: Request,
    body: ProposeDocumentRequest,
    case_id: str = Path(..., description="UUID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    """
    Proponer un documento borrador para vincular a un expediente.

    Crea una propuesta activa en el expediente. Un responsable del expediente
    podra luego aceptarla (vinculando el documento oficial) o rechazarla.

    Equivalente al endpoint REST del Gateway (SET B) `cases.propose_document`,
    expuesto aqui en el Backend (SET A) y accesible por JWT y por API Key.

    Requiere permiso de visualizacion sobre el expediente.
    """
    try:
        logger.info(f"Propose document: case={case_id}, draft={body.document_draft_id}")

        db_user_id = await get_authenticated_user(
            request.state.tenant_user_id, schema_name=schema_name
        )

        if not await can_user_view_case(case_id, db_user_id, schema_name=schema_name):
            raise AuthorizationError("Sin permisos para ver este expediente")

        result = await propose_document_to_case(
            case_id=case_id,
            document_draft_id=body.document_draft_id,
            proposing_user_id=db_user_id,
            schema_name=schema_name,
        )

        return {
            "success": True,
            "data": {
                "case_id": result["case_id"],
                "document_draft_id": result["document_draft_id"],
            },
            "message": result["message"],
        }

    except Exception as e:
        logger.error(f"Error in propose_document endpoint: {str(e)}")
        raise exception_to_http_exception(e)
