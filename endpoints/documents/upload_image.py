
from fastapi import APIRouter, Depends, File, Form, UploadFile, Response, Request, HTTPException
from uuid import UUID

from shared.logging import get_logger
from models.tags import Tags
from auth import get_current_user, AuthenticatedUser
from shared.dependencies import get_tenant_schema
from shared.exceptions import exception_to_http_exception
from services.documents.lifecycle.images import (
    upload_document_image,
    get_document_image,
    MAX_IMAGE_BYTES,
)

logger = get_logger(__name__)

router = APIRouter(tags=[Tags.DOCUMENTOS])


@router.post(
    "/documents/{document_id}/images",
    summary="Subir imagen para el editor de documentos",
    description=(
        "Sube una imagen (PNG/JPEG/WEBP, máx 5MB) insertada desde el editor "
        "de un documento Estándar en edición. Se guarda en un bucket R2 "
        "separado y exclusivo de los PDFs; se purga automáticamente cuando "
        "el documento queda numerado (oficial)."
    ),
    responses={
        200: {"description": "Imagen subida exitosamente"},
        400: {"description": "Formato no soportado o datos inválidos"},
        403: {"description": "Usuario sin permiso para editar este documento"},
        404: {"description": "Documento no encontrado"},
        409: {"description": "Documento en un estado que no admite subir imágenes"},
        413: {"description": "Imagen supera el tamaño máximo permitido (5MB)"},
    },
)
async def upload_document_image_endpoint(
    document_id: UUID,
    image: UploadFile = File(..., description="Archivo de imagen (PNG/JPEG/WEBP)"),
    alt_text: str = Form("", description="Texto alternativo de la imagen"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    try:
        data = await image.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="La imagen supera el tamaño máximo permitido (5MB)")

        if len(alt_text) > 255:
            raise HTTPException(status_code=400, detail="alt_text supera el máximo permitido (255 caracteres)")

        result = await upload_document_image(
            str(document_id), data, alt_text, current_user.user_id, schema_name=schema_name
        )
        return {"success": True, **result}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error subiendo imagen de documento {document_id}: {type(e).__name__} - {e}")
        raise exception_to_http_exception(e)


@router.get(
    "/documents/{document_id}/images/{image_id}",
    summary="Obtener imagen de un documento",
    description="Proxy autenticado de lectura de una imagen insertada en el editor.",
    responses={
        200: {"description": "Bytes de la imagen"},
        404: {"description": "Imagen no encontrada (uniforme: incluye sin permiso, anti-enumeración)"},
    },
)
async def get_document_image_endpoint(
    document_id: UUID,
    image_id: UUID,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    try:
        image_bytes, mime_type = await get_document_image(
            str(document_id), str(image_id), current_user.user_id, schema_name=schema_name
        )

        etag = f'"{image_id}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304)

        return Response(
            content=image_bytes,
            media_type=mime_type,
            headers={
                "Cache-Control": "private, no-cache",
                "ETag": etag,
            },
        )

    except Exception as e:
        logger.error(f"Error obteniendo imagen {image_id} de documento {document_id}: {type(e).__name__} - {e}")
        raise exception_to_http_exception(e)
