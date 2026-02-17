"""
Servicio para guardar recipients de NOTAS.
Usa el cursor de la transacción padre para atomicidad.
"""

from typing import Dict, List
from shared.logging import get_logger
from .queries import insert_note_recipient_query

logger = get_logger(__name__)


def save_recipients(
    cursor,
    document_id: str,
    sender_sector_id: str,
    recipients: Dict[str, List[str]],
    *, schema_name: str
) -> int:
    """
    Guarda los recipients de una NOTA en la base de datos.
    Usa el cursor de la transacción padre para garantizar atomicidad.

    Args:
        cursor: Cursor de la transacción padre
        document_id: UUID del documento
        sender_sector_id: UUID del sector emisor
        recipients: Dict con {to: [], cc: [], bcc: []}
        schema_name: Schema del tenant (keyword-only, para logging)

    Returns:
        Cantidad de recipients insertados
    """
    insert_count = 0
    query = insert_note_recipient_query()

    # Procesar cada tipo de recipient
    for recipient_type in ['TO', 'CC', 'BCC']:
        sector_ids = recipients.get(recipient_type.lower(), [])

        for sector_id in sector_ids:
            cursor.execute(query, (
                document_id,
                sector_id,
                recipient_type,
                sender_sector_id
            ))
            insert_count += 1

    logger.info(
        f"[{schema_name}] Guardados {insert_count} recipients para documento {document_id}: "
        f"TO={len(recipients.get('to', []))}, "
        f"CC={len(recipients.get('cc', []))}, "
        f"BCC={len(recipients.get('bcc', []))}"
    )

    return insert_count


def delete_recipients(cursor, document_id: str) -> int:
    """
    Elimina todos los recipients de un documento NOTA.
    Usa el cursor de la transacción padre para garantizar atomicidad.

    Args:
        cursor: Cursor de la transacción padre
        document_id: UUID del documento

    Returns:
        Número de registros eliminados
    """
    query = "DELETE FROM notes_recipients WHERE document_id = %s"
    cursor.execute(query, (document_id,))
    deleted_count = cursor.rowcount

    if deleted_count > 0:
        logger.debug(f"Eliminados {deleted_count} recipients del documento {document_id}")

    return deleted_count
