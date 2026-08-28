
from typing import Dict, List
from shared.logging import get_logger
from database import execute_many
from .queries import insert_note_recipients_bulk_query

logger = get_logger(__name__)


async def save_recipients(
    conn,
    document_id: str,
    sender_sector_id: str,
    recipients: Dict[str, List[str]],
    *,
    schema_name: str,
) -> int:
    sector_ids: List[str] = []
    recipient_types: List[str] = []
    for recipient_type in ["TO", "CC", "BCC"]:
        for sector_id in recipients.get(recipient_type.lower(), []):
            sector_ids.append(sector_id)
            recipient_types.append(recipient_type)

    insert_count = 0
    if sector_ids:
        query = insert_note_recipients_bulk_query()
        status = await execute_many(
            conn, query, document_id, sender_sector_id, sector_ids, recipient_types
        )
        insert_count = int(status.split()[-1])

    logger.info(
        f"[{schema_name}] Guardados {insert_count} recipients para documento {document_id}: "
        f"TO={len(recipients.get('to', []))}, "
        f"CC={len(recipients.get('cc', []))}, "
        f"BCC={len(recipients.get('bcc', []))}"
    )

    return insert_count


async def delete_recipients(conn, document_id: str) -> int:
    query = "DELETE FROM notes_recipients WHERE document_id = $1"
    status = await conn.execute(query, document_id)
    deleted_count = int(status.split()[-1])

    if deleted_count > 0:
        logger.debug(f"Eliminados {deleted_count} recipients del documento {document_id}")

    return deleted_count
