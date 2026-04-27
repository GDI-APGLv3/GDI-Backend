"""Endpoint para obtener detalles de documento en proceso de firma."""

from shared.logging import get_logger
from fastapi import APIRouter, Path, Depends, Request
from models.documents.signing import DocumentSignatureDetailsResponse
from models.tags import Tags
from auth import get_current_user
from services.documents.signing.details_builder import build_signature_details_response
from shared.exceptions import (
    DocumentNotFoundError, ValidationError, AuthorizationError,
    ExternalServiceError, exception_to_http_exception
)
from models.schemas import AuthenticatedUser
from shared.dependencies import get_tenant_schema

logger = get_logger(__name__)
router = APIRouter(tags=[Tags.DOCUMENTOS])

@router.get(
    "/documents/{document_id}/signature-details",
    response_model=DocumentSignatureDetailsResponse,
    summary="Obtener detalles de documento en proceso de firma",
    description="Obtiene información completa del documento para firma, incluyendo progreso y permisos del usuario",
    responses={
        200: {"description": "Detalles del documento obtenidos exitosamente"},
        403: {"description": "Usuario no autorizado para ver este documento"},
        404: {"description": "Documento o usuario no encontrado"}
    }
)
async def get_document_signature_details(
    request: Request,
    document_id: str = Path(..., description="UUID del documento a visualizar"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
) -> DocumentSignatureDetailsResponse:
    """Obtiene detalles completos de un documento en proceso de firma."""
    try:
        from services.documents.permissions import can_user_view_document
        if not can_user_view_document(document_id, request.state.tenant_user_id, schema_name=schema_name):
            raise AuthorizationError("No tiene permisos para ver este documento")
        logger.info(f"Obteniendo detalles de firma - Usuario: {request.state.tenant_user_id[:8]}")
        result = await build_signature_details_response(document_id, request.state.tenant_user_id, schema_name=schema_name)
        logger.info("Detalles de firma obtenidos exitosamente")
        return result
    except (DocumentNotFoundError, ValidationError, AuthorizationError, ExternalServiceError) as e:
        logger.error(f"Error: {str(e)}")
        raise exception_to_http_exception(e)
