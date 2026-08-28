
import base64
import re
import uuid
from typing import Any, Dict, Tuple

from database import fetch_all, fetch_one, execute
from config.constants import EDITABLE_DOCUMENT_STATES
from shared.exceptions import (
    DocumentNotFoundError,
    DocumentStateError,
    DocumentPermissionError,
    ValidationError,
)
from shared.validation import detect_image_mime
from shared.logging import get_logger

logger = get_logger(__name__)

MAX_IMAGE_BYTES = 5 * 1024 * 1024

_IMAGE_URL_PATTERN = re.compile(
    r'src="(?:/api)?/documents/(?P<document_id>[0-9a-fA-F-]{36})/images/(?P<image_id>[0-9a-fA-F-]{36})"'
)


def _extension_for_mime(mime_type: str) -> str:
    return {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(mime_type, "bin")


async def _fetch_document_for_permission_check(document_id: str, *, schema_name: str) -> Dict[str, Any]:
    row = await fetch_one(
        """
        SELECT d.id, d.status, d.created_by, u.sector_id AS creator_sector_id
        FROM document_draft d
        JOIN users u ON u.id = d.created_by
        WHERE d.id = $1
        """,
        document_id,
        schema_name=schema_name,
    )
    if not row:
        raise DocumentNotFoundError(document_id)
    return dict(row)


async def _validate_can_upload_image(document_id: str, user_id: str, *, schema_name: str) -> Dict[str, Any]:
    from services.case_service import get_user_editable_sector_ids

    document = await _fetch_document_for_permission_check(document_id, schema_name=schema_name)

    if document["status"] not in EDITABLE_DOCUMENT_STATES:
        raise DocumentStateError(
            f"Documento en estado '{document['status']}' no admite subir imagenes",
            current_state=document["status"],
            required_state=" o ".join(EDITABLE_DOCUMENT_STATES),
        )

    if str(document["created_by"]) == str(user_id):
        return document

    editable_sector_ids = await get_user_editable_sector_ids(user_id, schema_name=schema_name)
    if str(document["creator_sector_id"]) in editable_sector_ids:
        return document

    raise DocumentPermissionError(user_id, document_id, "subir imagenes")


async def upload_document_image(
    document_id: str,
    image_bytes: bytes,
    alt_text: str,
    user_id: str,
    *,
    schema_name: str,
) -> Dict[str, Any]:
    from services.storage.cloudflare import get_tenant_r2_client
    from fastapi.concurrency import run_in_threadpool

    await _validate_can_upload_image(document_id, user_id, schema_name=schema_name)

    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValidationError(f"La imagen supera el tamaño máximo permitido ({MAX_IMAGE_BYTES // (1024*1024)}MB)")

    mime_type = detect_image_mime(image_bytes)
    if not mime_type:
        raise ValidationError("Formato de imagen no soportado (solo PNG, JPEG o WEBP)")

    image_id = str(uuid.uuid4())
    ext = _extension_for_mime(mime_type)
    r2_key = f"images/{schema_name}/{document_id}/{image_id}.{ext}"

    r2_client = await get_tenant_r2_client(schema_name=schema_name)
    await run_in_threadpool(r2_client.upload_document_image, image_bytes, r2_key, mime_type)

    await execute(
        """
        INSERT INTO document_images
            (id, document_id, uploaded_by, filename, mime_type, size_bytes, r2_key, alt_text)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        image_id, document_id, user_id, f"{image_id}.{ext}", mime_type, len(image_bytes), r2_key, alt_text or None,
        schema_name=schema_name,
    )

    logger.info(f"Imagen {image_id} subida para documento {document_id[:8]}... ({len(image_bytes)} bytes, {mime_type})")

    return {
        "url": f"/api/documents/{document_id}/images/{image_id}",
        "image_id": image_id,
        "width": None,
        "height": None,
    }


async def get_document_image(
    document_id: str, image_id: str, user_id: str, *, schema_name: str
) -> Tuple[bytes, str]:
    from services.documents.permissions import can_user_view_document
    from services.storage.cloudflare import get_tenant_r2_client
    from fastapi.concurrency import run_in_threadpool

    row = await fetch_one(
        "SELECT r2_key, mime_type FROM document_images WHERE id = $1 AND document_id = $2",
        image_id, document_id,
        schema_name=schema_name,
    )
    if not row:
        raise DocumentNotFoundError(image_id)

    can_view = await can_user_view_document(document_id, user_id, schema_name=schema_name)
    if not can_view:
        raise DocumentNotFoundError(image_id)

    r2_client = await get_tenant_r2_client(schema_name=schema_name)
    image_bytes = await run_in_threadpool(r2_client.get_document_image_bytes, row["r2_key"])
    if image_bytes is None:
        raise DocumentNotFoundError(image_id)

    return image_bytes, row["mime_type"]


async def inline_document_images_as_base64(html: str, document_id: str, *, schema_name: str) -> str:
    if not html or f"/documents/{document_id}/images/" not in html:
        return html

    from services.storage.cloudflare import get_tenant_r2_client
    from fastapi.concurrency import run_in_threadpool

    matches = list(_IMAGE_URL_PATTERN.finditer(html))
    if not matches:
        return html

    image_ids = {m.group("image_id") for m in matches if m.group("document_id") == document_id}
    if not image_ids:
        return html

    rows = await fetch_all(
        "SELECT id, r2_key, mime_type FROM document_images WHERE document_id = $1 AND id = ANY($2::uuid[])",
        document_id, list(image_ids),
        schema_name=schema_name,
    )
    images_by_id = {str(r["id"]): r for r in rows}

    r2_client = await get_tenant_r2_client(schema_name=schema_name)

    result_html = html
    for match in matches:
        if match.group("document_id") != document_id:
            continue
        img = images_by_id.get(match.group("image_id"))
        if not img:
            continue
        image_bytes = await run_in_threadpool(r2_client.get_document_image_bytes, img["r2_key"])
        if image_bytes is None:
            logger.warning(f"inline_document_images_as_base64: bytes no encontrados en R2 para imagen {img['id']}")
            continue
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_uri = f'src="data:{img["mime_type"]};base64,{b64}"'
        result_html = result_html.replace(match.group(0), data_uri)

    return result_html


async def purge_document_images(document_id: str, *, schema_name: str) -> None:
    from services.storage.cloudflare import get_tenant_r2_client
    from fastapi.concurrency import run_in_threadpool

    rows = await fetch_all(
        "SELECT r2_key FROM document_images WHERE document_id = $1",
        document_id,
        schema_name=schema_name,
    )
    if not rows:
        return

    try:
        r2_client = await get_tenant_r2_client(schema_name=schema_name)
        for row in rows:
            await run_in_threadpool(r2_client.delete_document_image, row["r2_key"])
    except Exception as e:
        logger.warning(f"purge_document_images: error limpiando R2 para documento {document_id[:8]}...: {e}")

    await execute(
        "DELETE FROM document_images WHERE document_id = $1",
        document_id,
        schema_name=schema_name,
    )
    logger.info(f"purge_document_images: {len(rows)} imagen(es) purgada(s) para documento {document_id[:8]}...")
