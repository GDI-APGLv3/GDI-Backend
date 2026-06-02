"""Endpoint para rechazar documentos."""

from shared.logging import get_logger
from fastapi import APIRouter, Path, Depends, Request
from uuid import UUID
from models.documents.rejection import RejectDocumentRequest, RejectDocumentResponse
from models.tags import Tags
from auth import get_current_user, AuthenticatedUser
from services.documents.lifecycle.rejection import reject_document
from shared.exceptions import exception_to_http_exception
from shared.dependencies import get_tenant_schema

logger = get_logger(__name__)

router = APIRouter(tags=[Tags.DOCUMENTOS])


@router.post(
    "/documents/{document_id}/reject",
    response_model=RejectDocumentResponse,
    summary="Rechazar documento",
    description=(
        "Permite a cualquier firmante rechazar un documento con un motivo. "
        "El documento rechazado vuelve al estado de edición para correcciones."
    ),
    responses={
        200: {"description": "Documento rechazado exitosamente"},
        400: {"description": "Datos inválidos o motivo faltante"},
        403: {"description": "Usuario no autorizado para rechazar"},
        404: {"description": "Documento no encontrado"},
        409: {"description": "Documento ya está rechazado o en estado que no permite rechazo"}
    }
)
async def reject_document_endpoint(
    request: Request,
    document_id: UUID = Path(..., description="UUID del documento"),
    rejection_data: RejectDocumentRequest = None,
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
) -> RejectDocumentResponse:
    """Rechaza un documento proporcionando un motivo."""
    document_id = str(document_id)
    logger.info(
        "Iniciando rechazo de documento",
        extra={
            "operation": "reject_document",
            "document_id": document_id,
            "user_id": request.state.tenant_user_id,
            "endpoint": "reject_document_endpoint"
        }
    )

    try:
        result = await reject_document(document_id, request.state.tenant_user_id, rejection_data.reason, schema_name=schema_name)

        logger.info(
            "Documento rechazado exitosamente",
            extra={
                "operation": "reject_document_success",
                "document_id": document_id,
                "user_id": request.state.tenant_user_id,
                "rejection_id": result.get("rejection_id"),
                "reason": rejection_data.reason
            }
        )

        return result
    except Exception as e:
        logger.error(
            "Error al rechazar documento",
            extra={
                "operation": "reject_document_error",
                "document_id": document_id,
                "user_id": request.state.tenant_user_id,
                "error": str(e)
            },
            exc_info=True
        )
        raise exception_to_http_exception(e)