"""
Servicios de archivado para NOTAS.
Permite a los recipients archivar/desarchivar notas recibidas.
"""

from typing import Dict, Any
from datetime import datetime
from shared.logging import get_logger
from shared.exceptions import NotFoundError, AuthorizationError, ValidationError
from database import get_db_connection
from .queries import (
    update_note_archived_status_query,
    get_note_recipient_info_query,
    check_user_is_sender_query
)

logger = get_logger(__name__)


def toggle_note_archive(
    document_id: str,
    sector_id: str,
    archived: bool,
    *, schema_name: str
) -> Dict[str, Any]:
    """
    Archiva o desarchiva una nota para un sector específico.
    Solo recipients pueden archivar (el sender no puede).

    Args:
        document_id: UUID del documento
        sector_id: UUID del sector que archiva
        archived: True para archivar, False para desarchivar
        schema_name: Schema del tenant

    Returns:
        Dict con {
            document_id: str,
            sector_id: str,
            is_archived: bool,
            archived_at: str | None
        }

    Raises:
        NotFoundError: Si el documento no existe o el sector no es recipient
        AuthorizationError: Si el sector es el sender (no puede archivar su propia nota)
    """
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            # Verificar que el sector NO sea el sender
            cursor.execute(check_user_is_sender_query(), (document_id, sector_id))
            is_sender_result = cursor.fetchone()

            if is_sender_result and is_sender_result['is_sender']:
                raise AuthorizationError(
                    "El emisor no puede archivar su propia nota. "
                    "Solo los destinatarios pueden archivar."
                )

            # Verificar que el sector sea recipient
            cursor.execute(get_note_recipient_info_query(), (document_id, sector_id))
            recipient = cursor.fetchone()

            if not recipient:
                raise NotFoundError(
                    f"No se encontró el sector {sector_id} como destinatario "
                    f"de la nota {document_id}"
                )

            # Actualizar estado de archivado
            cursor.execute(
                update_note_archived_status_query(),
                (archived, archived, document_id, sector_id)
            )
            result = cursor.fetchone()

            if not result:
                raise NotFoundError(
                    f"Error al actualizar el estado de archivado para nota {document_id}"
                )

            conn.commit()

            action = "archivada" if archived else "desarchivada"
            logger.info(
                f"[{schema_name}] Nota {document_id} {action} por sector {sector_id}"
            )

            return {
                'document_id': document_id,
                'sector_id': sector_id,
                'is_archived': result['is_archived'],
                'archived_at': result['archived_at'].isoformat() if result['archived_at'] else None
            }


def get_archive_status(
    document_id: str,
    sector_id: str,
    *, schema_name: str
) -> Dict[str, Any]:
    """
    Obtiene el estado de archivado de una nota para un sector específico.

    Args:
        document_id: UUID del documento
        sector_id: UUID del sector
        schema_name: Schema del tenant

    Returns:
        Dict con {
            is_archived: bool,
            archived_at: str | None
        }

    Raises:
        NotFoundError: Si el sector no es recipient del documento
    """
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            cursor.execute(get_note_recipient_info_query(), (document_id, sector_id))
            recipient = cursor.fetchone()

            if not recipient:
                raise NotFoundError(
                    f"No se encontró el sector {sector_id} como destinatario "
                    f"de la nota {document_id}"
                )

            return {
                'is_archived': recipient['is_archived'],
                'archived_at': recipient['archived_at'].isoformat() if recipient['archived_at'] else None
            }
