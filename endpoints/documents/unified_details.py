"""
Endpoint unificado para obtener detalles de documentos en cualquier estado.
"""
from shared.logging import get_logger
from fastapi import APIRouter, Path, Depends, Request
from models.documents.unified import UnifiedDocumentDetailsResponse
from services.documents.unified_details import get_unified_document_details
from shared.exceptions import (
    DocumentNotFoundError, DocumentStateError, ValidationError,
    AuthorizationError, ExternalServiceError, exception_to_http_exception
)
from models.tags import Tags
from auth import get_current_user
from models.schemas import AuthenticatedUser
from shared.dependencies import get_tenant_schema

logger = get_logger(__name__)
router = APIRouter(tags=[Tags.DOCUMENTOS])


@router.get(
    "/documents/{document_id}/details",
    response_model=UnifiedDocumentDetailsResponse,
    summary="Obtener detalles de documento",
    description="Obtiene detalles de un documento en cualquier estado, delegando al servicio apropiado según su estado actual",
    responses={
        200: {"description": "Detalles obtenidos exitosamente"},
        400: {"description": "Estado no soportado o validación fallida"},
        403: {"description": "Usuario sin permisos (para documentos en firma)"},
        404: {"description": "Documento no encontrado"},
        500: {"description": "Error interno del servidor"}
    }
)
async def get_document_details(
    request: Request,
    document_id: str = Path(..., description="UUID del documento"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
) -> UnifiedDocumentDetailsResponse:
    """Obtiene detalles unificados de documento."""
    try:
        logger.info(f"Obteniendo detalles - Usuario: {request.state.tenant_user_id[:8]}")
        result = await get_unified_document_details(document_id, request.state.tenant_user_id, schema_name=schema_name)
        logger.info("Detalles obtenidos exitosamente")
        return result
    except (DocumentNotFoundError, DocumentStateError, ValidationError, 
            AuthorizationError, ExternalServiceError) as e:
        logger.error(f"Error: {str(e)}")
        raise exception_to_http_exception(e)
