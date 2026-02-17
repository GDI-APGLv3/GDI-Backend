"""Endpoint para obtener detalles de documento en estado editable."""

from shared.logging import get_logger
from fastapi import APIRouter, Path, Depends, Request
from models.documents.editing import DocumentDetailResponse
from models.tags import Tags
from auth import get_current_user, AuthenticatedUser
from services.documents.editing import get_document_details_for_editing
from shared.exceptions import exception_to_http_exception
from shared.dependencies import get_tenant_schema

logger = get_logger(__name__)

router = APIRouter(tags=[Tags.DOCUMENTOS])

@router.get(
    "/documents/{document_id}/editor-details",
    response_model=DocumentDetailResponse,
    summary="Obtener detalles de documento para edición",
    description=(
        "Obtiene todos los detalles de un documento editable para cargar en el editor. "
        "Solo funciona con documentos en estado 'draft' o 'rejected'. "
        "Incluye contenido, firmantes, tipo de documento, y datos del creador."
    ),
    responses={
        200: {"description": "Detalles del documento obtenidos exitosamente"},
        404: {"description": "Documento no encontrado"},
        409: {"description": "Documento no puede editarse en su estado actual"}
    }
)
async def get_document_editor_details(
    request: Request,
    document_id: str = Path(..., description="UUID del documento a editar"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
) -> DocumentDetailResponse:
    """Obtiene detalles completos de un documento editable."""
    logger.info(
        "Obteniendo detalles de documento para edición",
        extra={
            "operation": "get_editor_details",
            "document_id": document_id,
            "user_id": request.state.tenant_user_id,
            "endpoint": "get_document_editor_details"
        }
    )

    try:
        result = get_document_details_for_editing(document_id, schema_name=schema_name)

        logger.info(
            "Detalles de documento obtenidos exitosamente",
            extra={
                "operation": "get_editor_details_success",
                "document_id": document_id,
                "user_id": request.state.tenant_user_id,
                "document_status": result.get("status"),
                "signers_count": len(result.get("signers", []))
            }
        )

        return DocumentDetailResponse(**result)
    except Exception as e:
        logger.error(
            "Error obteniendo detalles de documento",
            extra={
                "operation": "get_editor_details_error",
                "document_id": document_id,
                "user_id": request.state.tenant_user_id,
                "error": str(e)
            },
            exc_info=True
        )
        raise exception_to_http_exception(e)