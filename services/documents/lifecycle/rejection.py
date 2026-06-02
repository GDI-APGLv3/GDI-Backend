"""
Servicio para rechazar documentos.

Ubicacion real: services/documents/lifecycle/rejection.py
MIGRADO: Fase 6 asyncpg
"""

from shared.logging import get_logger
import uuid
from typing import Dict, Any
from fastapi.concurrency import run_in_threadpool
from database import fetch_one, fetch_all, fetch_val, execute, transaction
from shared.exceptions import (
    DocumentNotFoundError,
    DocumentAlreadyRejectedError,
    DocumentStateError,
    AuthorizationError
)
from shared.validation import validate_document_id, validate_user_id, validate_rejection_reason
from config.constants import (
    REJECTION_SUCCESS_MESSAGE,
    REJECTION_DISPLAY_STATUS,
    REJECTION_ALREADY_REJECTED_ERROR,
    REJECTION_OFFICIAL_DOCUMENT_ERROR,
    REJECTION_USER_CREATOR_REASON,
    REJECTION_USER_SIGNER_REASON,
    REJECTION_USER_NOT_AUTHORIZED,
    REJECTION_PDF_CLEANUP_NOTE,
    REJECTION_NO_PDF_NOTE
)
from ..core.queries import (
    get_document_info_for_rejection_query,
    check_document_is_official_query,
    get_document_generate_id_query,
    update_document_to_rejected_query,
    insert_rejection_record_query,
    update_signers_to_rejected_query,
    get_rejector_info_query,
    check_user_is_signer_query,
    get_document_rejections_history_query,
    get_rejected_documents_for_user_query,
    count_rejected_documents_for_user_query
)

logger = get_logger(__name__)


async def reject_document(document_id: str, user_id: str, reason: str, *, schema_name: str) -> Dict[str, Any]:
    """Rechaza un documento con un motivo especifico.

    Args:
        document_id: UUID del documento
        user_id: UUID del usuario que rechaza
        reason: Motivo del rechazo
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Dict con resultado de la operacion

    Raises:
        DocumentNotFoundError: Documento no existe
        DocumentAlreadyRejectedError: Documento ya rechazado
        DocumentStateError: Documento no puede rechazarse
        AuthorizationError: Usuario sin permisos
    """
    logger.info(
        "Iniciando proceso de rechazo de documento",
        extra={
            "operation": "reject_document_service",
            "document_id": document_id,
            "user_id": user_id,
            "reason_length": len(reason)
        }
    )

    # Validaciones (schema_name se pasa para queries a BD)
    await validate_document_id(document_id, schema_name=schema_name)
    await validate_user_id(user_id, schema_name=schema_name)
    validate_rejection_reason(reason)

    # Obtener documento
    document = await fetch_one(get_document_info_for_rejection_query(), document_id, schema_name=schema_name)

    if not document:
        raise DocumentNotFoundError(document_id)

    # Verificar estado
    if document['status'] == 'rejected':
        raise DocumentAlreadyRejectedError(REJECTION_ALREADY_REJECTED_ERROR)

    # Verificar si es oficial
    official_count = await fetch_val(
        check_document_is_official_query(), document_id, schema_name=schema_name
    )
    if official_count and official_count > 0:
        raise DocumentStateError(
            REJECTION_OFFICIAL_DOCUMENT_ERROR,
            current_state="official",
            required_state="draft, sent_to_sign o signed"
        )

    # Verificar permisos
    can_reject = await _check_user_permissions(document_id, user_id, str(document['created_by']), schema_name=schema_name)
    if not can_reject['allowed']:
        raise AuthorizationError(f"Usuario no autorizado: {can_reject['reason']}")

    # Limpiar PDF de R2 (soft-fail)
    pdf_cleanup_info = await _cleanup_pdf_from_r2(document_id, schema_name=schema_name)

    # Ejecutar operaciones en transaccion
    rejection_id = str(uuid.uuid4())

    async with transaction(schema_name=schema_name) as conn:
        await conn.execute(update_document_to_rejected_query(), document_id)
        await conn.execute(insert_rejection_record_query(), rejection_id, document_id, user_id, reason)
        await conn.execute(update_signers_to_rejected_query(), document_id)

    # Obtener info del usuario
    rejector_info = await fetch_one(get_rejector_info_query(), user_id, schema_name=schema_name)

    from datetime import datetime

    return {
        "success": True,
        "message": REJECTION_SUCCESS_MESSAGE,
        "document_id": document_id,
        "rejected_by": user_id,
        "rejected_by_name": rejector_info['full_name'] if rejector_info else "Usuario desconocido",
        "rejection_id": rejection_id,
        "rejected_at": datetime.utcnow().isoformat() + "Z",
        "reason": reason,
        "new_status": "rejected",
        "display_status": REJECTION_DISPLAY_STATUS,
        "reset_info": {
            "sent_to_sign_at_cleared": True,
            "sent_by_cleared": True,
            "document_generate_id_cleared": True,
            "all_signers_reset_to_pending": True
        },
        "pdf_cleanup": pdf_cleanup_info
    }


