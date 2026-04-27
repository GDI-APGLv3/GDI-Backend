"""
Servicio para obtener recipients de MEMOS con seguridad BCC.
BCC solo visible para el sender.

Diferencias clave con NOTAS:
- Acceso por user_id en vez de sector_id
- Formato "Nombre (Sector)" en vez de "DEPT#SECTOR"
- No hay check_sector_access, se usa check_memo_user_access
"""

from typing import Dict, List, Any, Optional
from shared.logging import get_logger
from shared.exceptions import NotFoundError, AuthorizationError
from database import get_db_connection
from .queries import (
    get_recipients_by_document_query,
    get_sender_user_query,
    check_user_is_recipient_query,
    check_user_is_sender_query
)

logger = get_logger(__name__)


def get_visible_memo_recipients(
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

    Args:
        document_id: UUID del documento (document_draft.id)
        requesting_user_id: UUID del usuario que solicita
        schema_name: Schema del tenant

    Returns:
        Dict con {
            to: [{user_id, name, sector_acronym}],
            cc: [...],
            bcc: [...] (solo si es sender, o solo el propio BCC),
            is_sender: bool,
            my_recipient_type: str | None
        }

    Raises:
        AuthorizationError: Si el usuario no tiene acceso
    """
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            # Verificar si es sender
            cursor.execute(check_user_is_sender_query(), (document_id, requesting_user_id))
            is_sender_result = cursor.fetchone()
            is_sender = is_sender_result['is_sender'] if is_sender_result else False

            # Verificar si es recipient
            cursor.execute(check_user_is_recipient_query(), (document_id, requesting_user_id))
            recipient_result = cursor.fetchone()
            my_recipient_type = recipient_result['recipient_type'] if recipient_result else None

            # Si no es sender ni recipient, no tiene acceso
            if not is_sender and not my_recipient_type:
                raise AuthorizationError(
                    "No tenes acceso a este memo. "
                    "Solo el emisor y los destinatarios pueden verlo."
                )

            # Obtener todos los recipients
            cursor.execute(get_recipients_by_document_query(), (document_id,))
            all_recipients = cursor.fetchall()

            # Organizar por tipo
            result = {
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
                        # Sender ve todos los BCC
                        result['bcc'].append(recipient_data)
                    elif str(r['recipient_user_id']) == requesting_user_id:
                        # BCC recipient se ve a si mismo
                        if 'bcc' not in result:
                            result['bcc'] = []
                        result['bcc'].append(recipient_data)
                # BCC no se incluye para TO/CC recipients

            logger.debug(
                f"[{schema_name}] Recipients visibles para user {requesting_user_id}: "
                f"is_sender={is_sender}, my_type={my_recipient_type}"
            )

            return result


def get_memo_sender_user(document_id: str, *, schema_name: str) -> Optional[str]:
    """
    Obtiene el user_id del sender de un memo.

    Args:
        document_id: UUID del documento (document_draft.id)
        schema_name: Schema del tenant

    Returns:
        UUID del sender_user_id o None si no tiene recipients
    """
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            cursor.execute(get_sender_user_query(), (document_id,))
            result = cursor.fetchone()
            return str(result['sender_user_id']) if result else None


def check_memo_user_access(
    document_id: str,
    user_id: str,
    *, schema_name: str
) -> Dict[str, Any]:
    """
    Verifica el nivel de acceso de un usuario a un memo.

    Args:
        document_id: UUID del documento (document_draft.id)
        user_id: UUID del usuario
        schema_name: Schema del tenant

    Returns:
        Dict con {has_access, is_sender, recipient_type}
    """
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            # Verificar si es sender
            cursor.execute(check_user_is_sender_query(), (document_id, user_id))
            is_sender_result = cursor.fetchone()
            is_sender = is_sender_result['is_sender'] if is_sender_result else False

            # Verificar si es recipient
            cursor.execute(check_user_is_recipient_query(), (document_id, user_id))
            recipient_result = cursor.fetchone()
            recipient_type = recipient_result['recipient_type'] if recipient_result else None

            return {
                'has_access': is_sender or recipient_type is not None,
                'is_sender': is_sender,
                'recipient_type': recipient_type
            }


def format_memo_recipients_for_pdf(document_id: str, *, schema_name: str) -> Dict[str, Optional[str]]:
    """
    Formatea los recipients de un MEMO para PDFComposer.

    Retorna strings formateados para los endpoints de preview y oficializacion:
    - TO -> "para" (separado por coma)
    - CC -> "cc" (separado por coma, o None si vacio)
    - BCC no se incluye (confidencial)

    Formato de cada recipient: "Nombre (Sector)"
    Ejemplo: "Maria Garcia (MESA), Juan Lopez (TESORERIA)"

    Args:
        document_id: UUID del documento MEMO (document_draft.id)
        schema_name: Schema del tenant

    Returns:
        Dict con:
            - para: str con destinatarios TO (puede ser "" si no hay)
            - cc: str con destinatarios CC o None si no hay

    Example:
        >>> format_memo_recipients_for_pdf("uuid-123", schema_name="tenant_1")
        {"para": "Maria Garcia (MESA), Juan Lopez (TESORERIA)", "cc": "Pedro Ruiz (RRHH)"}
    """
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            # Obtener todos los recipients (excluyendo BCC)
            cursor.execute(get_recipients_by_document_query(), (document_id,))
            all_recipients = cursor.fetchall()

            to_list: List[str] = []
            cc_list: List[str] = []

            for r in all_recipients:
                # Formato: "Nombre (Sector)" o solo "Nombre" si no hay sector
                name = r['recipient_name']
                sector = r['recipient_sector_acronym']
                formatted = f"{name} ({sector})" if sector else name

                if r['recipient_type'] == 'TO':
                    to_list.append(formatted)
                elif r['recipient_type'] == 'CC':
                    cc_list.append(formatted)
                # BCC no se incluye en el PDF

            # Unir con coma
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
