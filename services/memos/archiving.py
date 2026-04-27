"""
Servicios de archivado para MEMOS.
Permite a los recipients archivar/desarchivar memos recibidos.

Diferencias clave con NOTAS:
- El usuario archiva para si mismo (user_id en vez de sector_id)
- Verificacion de acceso por user_id
"""

from typing import Dict, Any
from shared.logging import get_logger
from shared.exceptions import NotFoundError, AuthorizationError
from database import get_db_connection
from .queries import (
    update_memo_archived_status_query,
    get_memo_recipient_info_query,
    check_user_is_sender_query
)

logger = get_logger(__name__)


def toggle_memo_archive(
    document_id: str,
    user_id: str,
    archived: bool,
    *, schema_name: str
) -> Dict[str, Any]:
    """
    Archiva o desarchiva un memo para un usuario especifico.
    Solo recipients pueden archivar (el sender no puede).

    Args:
        document_id: UUID del documento (document_draft.id)
        user_id: UUID del usuario que archiva
        archived: True para archivar, False para desarchivar
        schema_name: Schema del tenant

    Returns:
        Dict con {
            document_id: str,
            user_id: str,
            is_archived: bool,
            archived_at: str | None
        }

    Raises:
        NotFoundError: Si el documento no existe o el usuario no es recipient
        AuthorizationError: Si el usuario es el sender (no puede archivar su propio memo)
    """
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            # Verificar que el usuario NO sea el sender
            cursor.execute(check_user_is_sender_query(), (document_id, user_id))
            is_sender_result = cursor.fetchone()

            if is_sender_result and is_sender_result['is_sender']:
                raise AuthorizationError(
                    "El emisor no puede archivar su propio memo. "
                    "Solo los destinatarios pueden archivar."
                )

            # Verificar que el usuario sea recipient
            cursor.execute(get_memo_recipient_info_query(), (document_id, user_id))
            recipient = cursor.fetchone()

            if not recipient:
                raise NotFoundError(
                    f"No se encontro al usuario {user_id} como destinatario "
                    f"del memo {document_id}"
                )

            # Actualizar estado de archivado
            cursor.execute(
                update_memo_archived_status_query(),
                (archived, archived, document_id, user_id)
            )
            result = cursor.fetchone()

            if not result:
                raise NotFoundError(
                    f"Error al actualizar el estado de archivado para memo {document_id}"
                )

            conn.commit()

            action = "archivado" if archived else "desarchivado"
            logger.info(
                f"[{schema_name}] Memo {document_id} {action} por usuario {user_id}"
            )

            return {
                'document_id': document_id,
                'user_id': user_id,
                'is_archived': result['is_archived'],
                'archived_at': result['archived_at'].isoformat() if result['archived_at'] else None
            }


def get_memo_archive_status(
    document_id: str,
    user_id: str,
    *, schema_name: str
) -> Dict[str, Any]:
    """
    Obtiene el estado de archivado de un memo para un usuario especifico.

    Args:
        document_id: UUID del documento (document_draft.id)
        user_id: UUID del usuario
        schema_name: Schema del tenant

    Returns:
        Dict con {
            is_archived: bool,
            archived_at: str | None
        }

    Raises:
        NotFoundError: Si el usuario no es recipient del documento
    """
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            cursor.execute(get_memo_recipient_info_query(), (document_id, user_id))
            recipient = cursor.fetchone()

            if not recipient:
                raise NotFoundError(
                    f"No se encontro al usuario {user_id} como destinatario "
                    f"del memo {document_id}"
                )

            return {
                'is_archived': recipient['is_archived'],
                'archived_at': recipient['archived_at'].isoformat() if recipient['archived_at'] else None
            }
