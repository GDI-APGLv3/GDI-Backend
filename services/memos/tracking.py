
from typing import Dict, Any
from shared.logging import get_logger
from shared.exceptions import NotFoundError, AuthorizationError
from database import fetch_all, fetch_one, transaction
from .queries import (
    record_memo_opening_query,
    get_memo_opening_query,
    get_openings_by_document_query,
    get_memo_detail_query,
    check_user_is_recipient_query,
    check_user_is_sender_query,
    get_memo_recipient_info_query,
    get_sender_user_query
)
from .recipients import get_visible_memo_recipients
from services.documents.lifecycle.editing import _fetch_proposed_cases

logger = get_logger(__name__)


async def record_memo_opening(
    document_id: str,
    user_id: str,
    *, schema_name: str
) -> Dict[str, Any]:
    recipient_result = await fetch_one(
        check_user_is_recipient_query(), document_id, user_id,
        schema_name=schema_name
    )

    if not recipient_result:
        is_sender_result = await fetch_one(
            check_user_is_sender_query(), document_id, user_id,
            schema_name=schema_name
        )
        if is_sender_result and is_sender_result['is_sender']:
            logger.debug(f"[{schema_name}] Sender {user_id} abrio memo {document_id}, no se registra")
            return {'recorded': False, 'opened_at': None, 'reason': 'sender'}
        else:
            raise AuthorizationError("No tenes acceso a este memo")

    async with transaction(schema_name=schema_name) as conn:
        result = await conn.fetchrow(record_memo_opening_query(), document_id, user_id)

        if result:
            logger.info(f"[{schema_name}] Usuario {user_id} abrio memo {document_id} por primera vez")
            return {
                'recorded': True,
                'opened_at': result['opened_at'].isoformat() if result['opened_at'] else None
            }
        else:
            opening = await conn.fetchrow(get_memo_opening_query(), document_id, user_id)
            logger.debug(f"[{schema_name}] Usuario {user_id} reabrio memo {document_id}")
            return {
                'recorded': False,
                'opened_at': opening['opened_at'].isoformat() if opening and opening['opened_at'] else None
            }


async def get_memo_detail(
    document_id: str,
    requesting_user_id: str,
    *,
    register_opening: bool = True,
    schema_name: str
) -> Dict[str, Any]:
    memo = await fetch_one(
        get_memo_detail_query(), document_id,
        schema_name=schema_name
    )

    if not memo:
        raise NotFoundError(f"Memo {document_id} no encontrado")

    draft_id = document_id

    sender_info = None
    sender_row = await fetch_one(
        get_sender_user_query(), draft_id,
        schema_name=schema_name
    )
    if sender_row:
        sender_user_id = str(sender_row['sender_user_id'])
        sender_user_row = await fetch_one(
            """SELECT u.full_name, s.acronym as sector_acronym
               FROM users u
               LEFT JOIN sectors s ON s.id = u.sector_id
               WHERE u.id = $1""",
            sender_user_id,
            schema_name=schema_name
        )
        if sender_user_row:
            sender_info = {
                'user_id': sender_user_id,
                'full_name': sender_user_row['full_name'],
                'sector_acronym': sender_user_row['sector_acronym'] or ''
            }

    recipients = await get_visible_memo_recipients(draft_id, requesting_user_id, schema_name=schema_name)

    if register_opening:
        opening_result = await record_memo_opening(
            draft_id, requesting_user_id,
            schema_name=schema_name
        )
    else:
        opening_result = {'recorded': False, 'opened_at': None, 'reason': 'view_only'}

    openings = None
    if recipients['is_sender']:
        openings_raw = await fetch_all(
            get_openings_by_document_query(), draft_id,
            schema_name=schema_name
        )
        openings = [
            {
                'user_id': str(o['user_id']),
                'full_name': o['user_name'],
                'sector_id': str(o['sector_id']) if o.get('sector_id') else None,
                'sector_acronym': o['sector_acronym'] or '',
                'sector_color': o.get('sector_color'),
                'profile_picture_url': o.get('profile_picture_url'),
                'seal_name': o.get('seal_name'),
                'recipient_type': o['recipient_type'],
                'opened_at': o['opened_at'].isoformat() if o['opened_at'] else None
            }
            for o in openings_raw
        ]

    is_archived = False
    archived_at = None
    if not recipients['is_sender']:
        recipient_info = await fetch_one(
            get_memo_recipient_info_query(), draft_id, requesting_user_id,
            schema_name=schema_name
        )
        if recipient_info:
            is_archived = recipient_info['is_archived']
            archived_at = recipient_info['archived_at'].isoformat() if recipient_info['archived_at'] else None

    result = {
        'document_id': str(memo['id']),
        'official_number': memo['official_number'],
        'reference': memo['reference'],
        'content': memo['content'],
        'signed_at': memo['signed_at'].isoformat() if memo['signed_at'] else None,
        'ai_summary': memo['ai_summary'],
        'signers': memo['signers'],
        'document_type': {
            'name': memo['document_type_name'],
            'acronym': memo['document_type_acronym']
        },
        'department_name': memo['department_name'],
        'sender': sender_info,
        'recipients': recipients,
        'my_access': {
            'is_sender': recipients['is_sender'],
            'recipient_type': recipients['my_recipient_type'],
            'user_id': requesting_user_id,
            'first_open': opening_result['recorded'] if opening_result else False,
            'opened_at': opening_result['opened_at'] if opening_result else None,
            'is_archived': is_archived,
            'archived_at': archived_at
        }
    }

    if openings is not None:
        result['openings'] = openings

    try:
        proposed_cases = await _fetch_proposed_cases(document_id, schema_name=schema_name)
        if proposed_cases:
            result['proposed_cases'] = proposed_cases
    except Exception as e:
        logger.warning(f"[{schema_name}] Error al obtener proposed_cases para memo {document_id}: {e}")

    logger.info(
        f"[{schema_name}] Memo {document_id} accedido por user {requesting_user_id} "
        f"(is_sender={recipients['is_sender']}, type={recipients['my_recipient_type']})"
    )

    return result
