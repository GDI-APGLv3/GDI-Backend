import re
from shared.logging import get_logger
from fastapi import APIRouter, Path, Depends, Request, Response
from uuid import UUID
from shared.exceptions import (
    AuthorizationError, DocumentNotFoundError, ValidationError, DocumentStateError,
    exception_to_http_exception
)
from services.documents.retrieval.official_url import (
    get_official_document_url, get_official_document_bytes
)
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
    document_id: UUID = Path(..., description="UUID del documento"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
) -> OfficialDocumentUrlResponse:
    """Obtiene URL temporal para descargar documento oficial firmado."""
    document_id = str(document_id)
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
        if not await can_user_view_document(document_id, request.state.tenant_user_id, schema_name=schema_name):
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


@router.get(
    "/documents/{document_id}/stream_officialdoc",
    summary="Servir PDF oficial firmado via proxy autenticado",
    description="""Devuelve el PDF oficial firmado como binario (application/pdf inline),
    descargado desde Cloudflare R2 del lado del servidor.

    A diferencia de `geturl_officialdoc` (que entrega una URL firmada de R2 que el
    browser abre directo), este endpoint sirve el contenido a traves del backend.
    Asi la URL de Cloudflare nunca queda expuesta al cliente: pensado para abrir el
    PDF en una pestana nueva en mobile sin filtrar el link presignado.

    **Requisitos:**
    - Documento en estado 'signed' (firmado y numerado)
    - Usuario autenticado con permiso de ver el documento
    """,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "PDF servido inline"},
        403: {"description": "Sin permisos para ver el documento"},
        404: {"description": "Documento no encontrado"},
    },
)
async def stream_official_doc(
    request: Request,
    document_id: UUID = Path(..., description="UUID del documento"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
) -> Response:
    """Sirve el PDF oficial firmado via proxy, validando permisos."""
    document_id = str(document_id)
    logger.info(
        "Sirviendo PDF oficial via proxy",
        extra={
            "operation": "stream_official_doc",
            "document_id": document_id,
            "user_id": request.state.tenant_user_id,
        }
    )

    try:
        from services.documents.permissions import can_user_view_document
        if not await can_user_view_document(document_id, request.state.tenant_user_id, schema_name=schema_name):
            raise AuthorizationError("No tiene permisos para ver este documento")

        result = await get_official_document_bytes(document_id, schema_name=schema_name)
        pdf_bytes = result["pdf_bytes"]
        official_number = result["official_number"]
        filename = official_number if official_number.lower().endswith(".pdf") else f"{official_number}.pdf"
        filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename)

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                "Content-Length": str(len(pdf_bytes)),
                "Cache-Control": "private, no-store",
            },
        )

    except (DocumentNotFoundError, ValidationError, DocumentStateError, AuthorizationError) as e:
        logger.error(
            "Error sirviendo PDF oficial via proxy",
            extra={
                "operation": "stream_official_doc_error",
                "document_id": document_id,
                "user_id": request.state.tenant_user_id,
                "error": str(e),
            },
            exc_info=True
        )
        raise exception_to_http_exception(e)
