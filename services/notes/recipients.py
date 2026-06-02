"""
Servicio para obtener recipients de NOTAS con seguridad BCC.
BCC solo visible para el sender.
"""

from typing import Dict, List, Any, Optional
from shared.logging import get_logger
from shared.exceptions import NotFoundError, AuthorizationError
from database import fetch_all, fetch_one
from .queries import (
    get_recipients_by_document_query,
    get_sender_sector_query,
    check_user_is_recipient_query,
    check_user_is_sender_query
)

logger = get_logger(__name__)


async def get_visible_recipients(
    document_id: str,
    requesting_sector_id: str,
    *, schema_name: str
) -> Dict[str, Any]:
    """
    Obtiene los recipients visibles para un sector específico.

    Reglas de seguridad:
    - Si es SENDER: ve TO, CC, BCC (todo)
    - Si es DESTINATARIO: ve TO, CC (nunca BCC)
    - Si no es ni sender ni destinatario: error 403

    Returns:
        Dict con {
            to: [{sector_id, acronym, department_name}],
            cc: [...],
            bcc: [...] (solo si es sender),
            is_sender: bool,
            my_recipient_type: str | None
        }

    Raises:
        AuthorizationError: Si el sector no tiene acceso
    """
    # Verificar si es sender
    is_sender_row = await fetch_one(
        check_user_is_sender_query(), document_id, requesting_sector_id,
        schema_name=schema_name
    )
    is_sender = is_sender_row['is_sender'] if is_sender_row else False

    # Verificar si es recipient
    recipient_row = await fetch_one(
        check_user_is_recipient_query(), document_id, requesting_sector_id,
        schema_name=schema_name
    )
    my_recipient_type = recipient_row['recipient_type'] if recipient_row else None

    # Si no es sender ni recipient, no tiene acceso
    if not is_sender and not my_recipient_type:
        raise AuthorizationError(
            "No tienes acceso a esta nota. "
            "Solo el emisor y los destinatarios pueden verla."
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
        # BCC no se incluye si no es sender

    logger.debug(
        f"[{schema_name}] Recipients visibles para sector {requesting_sector_id}: "
        f"is_sender={is_sender}, my_type={my_recipient_type}"
    )

    return result


async def get_sender_sector(document_id: str, *, schema_name: str) -> Optional[str]:
    """
    Obtiene el sector_id del sender de una nota.

    Returns:
        UUID del sender_sector_id o None si no tiene recipients
    """
    result = await fetch_one(
        get_sender_sector_query(), document_id,
        schema_name=schema_name
    )
    return str(result['sender_sector_id']) if result else None


async def check_sector_access(
    document_id: str,
    sector_id: str,
    *, schema_name: str
) -> Dict[str, Any]:
    """
    Verifica el nivel de acceso de un sector a una nota.

    Returns:
        Dict con {has_access, is_sender, recipient_type}
    """
    # Verificar si es sender
    is_sender_row = await fetch_one(
        check_user_is_sender_query(), document_id, sector_id,
        schema_name=schema_name
    )
    is_sender = is_sender_row['is_sender'] if is_sender_row else False

    # Verificar si es recipient
    recipient_row = await fetch_one(
        check_user_is_recipient_query(), document_id, sector_id,
        schema_name=schema_name
    )
    recipient_type = recipient_row['recipient_type'] if recipient_row else None

    return {
        'has_access': is_sender or recipient_type is not None,
        'is_sender': is_sender,
        'recipient_type': recipient_type
    }


async def format_recipients_for_pdf(document_id: str, *, schema_name: str) -> Dict[str, Optional[str]]:
    """
    Formatea los recipients de una NOTA para PDFComposer.

    - TO -> "para" (separado por coma)
    - CC -> "cc" (separado por coma, o None si vacío)
    - BCC no se incluye (confidencial)

    Formato: "DEPARTAMENTO#SECTOR"
    """
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
