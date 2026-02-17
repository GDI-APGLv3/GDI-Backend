"""
Servicio para eliminar documentos (soft delete).

Ubicacion real: services/documents/lifecycle/deletion.py
"""

from shared.logging import get_logger
from typing import Dict, Any
from datetime import datetime
from database import get_db_connection, execute_transaction
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


def delete_document(document_id: str, user_id: str, *, schema_name: str) -> Dict[str, Any]:
    """Elimina un documento (soft delete).

    Solo el creador puede eliminar documentos en estado 'draft' o 'rejected'.
    Si el documento esta vinculado a expedientes, se desvincula automaticamente.

    Args:
        document_id: UUID del documento
        user_id: UUID del usuario que elimina (debe ser el creador)
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Dict con resultado de la operacion

    Raises:
        DocumentNotFoundError: Documento no existe
        ConflictError: Documento ya eliminado previamente
        AuthorizationError: Usuario no es el creador
        DocumentStateError: Estado no permite eliminacion
    """
    logger.info(
        "Iniciando proceso de eliminacion de documento",
        extra={
            "operation": "delete_document_service",
            "document_id": document_id,
            "user_id": user_id
        }
    )

    # Validaciones de formato
    validate_document_id(document_id, schema_name=schema_name)
    validate_user_id(user_id, schema_name=schema_name)

    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            # 1. Obtener documento
            cursor.execute(get_document_for_deletion_query(), (document_id,))
            document = cursor.fetchone()

            if not document:
                raise DocumentNotFoundError(document_id)

            # 2. Verificar si ya esta eliminado
            if document['is_deleted']:
                raise ConflictError(DELETION_ALREADY_DELETED_ERROR)

            # 3. Verificar que el usuario sea el creador
            if str(document['created_by']) != str(user_id):
                raise AuthorizationError(DELETION_PERMISSION_DENIED)

            # 4. Verificar estado permitido
            current_status = document['status']
            if current_status not in DELETABLE_DOCUMENT_STATES:
                raise DocumentStateError(
                    DELETION_INVALID_STATE_ERROR.format(status=current_status),
                    current_state=current_status,
                    required_state="draft o rejected"
                )

            # 5. Limpiar PDF de R2 (soft-fail, antes de la transaccion)
            pdf_cleanup_info = _cleanup_pdf_from_r2(document_id, schema_name=schema_name)

            # 6. Ejecutar operaciones en transaccion
            with execute_transaction(schema_name=schema_name) as (conn_tx, cursor_tx):
                # Desvincular de expedientes
                cursor_tx.execute(unlink_document_from_cases_query(), (document_id,))
                unlinked_count = cursor_tx.rowcount

                # Soft delete
                cursor_tx.execute(soft_delete_document_query(), (document_id,))

            # 7. Obtener info del usuario
            cursor.execute(get_user_info_for_deletion_query(), (user_id,))
            user_info = cursor.fetchone()

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


def _cleanup_pdf_from_r2(document_id: str, *, schema_name: str) -> Dict[str, Any]:
    """Intenta eliminar PDF de R2 (soft-fail).

    Args:
        document_id: UUID del documento
        schema_name: Schema del tenant

    Returns:
        Dict con informacion del intento de limpieza
    """
    try:
        from services.storage.cloudflare import get_tenant_r2_client
        r2_client = get_tenant_r2_client(schema_name=schema_name)

        # El filename es el UUID sin guiones + .pdf
        filename = document_id.replace('-', '') + '.pdf'

        # Verificar si existe antes de intentar eliminar
        if not r2_client.exists_tosign(filename):
            return {
                "attempted": False,
                "success": False,
                "error": None,
                "note": DELETION_NO_PDF_NOTE
            }

        r2_client.delete_tosign(filename)

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


def can_user_delete_document(document_id: str, user_id: str, *, schema_name: str) -> Dict[str, Any]:
    """Verifica si un usuario puede eliminar un documento especifico.

    Args:
        document_id: UUID del documento
        user_id: UUID del usuario
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Dict con can_delete (bool) y reason (str)
    """
    validate_document_id(document_id, schema_name=schema_name)
    validate_user_id(user_id, schema_name=schema_name)

    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            cursor.execute(get_document_for_deletion_query(), (document_id,))
            document = cursor.fetchone()

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
