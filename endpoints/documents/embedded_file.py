
from fastapi import APIRouter, Depends, File, UploadFile, HTTPException
from uuid import UUID

from shared.logging import get_logger
from models.tags import Tags
from auth import get_current_user, AuthenticatedUser
from shared.dependencies import get_tenant_schema
from shared.exceptions import exception_to_http_exception
from services.documents.lifecycle.embedded_files import (
    upload_embedded_file,
    list_embedded_files,
    get_embedded_file_download_url,
    delete_embedded_file,
    get_official_embedded_files,
)

logger = get_logger(__name__)

router = APIRouter(tags=[Tags.DOCUMENTOS])


@router.post(
    "/documents/{document_id}/embedded-files",
    summary="Adjuntar archivo embebido (en edición)",
    description=(
        "Sube UN archivo (multipart) que viajará embebido dentro del PDF "
        "firmado. Solo el creador puede adjuntar, solo mientras el documento "
        "está en edición (draft/rejected), y solo si el tipo de documento "
        "tiene habilitado 'Permite adjuntar archivos embebidos'."
    ),
    responses={
        200: {"description": "Adjunto subido exitosamente"},
        400: {"description": "Tipo no permitido, tope excedido, o archivo inválido"},
        403: {"description": "Usuario no es el creador del documento"},
        404: {"description": "Documento no encontrado"},
        409: {"description": "Documento no está en estado editable"},
    },
)
async def upload_embedded_file_endpoint(
    document_id: UUID,
    file: UploadFile = File(..., description="Archivo a adjuntar"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    try:
        content = await file.read()
        result = await upload_embedded_file(
            str(document_id), current_user.user_id, file.filename or "archivo", content,
            schema_name=schema_name,
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error subiendo adjunto embebido de documento {document_id}: {type(e).__name__} - {e}")
        raise exception_to_http_exception(e)


@router.get(
    "/documents/{document_id}/embedded-files",
    summary="Listar adjuntos embebidos (en edición)",
    description="Lista los adjuntos embebidos actuales de un documento en edición, para poblar el panel al reabrir el draft. Requiere permiso de visualización del documento.",
    responses={
        200: {"description": "Lista de adjuntos embebidos"},
        403: {"description": "Usuario sin permiso para ver este documento"},
    },
)
async def list_embedded_files_endpoint(
    document_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    try:
        files = await list_embedded_files(str(document_id), current_user.user_id, schema_name=schema_name)
        return {"document_id": str(document_id), "embedded_files": files, "total": len(files)}
    except Exception as e:
        logger.error(f"Error listando adjuntos embebidos de documento {document_id}: {type(e).__name__} - {e}")
        raise exception_to_http_exception(e)


@router.get(
    "/documents/{document_id}/embedded-files/official",
    summary="Info de adjuntos embebidos del documento OFICIAL firmado",
    description=(
        "Metadata (nombre, tamaño, extensión) de los adjuntos que viajan "
        "embebidos dentro del PDF firmado, sin abrir el PDF. Requiere "
        "permiso de visualización del documento (can_user_view_document)."
    ),
    responses={
        200: {"description": "Lista de adjuntos embebidos"},
        403: {"description": "Usuario sin permiso para ver este documento"},
    },
)
async def get_official_embedded_files_endpoint(
    document_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    try:
        files = await get_official_embedded_files(str(document_id), current_user.user_id, schema_name=schema_name)
        return {"document_id": str(document_id), "embedded_files": files, "total": len(files)}
    except Exception as e:
        logger.error(f"Error obteniendo adjuntos embebidos oficiales de documento {document_id}: {type(e).__name__} - {e}")
        raise exception_to_http_exception(e)


@router.get(
    "/documents/{document_id}/embedded-files/{file_id}",
    summary="Descargar/previsualizar un adjunto embebido (en edición)",
    description="Genera una URL pre-firmada (TTL=1800s) para descargar un adjunto EN EDICIÓN. Requiere permiso de visualización del documento.",
    responses={
        200: {"description": "URL pre-firmada generada"},
        403: {"description": "Usuario sin permiso para ver este documento"},
        404: {"description": "Adjunto no encontrado"},
    },
)
async def get_embedded_file_download_url_endpoint(
    document_id: UUID,
    file_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    try:
        result = await get_embedded_file_download_url(str(document_id), str(file_id), current_user.user_id, schema_name=schema_name)
        return result
    except Exception as e:
        logger.error(f"Error generando URL de adjunto {file_id} de documento {document_id}: {type(e).__name__} - {e}")
        raise exception_to_http_exception(e)


@router.delete(
    "/documents/{document_id}/embedded-files/{file_id}",
    summary="Quitar un adjunto embebido (en edición)",
    description="Elimina un adjunto embebido en edición. Solo el creador, solo mientras el documento está en edición.",
    responses={
        200: {"description": "Adjunto eliminado exitosamente"},
        403: {"description": "Usuario no es el creador del documento"},
        404: {"description": "Adjunto o documento no encontrado"},
        409: {"description": "Documento no está en estado editable"},
    },
)
async def delete_embedded_file_endpoint(
    document_id: UUID,
    file_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    try:
        result = await delete_embedded_file(str(document_id), str(file_id), current_user.user_id, schema_name=schema_name)
        return result
    except Exception as e:
        logger.error(f"Error eliminando adjunto {file_id} de documento {document_id}: {type(e).__name__} - {e}")
        raise exception_to_http_exception(e)
