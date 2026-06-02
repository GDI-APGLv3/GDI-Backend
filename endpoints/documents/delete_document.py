"""Endpoint para eliminar documentos (soft delete)."""

from shared.logging import get_logger
from fastapi import APIRouter, Path, Depends, Request
from uuid import UUID
from models.documents.deletion import DeleteDocumentResponse
from models.tags import Tags
from auth import get_current_user, AuthenticatedUser
from services.documents.lifecycle.deletion import delete_document
from shared.exceptions import exception_to_http_exception
from shared.dependencies import get_tenant_schema

logger = get_logger(__name__)

router = APIRouter(tags=[Tags.DOCUMENTOS])


@router.delete(
    "/documents/{document_id}",
    response_model=DeleteDocumentResponse,
    summary="Eliminar documento",
    description=(
        "Permite al creador eliminar un documento en estado 'draft' o 'rejected'. "
        "El documento se marca como eliminado (soft delete) y se desvincula automáticamente "
        "de cualquier expediente al que estuviera asociado."
    ),
    responses={
        200: {"description": "Documento eliminado exitosamente"},
        403: {"description": "Usuario no es el creador del documento"},
        404: {"description": "Documento no encontrado"},
        409: {"description": "Documento ya fue eliminado previamente"},
        422: {"description": "Estado del documento no permite eliminación (sent_to_sign, signed)"}
    }
)
async def delete_document_endpoint(
    request: Request,
    document_id: UUID = Path(..., description="UUID del documento a eliminar"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
) -> DeleteDocumentResponse:
    """Elimina un documento (soft delete)."""
    document_id = str(document_id)
    logger.info(
        "Iniciando eliminación de documento",
        extra={
            "operation": "delete_document",
            "document_id": document_id,
            "user_id": request.state.tenant_user_id,
            "endpoint": "delete_document_endpoint"
        }
    )

    try:
        result = await delete_document(document_id, request.state.tenant_user_id, schema_name=schema_name)

        logger.info(
            "Documento eliminado exitosamente",
            extra={
                "operation": "delete_document_success",
                "document_id": document_id,
                "user_id": request.state.tenant_user_id,
                "unlinked_cases": result.get("unlinked_cases", 0)
            }
        )

        return result
    except Exception as e:
        logger.error(
            "Error al eliminar documento",
            extra={
                "operation": "delete_document_error",
                "document_id": document_id,
                "user_id": request.state.tenant_user_id,
                "error": str(e)
            },
            exc_info=True
        )
        raise exception_to_http_exception(e)
