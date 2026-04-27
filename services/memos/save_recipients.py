"""
Servicio para guardar recipients de MEMOS.
Usa el cursor de la transaccion padre para atomicidad.

Diferencias clave con NOTAS:
- INSERT incluye recipient_sector_id y sender_sector_id (snapshots)
- Se obtiene el sector_id actual de cada usuario para el snapshot
"""

from typing import Dict, List
from shared.logging import get_logger
from .queries import insert_memo_recipient_query, get_user_sector_id_query

logger = get_logger(__name__)


def save_memo_recipients(
    cursor,
    document_id: str,
    sender_user_id: str,
    recipients: Dict[str, List[str]],
    *, schema_name: str
) -> int:
    """
    Guarda los recipients de un MEMO en la base de datos.
    Usa el cursor de la transaccion padre para garantizar atomicidad.

    Obtiene el sector_id actual de cada recipient y del sender como snapshot.

    Args:
        cursor: Cursor de la transaccion padre
        document_id: UUID del documento (document_draft.id)
        sender_user_id: UUID del usuario emisor
        recipients: Dict con {to: [], cc: [], bcc: []}
        schema_name: Schema del tenant (keyword-only, para logging)

    Returns:
        Cantidad de recipients insertados
    """
    insert_count = 0
    query = insert_memo_recipient_query()
    sector_query = get_user_sector_id_query()

    # Obtener sector_id del sender (snapshot)
    cursor.execute(sector_query, (sender_user_id,))
    sender_row = cursor.fetchone()
    sender_sector_id = sender_row['sector_id'] if sender_row and sender_row['sector_id'] else None

    # Cache de sector_ids para evitar queries repetidas
    sector_cache: Dict[str, str | None] = {}

    # Procesar cada tipo de recipient
    for recipient_type in ['TO', 'CC', 'BCC']:
        user_ids = recipients.get(recipient_type.lower(), [])

        for user_id in user_ids:
            # Obtener sector_id del recipient (snapshot, con cache)
            if user_id not in sector_cache:
                cursor.execute(sector_query, (user_id,))
                row = cursor.fetchone()
                sector_cache[user_id] = row['sector_id'] if row and row['sector_id'] else None

            recipient_sector_id = sector_cache[user_id]

            cursor.execute(query, (
                document_id,
                user_id,
                sender_user_id,
                recipient_type,
                recipient_sector_id,
                sender_sector_id
            ))
            insert_count += 1

    logger.info(
        f"[{schema_name}] Guardados {insert_count} recipients para memo {document_id}: "
        f"TO={len(recipients.get('to', []))}, "
        f"CC={len(recipients.get('cc', []))}, "
        f"BCC={len(recipients.get('bcc', []))}"
    )

    return insert_count


def delete_memo_recipients(cursor, document_id: str, *, schema_name: str) -> int:
    """
    Elimina todos los recipients de un documento MEMO.
    Usa el cursor de la transaccion padre para garantizar atomicidad.

    Args:
        cursor: Cursor de la transaccion padre
        document_id: UUID del documento (document_draft.id)

    Returns:
        Numero de registros eliminados
    """
    query = "DELETE FROM memo_recipients WHERE document_id = %s"
    cursor.execute(query, (document_id,))
    deleted_count = cursor.rowcount

    if deleted_count > 0:
        logger.debug(f"[{schema_name}] Eliminados {deleted_count} recipients del memo {document_id}")

    return deleted_count
