"""Endpoint para obtener URL de descarga de documentos oficiales."""
from shared.logging import get_logger
from fastapi import APIRouter, Path, Depends, Request
from shared.exceptions import (
    AuthorizationError, DocumentNotFoundError, ValidationError, DocumentStateError,
    exception_to_http_exception
)
from services.documents.retrieval.official_url import get_official_document_url
from models.documents.official_url import OfficialDocumentUrlResponse
from models.tags import Tags
from auth import get_current_user
from models.schemas import AuthenticatedUser
from shared.dependencies import get_tenant_schema

logger = get_logger(__name__)

router = APIRouter(tags=[Tags.DOCUMENTOS])

@router.get(
    "/documents/{document_id}/geturl_officialdoc",
    response_model=OfficialDocumentUrlResponse,
    summary="Obtener URL de documento oficial firmado",
    description="""Obtiene URL temporal (15 min) de descarga del PDF oficial firmado desde Cloudflare R2.
    
    **Uso en frontend:**
    - Modal de subsanación: Previsualizar documento justificante antes de vincular
    - Modal de vinculación: Verificar documento oficial antes de asociar al expediente
    
    **Requisitos:**
    - Documento debe estar en estado 'signed' (firmado y numerado)
    - Usuario autenticado
    
    **Casos de uso:**
    1. Usuario subsana documento rechazado → selecciona justificante → ve PDF
    2. Usuario vincula documento a expediente → selecciona oficial → ve PDF
    """
)
async def get_url_official_doc(
    request: Request,
    document_id: str = Path(..., description="UUID del documento"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
) -> OfficialDocumentUrlResponse:
    """Obtiene URL temporal para descargar documento oficial firmado."""
    logger.info(
        "Obteniendo URL de documento oficial",
        extra={
            "operation": "get_url_official_doc",
            "document_id": document_id,
            "user_id": request.state.tenant_user_id,
            "endpoint": "get_url_official_doc"
        }
    )

    try:
        from services.documents.permissions import can_user_view_document
        if not can_user_view_document(document_id, request.state.tenant_user_id, schema_name=schema_name):
            from shared.exceptions import AuthorizationError
            raise AuthorizationError("No tiene permisos para ver este documento")
        result = await get_official_document_url(document_id, schema_name=schema_name)

        logger.info(
            "URL de documento oficial obtenida exitosamente",
            extra={
                "operation": "get_url_official_doc_success",
                "document_id": document_id,
                "user_id": request.state.tenant_user_id,
                "official_number": result.get("official_number"),
                "expires_in": result.get("expires_in")
            }
        )

        return {
            "success": True,
            "data": result,
            "message": "URL de descarga obtenida exitosamente"
        }

    except (DocumentNotFoundError, ValidationError, DocumentStateError, AuthorizationError) as e:
        logger.error(
            "Error obteniendo URL de documento oficial",
            extra={
                "operation": "get_url_official_doc_error",
                "document_id": document_id,
                "user_id": request.state.tenant_user_id,
                "error": str(e)
            },
            exc_info=True
        )
        raise exception_to_http_exception(e)
