"""
Servicios de tracking para MEMOS.
Registro de apertura y detalle de memos.

Diferencias clave con NOTAS:
- opened_at es inline en memo_recipients (no tabla notes_openings separada)
- record_memo_opening hace UPDATE en vez de INSERT
- Acceso verificado por user_id (no sector_id)
- No hay variante multi_sector
"""

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
from .recipients import get_visible_memo_recipients, check_memo_user_access
from services.documents.lifecycle.editing import _fetch_proposed_cases

logger = get_logger(__name__)


async def record_memo_opening(
    document_id: str,
    user_id: str,
    *, schema_name: str
) -> Dict[str, Any]:
    """
    Registra la apertura de un memo por un usuario.
    Solo se registra una vez (UPDATE WHERE opened_at IS NULL).

    Returns:
        Dict con {
            recorded: bool (True si es primera apertura),
            opened_at: datetime (fecha de apertura)
        }
    """
    # Verificar que el usuario sea recipient (no sender)
    recipient_result = await fetch_one(
        check_user_is_recipient_query(), document_id, user_id,
        schema_name=schema_name
    )

    if not recipient_result:
        # No es recipient, verificar si es sender
        is_sender_result = await fetch_one(
            check_user_is_sender_query(), document_id, user_id,
            schema_name=schema_name
        )
        if is_sender_result and is_sender_result['is_sender']:
            logger.debug(f"[{schema_name}] Sender {user_id} abrio memo {document_id}, no se registra")
            return {'recorded': False, 'opened_at': None, 'reason': 'sender'}
        else:
            raise AuthorizationError("No tenes acceso a este memo")

    # Intentar marcar como abierto (solo si opened_at IS NULL)
    async with transaction(schema_name=schema_name) as conn:
        result = await conn.fetchrow(record_memo_opening_query(), document_id, user_id)

        if result:
            # Primera apertura
            logger.info(f"[{schema_name}] Usuario {user_id} abrio memo {document_id} por primera vez")
            return {
                'recorded': True,
                'opened_at': result['opened_at'].isoformat() if result['opened_at'] else None
            }
        else:
            # Ya habia sido abierto
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
    """
    Obtiene el detalle completo de un memo.
    Registra la apertura si es recipient y no es sender.

    Raises:
        NotFoundError: Si el memo no existe
        AuthorizationError: Si el usuario no tiene acceso
    """
    # Obtener detalle del memo (official_documents)
    memo = await fetch_one(
        get_memo_detail_query(), document_id,
        schema_name=schema_name
    )

    if not memo:
        raise NotFoundError(f"Memo {document_id} no encontrado")

    # official_documents.id ES el mismo UUID que document_draft.id
    draft_id = document_id

    # Obtener sender info
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

    # Verificar acceso y obtener recipients visibles (usa draft_id para memo_recipients)
    recipients = await get_visible_memo_recipients(draft_id, requesting_user_id, schema_name=schema_name)

    # Registrar apertura si corresponde
    if register_opening:
        opening_result = await record_memo_opening(
            draft_id, requesting_user_id,
            schema_name=schema_name
        )
    else:
        opening_result = {'recorded': False, 'opened_at': None, 'reason': 'view_only'}

    # Obtener aperturas si es sender
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
                'sector_acronym': o['sector_acronym'] or '',
                'recipient_type': o['recipient_type'],
                'opened_at': o['opened_at'].isoformat() if o['opened_at'] else None
            }
            for o in openings_raw
        ]

    # Obtener estado de archivado si es recipient
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

    # Construir respuesta
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

    # Incluir openings solo si es sender
    if openings is not None:
        result['openings'] = openings

    # Incluir expedientes propuestos
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
