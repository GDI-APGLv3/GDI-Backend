
from typing import Dict, Any
from shared.logging import get_logger
from shared.exceptions import NotFoundError, AuthorizationError
from database import fetch_one, transaction
from .queries import (
    update_memo_archived_status_query,
    get_memo_recipient_info_query,
    check_user_is_sender_query
)

logger = get_logger(__name__)


async def toggle_memo_archive(
    document_id: str,
    user_id: str,
    archived: bool,
    *, schema_name: str
) -> Dict[str, Any]:
    is_sender_result = await fetch_one(
        check_user_is_sender_query(), document_id, user_id,
        schema_name=schema_name
    )

    if is_sender_result and is_sender_result['is_sender']:
        raise AuthorizationError(
            "El emisor no puede archivar su propio memo. "
            "Solo los destinatarios pueden archivar."
        )

    recipient = await fetch_one(
        get_memo_recipient_info_query(), document_id, user_id,
        schema_name=schema_name
    )

    if not recipient:
        raise NotFoundError(
            f"No se encontro al usuario {user_id} como destinatario "
            f"del memo {document_id}"
        )

    async with transaction(schema_name=schema_name) as conn:
        result = await conn.fetchrow(
            update_memo_archived_status_query(),
            archived, archived, document_id, user_id
        )

    if not result:
        raise NotFoundError(
            f"Error al actualizar el estado de archivado para memo {document_id}"
        )

    action = "archivado" if archived else "desarchivado"
    logger.info(f"[{schema_name}] Memo {document_id} {action} por usuario {user_id}")

    return {
        'document_id': document_id,
        'user_id': user_id,
        'is_archived': result['is_archived'],
        'archived_at': result['archived_at'].isoformat() if result['archived_at'] else None
    }


async def get_memo_archive_status(
    document_id: str,
    user_id: str,
    *, schema_name: str
) -> Dict[str, Any]:
    recipient = await fetch_one(
        get_memo_recipient_info_query(), document_id, user_id,
        schema_name=schema_name
    )

    if not recipient:
        raise NotFoundError(
            f"No se encontro al usuario {user_id} como destinatario "
            f"del memo {document_id}"
        )

    return {
        'is_archived': recipient['is_archived'],
        'archived_at': recipient['archived_at'].isoformat() if recipient['archived_at'] else None
    }
