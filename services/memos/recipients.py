"""
Servicio para obtener recipients de MEMOS con seguridad BCC.
BCC solo visible para el sender.

Diferencias clave con NOTAS:
- Acceso por user_id en vez de sector_id
- Formato "Nombre (Sector)" en vez de "DEPT#SECTOR"
"""

from typing import Dict, List, Any, Optional
from shared.logging import get_logger
from shared.exceptions import NotFoundError, AuthorizationError
from database import fetch_all, fetch_one
from .queries import (
    get_recipients_by_document_query,
    get_sender_user_query,
    check_user_is_recipient_query,
    check_user_is_sender_query
)

logger = get_logger(__name__)


async def get_visible_memo_recipients(
    document_id: str,
    requesting_user_id: str,
    *, schema_name: str
) -> Dict[str, Any]:
    """
    Obtiene los recipients visibles para un usuario especifico.

    Reglas de seguridad:
    - Si es SENDER: ve TO, CC, BCC (todo)
    - Si es DESTINATARIO TO/CC: ve TO, CC (nunca BCC)
    - Si es DESTINATARIO BCC: ve TO, CC + se ve a si mismo como BCC
    - Si no es ni sender ni destinatario: error 403

    Raises:
        AuthorizationError: Si el usuario no tiene acceso
    """
    # Verificar si es sender
    is_sender_result = await fetch_one(
        check_user_is_sender_query(), document_id, requesting_user_id,
        schema_name=schema_name
    )
    is_sender = is_sender_result['is_sender'] if is_sender_result else False

    # Verificar si es recipient
    recipient_result = await fetch_one(
        check_user_is_recipient_query(), document_id, requesting_user_id,
        schema_name=schema_name
    )
    my_recipient_type = recipient_result['recipient_type'] if recipient_result else None

    # Si no es sender ni recipient, no tiene acceso
    if not is_sender and not my_recipient_type:
        raise AuthorizationError(
            "No tenes acceso a este memo. "
            "Solo el emisor y los destinatarios pueden verlo."
        )

    # Obtener todos los recipients
    all_recipients = await fetch_all(
        get_recipients_by_document_query(), document_id,
        schema_name=schema_name
    )

    # Organizar por tipo
    result: Dict[str, Any] = {
        'to': [],
        'cc': [],
        'is_sender': is_sender,
        'my_recipient_type': my_recipient_type
    }

    # Solo incluir BCC si es sender
    if is_sender:
        result['bcc'] = []

    for r in all_recipients:
        recipient_data = {
            'user_id': str(r['recipient_user_id']),
            'full_name': r['recipient_name'],
            'sector_acronym': r['recipient_sector_acronym'] or ''
        }

        if r['recipient_type'] == 'TO':
            result['to'].append(recipient_data)
        elif r['recipient_type'] == 'CC':
            result['cc'].append(recipient_data)
        elif r['recipient_type'] == 'BCC':
            if is_sender:
                result['bcc'].append(recipient_data)
            elif str(r['recipient_user_id']) == requesting_user_id:
                if 'bcc' not in result:
                    result['bcc'] = []
                result['bcc'].append(recipient_data)
        # BCC no se incluye para TO/CC recipients

    logger.debug(
        f"[{schema_name}] Recipients visibles para user {requesting_user_id}: "
        f"is_sender={is_sender}, my_type={my_recipient_type}"
    )

    return result


async def get_memo_sender_user(document_id: str, *, schema_name: str) -> Optional[str]:
    """
    Obtiene el user_id del sender de un memo.

    Returns:
        UUID del sender_user_id o None si no tiene recipients
    """
    result = await fetch_one(
        get_sender_user_query(), document_id,
        schema_name=schema_name
    )
    return str(result['sender_user_id']) if result else None


async def check_memo_user_access(
    document_id: str,
    user_id: str,
    *, schema_name: str
) -> Dict[str, Any]:
    """
    Verifica el nivel de acceso de un usuario a un memo.

    Returns:
        Dict con {has_access, is_sender, recipient_type}
    """
    is_sender_result = await fetch_one(
        check_user_is_sender_query(), document_id, user_id,
        schema_name=schema_name
    )
    is_sender = is_sender_result['is_sender'] if is_sender_result else False

    recipient_result = await fetch_one(
        check_user_is_recipient_query(), document_id, user_id,
        schema_name=schema_name
    )
    recipient_type = recipient_result['recipient_type'] if recipient_result else None

    return {
        'has_access': is_sender or recipient_type is not None,
        'is_sender': is_sender,
        'recipient_type': recipient_type
    }


async def format_memo_recipients_for_pdf(document_id: str, *, schema_name: str) -> Dict[str, Optional[str]]:
    """
    Formatea los recipients de un MEMO para PDFComposer.

    - TO -> "para" (separado por coma)
    - CC -> "cc" (separado por coma, o None si vacio)
    - BCC no se incluye (confidencial)

    Formato: "Nombre (Sector)"
    """
    all_recipients = await fetch_all(
        get_recipients_by_document_query(), document_id,
        schema_name=schema_name
    )

    to_list: List[str] = []
    cc_list: List[str] = []

    for r in all_recipients:
        name = r['recipient_name']
        sector = r['recipient_sector_acronym']
        formatted = f"{name} ({sector})" if sector else name

        if r['recipient_type'] == 'TO':
            to_list.append(formatted)
        elif r['recipient_type'] == 'CC':
            cc_list.append(formatted)
        # BCC no se incluye en el PDF

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
