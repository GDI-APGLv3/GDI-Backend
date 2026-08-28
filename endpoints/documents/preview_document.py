from uuid import UUID
from shared.logging import get_logger
from fastapi import APIRouter, Path, Response, Depends, Request
from auth import get_current_user
from models.schemas import AuthenticatedUser
from models.documents.preview import PreviewInfoResponse
from services.documents.preview.core import generate_document_preview
from services.documents.preview.data_fetcher import PreviewDataFetcher
from shared.dependencies import get_tenant_schema
from shared.exceptions import (
    DocumentNotFoundError, DocumentStateError, ValidationError,
    exception_to_http_exception
)
from models.tags import Tags

logger = get_logger("preview_document")

router = APIRouter(tags=[Tags.DOCUMENTOS])

@router.post(
    "/documents/{document_id}/preview-info",
    response_model=PreviewInfoResponse,
    summary="Obtener información del documento para preview",
    description="""Obtiene datos completos del documento para previsualización.
    
    **Uso en frontend:**
    - **Hook usePreviewDocumentInfo**: Carga datos del documento en modo preview
    - Modal de previsualización antes de enviar a firma
    - Vista previa sin descargar PDF
    
    **Retorna:**
    - Estado de visualización del documento
    - Tipo de documento (acrónimo y nombre)
    - Lista de firmantes
    - ID del PDF generado (si existe)
    
    **Casos de uso:**
    1. Usuario previsualiza documento antes de firmar
    2. Modal de revisión de información del documento
    """,
    dependencies=[Depends(get_current_user)]
)
async def preview_document_info(
    request: Request,
    document_id: UUID = Path(..., description="UUID del documento a previsualizar"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
) -> PreviewInfoResponse:
    """
    Obtiene información completa del documento para previsualización.

    Args:
        request: FastAPI request object
        document_id: UUID del documento
        current_user: Usuario autenticado
        schema_name: Schema del tenant

    Returns:
        PreviewInfoResponse: Datos del documento para preview

    Raises:
        HTTPException: Para errores de documento no encontrado o estado inválido
    """
    document_id = str(document_id)
    try:
        from services.documents.permissions import can_user_view_document
        if not await can_user_view_document(document_id, request.state.tenant_user_id, schema_name=schema_name):
            from shared.exceptions import AuthorizationError
            raise AuthorizationError("No tiene permisos para ver este documento")

        logger.info(f"Usuario {request.state.tenant_user_id[:8]}... solicitando info de preview para documento {document_id[:8]}...")

        data_fetcher = PreviewDataFetcher(schema_name=schema_name)
        document_data = await data_fetcher.get_complete_document_data(document_id)
        
        logger.info(f"Información de preview obtenida exitosamente para documento {document_id[:8]}...")
        return {
            "success": True,
            "message": "Información del documento obtenida exitosamente",
            "document_id": document_id,
            "document_data": document_data
        }
        
    except (DocumentNotFoundError, DocumentStateError, ValidationError) as e:
        logger.warning(f"Error en info de preview para documento {document_id[:8]}...: {type(e).__name__} - {e}")
        raise exception_to_http_exception(e)


@router.post(
    "/documents/{document_id}/preview-download",
    summary="Descargar PDF de previsualización",
    description="""Genera y descarga PDF del documento con marca de agua.

    **Uso en frontend:**
    - Botón "Descargar preview" en editor de documentos
    - Vista previa antes de enviar a firma

    **Características:**
    - Para documentos HTML: Genera PDF con marca de agua "BORRADOR"
    - Para documentos Importados: Retorna URL firmada del PDF en R2
    - Aplica auto-guardado si el documento está en edición

    **Retorno:**
    - Documentos HTML: Archivo binario PDF listo para descarga
    - Documentos Importados: JSON con URL firmada

    **Casos de uso:**
    1. Usuario quiere ver PDF antes de firmar
    2. Revisor valida formato del documento
    """,
    responses={
        200: {
            "content": {
                "application/pdf": {},
                "application/json": {}
            },
            "description": "PDF generado/URL obtenida exitosamente"
        },
        404: {
            "description": "Documento no encontrado"
        },
        400: {
            "description": "Error en la validación o generación del PDF"
        }
    },
    dependencies=[Depends(get_current_user)]
)
async def preview_document_download(
    request: Request,
    document_id: UUID = Path(..., description="UUID del documento a previsualizar"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Genera y descarga PDF del documento con marca de agua.

    Para documentos HTML: retorna PDF binario
    Para documentos Importados: retorna JSON con URL firmada

    Args:
        request: FastAPI request object
        document_id: UUID del documento
        current_user: Usuario autenticado
        schema_name: Schema del tenant

    Returns:
        Response: PDF binario con headers apropiados o JSON con URL

    Raises:
        HTTPException: Para errores de generación o validación
    """
    document_id = str(document_id)
    try:
        from services.documents.permissions import can_user_view_document
        if not await can_user_view_document(document_id, request.state.tenant_user_id, schema_name=schema_name):
            from shared.exceptions import AuthorizationError
            raise AuthorizationError("No tiene permisos para ver este documento")

        logger.info(f"Usuario {request.state.tenant_user_id[:8]}... solicitando descarga de preview para documento {document_id[:8]}...")

        result = await generate_document_preview(document_id, schema_name=schema_name)

        if result.get("is_imported"):
            if "pdf_url" not in result:
                logger.error(f"Error obteniendo URL de PDF importado para documento {document_id[:8]}...")
                raise ValidationError("No se pudo obtener la URL del PDF importado")

            logger.info(f"URL de preview obtenida exitosamente para documento importado {document_id[:8]}...")

            return {
                "success": True,
                "message": "URL de previsualización obtenida exitosamente",
                "pdf_url": result["pdf_url"],
                "is_imported": True
            }

        if "pdf_content" not in result:
            logger.error(f"Error en generación de PDF para documento {document_id[:8]}...")
            raise ValidationError("No se pudo generar el PDF de previsualización")

        pdf_content = result["pdf_content"]
        pdf_size_kb = len(pdf_content) / 1024

        document_data = result.get("document_data", {})
        filename = f"preview_{document_id}.pdf"

        logger.info(f"PDF de preview generado exitosamente: {pdf_size_kb:.2f} KB")

        headers = {
            "Content-Type": "application/pdf",
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Length": str(len(pdf_content)),
            "Cache-Control": "no-cache"
        }

        return Response(
            content=pdf_content,
            media_type="application/pdf",
            headers=headers
        )

    except (DocumentNotFoundError, DocumentStateError, ValidationError) as e:
        logger.warning(f"Error en descarga de preview para documento {document_id[:8]}...: {type(e).__name__} - {e}")
        raise exception_to_http_exception(e)
