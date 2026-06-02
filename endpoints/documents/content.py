"""
Endpoint para obtener contenido HTML de documentos oficiales.
Usado por frontend/integraciones que necesitan el contenido completo.
"""
from shared.logging import get_logger
from fastapi import APIRouter, HTTPException, Path, Request, status, Depends
from typing import Dict, Any
from uuid import UUID

from auth import get_current_user
from shared.dependencies import get_tenant_schema
from services.documents.content import get_official_document_content
from shared.exceptions import DocumentNotFoundError, exception_to_http_exception
from models.tags import Tags

logger = get_logger(__name__)

router = APIRouter(tags=[Tags.DOCUMENTOS])


@router.get(
    "/api/v1/documents/{document_id}/content",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Obtener contenido HTML de documento oficial",
    description="Devuelve el contenido HTML completo de un documento oficial firmado"
)
async def get_document_content(
    request: Request,
    document_id: UUID = Path(..., description="UUID del documento oficial"),
    current_user: Dict = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
) -> Dict[str, Any]:
    """
    Obtiene el contenido HTML de un documento oficial.

    Requiere autenticación Auth0 y valida permisos de visualización
    del usuario sobre el documento.
    """
    document_id = str(document_id)
    # SEC-11: Verificar permisos de visualizacion
    from services.documents.permissions import can_user_view_document
    if not await can_user_view_document(document_id, request.state.tenant_user_id, schema_name=schema_name):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tiene permisos para ver este documento")

    logger.info(f"[GET CONTENT] Usuario {current_user.get('id', '')[:8] if current_user.get('id') else 'unknown'} solicitando contenido de {document_id[:8]}")

    try:
        result = await get_official_document_content(document_id, schema_name)
        logger.info(f"[GET CONTENT] Contenido obtenido exitosamente")
        return result

    except DocumentNotFoundError as e:
        logger.warning(f"[GET CONTENT] Documento no encontrado: {document_id}")
        raise exception_to_http_exception(e)

    except Exception as e:
        logger.error(f"[GET CONTENT] Error inesperado: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al obtener el contenido del documento"
        )
