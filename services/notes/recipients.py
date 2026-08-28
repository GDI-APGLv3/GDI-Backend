
from typing import Dict, List, Any, Optional
from shared.logging import get_logger
from shared.exceptions import AuthorizationError
from database import fetch_all, fetch_one
from .queries import (
    get_recipients_by_document_query,
    check_sector_access_query,
)

logger = get_logger(__name__)


async def get_visible_recipients(
    document_id: str,
    requesting_sector_id: str,
    *, schema_name: str
) -> Dict[str, Any]:
    access_row = await fetch_one(
        check_sector_access_query(), document_id, requesting_sector_id,
        schema_name=schema_name
    )
    is_sender = access_row['is_sender'] if access_row else False
    my_recipient_type = access_row['recipient_type'] if access_row else None

    if not is_sender and not my_recipient_type:
        raise AuthorizationError(
            "No tienes acceso a esta nota. "
            "Solo el emisor y los destinatarios pueden verla."
        )

    all_recipients = await fetch_all(
        get_recipients_by_document_query(), document_id,
        schema_name=schema_name
    )

    result: Dict[str, Any] = {
        'to': [],
        'cc': [],
        'is_sender': is_sender,
        'my_recipient_type': my_recipient_type
    }

    if is_sender:
        result['bcc'] = []

    for r in all_recipients:
        recipient_data = {
            'sector_id': str(r['sector_id']),
            'acronym': r['sector_acronym'],
            'department_name': r['department_name'],
            'department_acronym': r.get('department_acronym', '')
        }

        if r['recipient_type'] == 'TO':
            result['to'].append(recipient_data)
        elif r['recipient_type'] == 'CC':
            result['cc'].append(recipient_data)
        elif r['recipient_type'] == 'BCC' and is_sender:
            result['bcc'].append(recipient_data)

    logger.debug(
        f"[{schema_name}] Recipients visibles para sector {requesting_sector_id}: "
        f"is_sender={is_sender}, my_type={my_recipient_type}"
    )

    return result


async def check_sector_access(
    document_id: str,
    sector_id: str,
    *, schema_name: str
) -> Dict[str, Any]:
    access_row = await fetch_one(
        check_sector_access_query(), document_id, sector_id,
        schema_name=schema_name
    )
    is_sender = access_row['is_sender'] if access_row else False
    recipient_type = access_row['recipient_type'] if access_row else None

    return {
        'has_access': is_sender or recipient_type is not None,
        'is_sender': is_sender,
        'recipient_type': recipient_type
    }


async def format_recipients_for_pdf(document_id: str, *, schema_name: str) -> Dict[str, Optional[str]]:
    all_recipients = await fetch_all(
        get_recipients_by_document_query(), document_id,
        schema_name=schema_name
    )

    to_list: List[str] = []
    cc_list: List[str] = []

    for r in all_recipients:
        formatted = f"{r['department_name']}#{r['sector_acronym']}"

        if r['recipient_type'] == 'TO':
            to_list.append(formatted)
        elif r['recipient_type'] == 'CC':
            cc_list.append(formatted)

    para_str = ", ".join(to_list) if to_list else ""
    cc_str = ", ".join(cc_list) if cc_list else None

    logger.debug(
        f"[{schema_name}] Recipients formateados para PDF: "
        f"para='{para_str}', cc='{cc_str}'"
    )

    return {
        'para': para_str,
        'cc': cc_str
    }