async def _check_user_permissions(document_id: str, user_id: str, creator_id: str, *, schema_name: str) -> Dict[str, Any]:
    """Verifica si usuario puede rechazar el documento."""
    if user_id == creator_id:
        return {"allowed": True, "reason": REJECTION_USER_CREATOR_REASON}

    signer = await fetch_one(check_user_is_signer_query(), document_id, user_id, schema_name=schema_name)
    if signer:
        return {"allowed": True, "reason": REJECTION_USER_SIGNER_REASON}

    return {"allowed": False, "reason": REJECTION_USER_NOT_AUTHORIZED}


async def _cleanup_pdf_from_r2(document_id: str, *, schema_name: str) -> Dict[str, Any]:
    """Intenta eliminar PDF de R2 (soft-fail)."""
    doc_gen_result = await fetch_one(get_document_generate_id_query(), document_id, schema_name=schema_name)
    document_generate_id = doc_gen_result.get('document_generate_id') if doc_gen_result else None

    if not document_generate_id:
        return {
            "attempted": False,
            "success": False,
            "error": None,
            "note": REJECTION_NO_PDF_NOTE
        }

    try:
        from services.storage.cloudflare import get_tenant_r2_client
        r2_client = await get_tenant_r2_client(schema_name=schema_name)
        filename = str(document_generate_id).replace('-', '') + '.pdf'
        await run_in_threadpool(r2_client.delete_tosign, filename)

        return {
            "attempted": True,
            "success": True,
            "error": None,
            "note": REJECTION_PDF_CLEANUP_NOTE
        }
    except Exception as e:
        return {
            "attempted": True,
            "success": False,
            "error": str(e),
            "note": REJECTION_PDF_CLEANUP_NOTE
        }


async def get_document_rejections(document_id: str, *, schema_name: str) -> Dict[str, Any]:
    """Obtiene el historial de rechazos de un documento.

    Args:
        document_id: UUID del documento
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Dict con el historial de rechazos
    """
    await validate_document_id(document_id, schema_name=schema_name)

    rejections_data = await fetch_all(get_document_rejections_history_query(), document_id, schema_name=schema_name)

    rejections = []
    for rejection in rejections_data:
        rejections.append({
            "rejection_id": rejection['rejection_id'],
            "reason": rejection['reason'],
            "rejected_by": {
                "name": rejection['rejected_by_name'],
                "email": rejection['rejected_by_email']
            },
            "rejected_at": rejection['rejected_at'].isoformat() if rejection['rejected_at'] else None
        })

    return {
        "document_id": document_id,
        "rejections": rejections,
        "total_rejections": len(rejections),
        "current_rejection": rejections[0] if rejections else None
    }


async def get_rejected_documents_for_user(user_id: str, page: int = 1, page_size: int = 20, *, schema_name: str) -> Dict[str, Any]:
    """Obtiene documentos rechazados donde el usuario es creador o firmante.

    Args:
        user_id: UUID del usuario
        page: Pagina a obtener
        page_size: Tamano de pagina
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Dict con documentos rechazados paginados
    """
    await validate_user_id(user_id, schema_name=schema_name)

    offset = (page - 1) * page_size

    documents_data = await fetch_all(
        get_rejected_documents_for_user_query(),
        user_id, user_id, user_id, page_size, offset,
        schema_name=schema_name
    )

    count_row = await fetch_one(
        count_rejected_documents_for_user_query(),
        user_id, user_id,
        schema_name=schema_name
    )
    total_count = count_row[0] if count_row else 0
    total_pages = (total_count + page_size - 1) // page_size

    return {
        "documents": [dict(r) for r in documents_data],
        "pagination": {
            "current_page": page,
            "page_size": page_size,
            "total_documents": total_count,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_previous": page > 1
        }
    }


async def can_user_reject_document(document_id: str, user_id: str, *, schema_name: str) -> Dict[str, Any]:
    """Verifica si un usuario puede rechazar un documento especifico.

    Args:
        document_id: UUID del documento
        user_id: UUID del usuario
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Dict con can_reject (bool) y reason (str)
    """
    await validate_document_id(document_id, schema_name=schema_name)
    await validate_user_id(user_id, schema_name=schema_name)

    document = await fetch_one(get_document_info_for_rejection_query(), document_id, schema_name=schema_name)

    if not document:
        return {"can_reject": False, "reason": "Documento no encontrado"}

    if document['status'] == 'rejected':
        return {"can_reject": False, "reason": REJECTION_ALREADY_REJECTED_ERROR}

    official_count = await fetch_val(
        check_document_is_official_query(), document_id, schema_name=schema_name
    )
    if official_count and official_count > 0:
        return {"can_reject": False, "reason": REJECTION_OFFICIAL_DOCUMENT_ERROR}

    permission_check = await _check_user_permissions(document_id, user_id, str(document['created_by']), schema_name=schema_name)

    return {
        "can_reject": permission_check['allowed'],
        "reason": permission_check['reason']
    }
