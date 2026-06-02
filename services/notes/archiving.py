"""
Servicios de archivado para NOTAS.
Permite a los recipients archivar/desarchivar notas recibidas.
"""

from typing import Dict, Any
from shared.logging import get_logger
from shared.exceptions import NotFoundError, AuthorizationError
from database import fetch_one, transaction
from .queries import (
    update_note_archived_status_query,
    get_note_recipient_info_query,
    check_user_is_sender_query,
)

logger = get_logger(__name__)


async def toggle_note_archive(
    document_id: str,
    sector_id: str,
    archived: bool,
    *,
    schema_name: str,
) -> Dict[str, Any]:
    """
    Archiva o desarchiva una nota para un sector específico.
    Solo recipients pueden archivar (el sender no puede).

    Raises:
        NotFoundError: Si el documento no existe o el sector no es recipient.
        AuthorizationError: Si el sector es el sender.
    """
    async with transaction(schema_name=schema_name) as conn:
        is_sender_row = await conn.fetchrow(check_user_is_sender_query(), document_id, sector_id)
        if is_sender_row and is_sender_row["is_sender"]:
            raise AuthorizationError(
                "El emisor no puede archivar su propia nota. "
                "Solo los destinatarios pueden archivar."
            )

        recipient = await conn.fetchrow(get_note_recipient_info_query(), document_id, sector_id)
        if not recipient:
            raise NotFoundError(
                f"No se encontró el sector {sector_id} como destinatario "
                f"de la nota {document_id}"
            )

        result = await conn.fetchrow(
            update_note_archived_status_query(), archived, archived, document_id, sector_id
        )
        if not result:
            raise NotFoundError(
                f"Error al actualizar el estado de archivado para nota {document_id}"
            )

    action = "archivada" if archived else "desarchivada"
    logger.info(f"[{schema_name}] Nota {document_id} {action} por sector {sector_id}")

    return {
        "document_id": document_id,
        "sector_id": sector_id,
        "is_archived": result["is_archived"],
        "archived_at": result["archived_at"].isoformat() if result["archived_at"] else None,
    }


async def get_archive_status(
    document_id: str,
    sector_id: str,
    *,
    schema_name: str,
) -> Dict[str, Any]:
    """
    Obtiene el estado de archivado de una nota para un sector específico.

    Raises:
        NotFoundError: Si el sector no es recipient del documento.
    """
    recipient = await fetch_one(
        get_note_recipient_info_query(), document_id, sector_id, schema_name=schema_name
    )
    if not recipient:
        raise NotFoundError(
            f"No se encontró el sector {sector_id} como destinatario "
            f"de la nota {document_id}"
        )

    return {
        "is_archived": recipient["is_archived"],
        "archived_at": recipient["archived_at"].isoformat() if recipient["archived_at"] else None,
    }
