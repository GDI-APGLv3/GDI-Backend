
from typing import Dict, List
from shared.logging import get_logger
from database import execute_many
from .queries import (
    insert_memo_recipients_bulk_query,
    get_user_sector_id_query,
    get_users_sector_ids_bulk_query,
)

logger = get_logger(__name__)


async def save_memo_recipients(
    conn,
    document_id: str,
    sender_user_id: str,
    recipients: Dict[str, List[str]],
    *, schema_name: str
) -> int:
    sender_row = await conn.fetchrow(get_user_sector_id_query(), sender_user_id)
    sender_sector_id = sender_row['sector_id'] if sender_row and sender_row['sector_id'] else None

    user_ids: List[str] = []
    recipient_types: List[str] = []
    for recipient_type in ['TO', 'CC', 'BCC']:
        for user_id in recipients.get(recipient_type.lower(), []):
            user_ids.append(user_id)
            recipient_types.append(recipient_type)

    insert_count = 0
    if user_ids:
        distinct_user_ids = list(dict.fromkeys(user_ids))
        sector_rows = await conn.fetch(get_users_sector_ids_bulk_query(), distinct_user_ids)
        sector_by_user = {str(row['id']): row['sector_id'] for row in sector_rows}
        recipient_sector_ids = [sector_by_user.get(uid) for uid in user_ids]

        query = insert_memo_recipients_bulk_query()
        status = await execute_many(
            conn,
            query,
            document_id,
            sender_user_id,
            sender_sector_id,
            user_ids,
            recipient_types,
            recipient_sector_ids,
        )
        insert_count = int(status.split()[-1])

    logger.info(
        f"[{schema_name}] Guardados {insert_count} recipients para memo {document_id}: "
        f"TO={len(recipients.get('to', []))}, "
        f"CC={len(recipients.get('cc', []))}, "
        f"BCC={len(recipients.get('bcc', []))}"
    )

    return insert_count


async def delete_memo_recipients(conn, document_id: str, *, schema_name: str) -> int:
    query = "DELETE FROM memo_recipients WHERE document_id = $1"
    status = await conn.execute(query, document_id)
    deleted_count = int(status.split()[-1])

    if deleted_count > 0:
        logger.debug(f"[{schema_name}] Eliminados {deleted_count} recipients del memo {document_id}")

    return deleted_count
