
from shared.logging import get_logger
from typing import Dict, Any
from datetime import datetime
from fastapi.concurrency import run_in_threadpool
from database import fetch_one, transaction
from shared.exceptions import (
    DocumentNotFoundError,
    DocumentStateError,
    AuthorizationError,
    ConflictError
)
from shared.validation import validate_document_id, validate_user_id
from config.constants import (
    DELETION_SUCCESS_MESSAGE,
    DELETION_PERMISSION_DENIED,
    DELETION_INVALID_STATE_ERROR,
    DELETION_ALREADY_DELETED_ERROR,
    DELETABLE_DOCUMENT_STATES,
    DELETION_PDF_CLEANUP_NOTE,
    DELETION_NO_PDF_NOTE
)
from ..core.queries import (
    get_document_for_deletion_query,
    unlink_document_from_cases_query,
    soft_delete_document_query,
    get_user_info_for_deletion_query
)

logger = get_logger(__name__)


async def delete_document(document_id: str, user_id: str, *, schema_name: str) -> Dict[str, Any]:
    logger.info(
        "Iniciando proceso de eliminacion de documento",
        extra={
            "operation": "delete_document_service",
            "document_id": document_id,
            "user_id": user_id
        }
    )

    await validate_document_id(document_id, schema_name=schema_name)
    await validate_user_id(user_id, schema_name=schema_name)

    document = await fetch_one(get_document_for_deletion_query(), document_id, schema_name=schema_name)

    if not document:
        raise DocumentNotFoundError(document_id)

    if document['is_deleted']:
        raise ConflictError(DELETION_ALREADY_DELETED_ERROR)

    if str(document['created_by']) != str(user_id):
        raise AuthorizationError(DELETION_PERMISSION_DENIED)

    current_status = document['status']
    if current_status not in DELETABLE_DOCUMENT_STATES:
        raise DocumentStateError(
            DELETION_INVALID_STATE_ERROR.format(status=current_status),
            current_state=current_status,
            required_state="draft o rejected"
        )

    pdf_cleanup_info = await _cleanup_pdf_from_r2(document_id, schema_name=schema_name)

    try:
        from services.documents.lifecycle.images import purge_document_images
        await purge_document_images(document_id, schema_name=schema_name)
    except Exception as e:
        logger.warning(f"Error purgando imagenes del documento (soft-fail): {e}")

    try:
        from services.documents.lifecycle.embedded_files import purge_document_embedded_files
        await purge_document_embedded_files(document_id, schema_name=schema_name)
    except Exception as e:
        logger.warning(f"Error purgando adjuntos embebidos del documento (soft-fail): {e}")

    unlinked_count = 0
    async with transaction(schema_name=schema_name) as conn:
        status_str = await conn.execute(unlink_document_from_cases_query(), document_id)
        unlinked_count = int(status_str.split()[-1])

        await conn.execute(soft_delete_document_query(), document_id)

    user_info = await fetch_one(get_user_info_for_deletion_query(), user_id, schema_name=schema_name)

    logger.info(
        "Documento eliminado exitosamente",
        extra={
            "operation": "delete_document_success",
            "document_id": document_id,
            "user_id": user_id,
            "previous_status": current_status,
            "unlinked_cases": unlinked_count
        }
    )

    return {
        "success": True,
        "message": DELETION_SUCCESS_MESSAGE,
        "document_id": document_id,
        "deleted_by": user_id,
        "deleted_by_name": user_info['full_name'] if user_info else "Usuario desconocido",
        "deleted_at": datetime.utcnow().isoformat() + "Z",
        "previous_status": current_status,
        "unlinked_cases": unlinked_count,
        "pdf_cleanup": pdf_cleanup_info
    }


async def _cleanup_pdf_from_r2(document_id: str, *, schema_name: str) -> Dict[str, Any]:
    try:
        from services.storage.cloudflare import get_tenant_r2_client
        r2_client = await get_tenant_r2_client(schema_name=schema_name)

        filename = document_id.replace('-', '') + '.pdf'

        if not await run_in_threadpool(r2_client.exists_tosign, filename):
            return {
                "attempted": False,
                "success": False,
                "error": None,
                "note": DELETION_NO_PDF_NOTE
            }

        await run_in_threadpool(r2_client.delete_tosign, filename)

        return {
            "attempted": True,
            "success": True,
            "error": None,
            "note": DELETION_PDF_CLEANUP_NOTE
        }
    except Exception as e:
        logger.warning(
            "Error limpiando PDF de R2 (soft-fail)",
            extra={
                "operation": "delete_document_pdf_cleanup",
                "document_id": document_id,
                "error": str(e)
            }
        )
        return {
            "attempted": True,
            "success": False,
            "error": str(e),
            "note": DELETION_PDF_CLEANUP_NOTE
        }


async def can_user_delete_document(document_id: str, user_id: str, *, schema_name: str) -> Dict[str, Any]:
    await validate_document_id(document_id, schema_name=schema_name)
    await validate_user_id(user_id, schema_name=schema_name)

    document = await fetch_one(get_document_for_deletion_query(), document_id, schema_name=schema_name)

    if not document:
        return {"can_delete": False, "reason": "Documento no encontrado"}

    if document['is_deleted']:
        return {"can_delete": False, "reason": DELETION_ALREADY_DELETED_ERROR}

    if str(document['created_by']) != str(user_id):
        return {"can_delete": False, "reason": DELETION_PERMISSION_DENIED}

    if document['status'] not in DELETABLE_DOCUMENT_STATES:
        return {
            "can_delete": False,
            "reason": DELETION_INVALID_STATE_ERROR.format(status=document['status'])
        }

    return {
        "can_delete": True,
        "reason": "Usuario es el creador y documento esta en estado eliminable"
    }
