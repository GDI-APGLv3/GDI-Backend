"""
Servicio para obtener recipients de NOTAS con seguridad BCC.
BCC solo visible para el sender.
"""

from typing import Dict, List, Any, Optional
from shared.logging import get_logger
from shared.exceptions import NotFoundError, AuthorizationError
from database import get_db_connection
from .queries import (
    get_recipients_by_document_query,
    get_sender_sector_query,
    check_user_is_recipient_query,
    check_user_is_sender_query
)

logger = get_logger(__name__)


def get_visible_recipients(
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

    Args:
        document_id: UUID del documento
        requesting_sector_id: UUID del sector que solicita
        schema_name: Schema del tenant

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
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            # Verificar si es sender
            cursor.execute(check_user_is_sender_query(), (document_id, requesting_sector_id))
            is_sender_result = cursor.fetchone()
            is_sender = is_sender_result['is_sender'] if is_sender_result else False

            # Verificar si es recipient
            cursor.execute(check_user_is_recipient_query(), (document_id, requesting_sector_id))
            recipient_result = cursor.fetchone()
            my_recipient_type = recipient_result['recipient_type'] if recipient_result else None

            # Si no es sender ni recipient, no tiene acceso
            if not is_sender and not my_recipient_type:
                raise AuthorizationError(
                    "No tienes acceso a esta nota. "
                    "Solo el emisor y los destinatarios pueden verla."
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


def get_sender_sector(document_id: str, *, schema_name: str) -> Optional[str]:
    """
    Obtiene el sector_id del sender de una nota.

    Args:
        document_id: UUID del documento
        schema_name: Schema del tenant

    Returns:
        UUID del sender_sector_id o None si no tiene recipients
    """
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            cursor.execute(get_sender_sector_query(), (document_id,))
            result = cursor.fetchone()
            return str(result['sender_sector_id']) if result else None


def check_sector_access(
    document_id: str,
    sector_id: str,
    *, schema_name: str
) -> Dict[str, Any]:
    """
    Verifica el nivel de acceso de un sector a una nota.

    Args:
        document_id: UUID del documento
        sector_id: UUID del sector
        schema_name: Schema del tenant

    Returns:
        Dict con {has_access, is_sender, recipient_type}
    """
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            # Verificar si es sender
            cursor.execute(check_user_is_sender_query(), (document_id, sector_id))
            is_sender_result = cursor.fetchone()
            is_sender = is_sender_result['is_sender'] if is_sender_result else False

            # Verificar si es recipient
            cursor.execute(check_user_is_recipient_query(), (document_id, sector_id))
            recipient_result = cursor.fetchone()
            recipient_type = recipient_result['recipient_type'] if recipient_result else None

            return {
                'has_access': is_sender or recipient_type is not None,
                'is_sender': is_sender,
                'recipient_type': recipient_type
            }


def format_recipients_for_pdf(document_id: str, *, schema_name: str) -> Dict[str, Optional[str]]:
    """
    Formatea los recipients de una NOTA para PDFComposer.

    Retorna strings formateados para los endpoints /note-preview/ y /note/:
    - TO -> "para" (separado por coma)
    - CC -> "cc" (separado por coma, o None si vacío)
    - BCC no se incluye (confidencial)

    Formato de cada recipient: "DEPARTAMENTO#SECTOR"
    Ejemplo: "Secretaría General#Mesa de Entradas, Finanzas#Tesorería"

    Args:
        document_id: UUID del documento NOTA
        schema_name: Schema del tenant

    Returns:
        Dict con:
            - para: str con destinatarios TO (puede ser "" si no hay)
            - cc: str con destinatarios CC o None si no hay

    Example:
        >>> format_recipients_for_pdf("uuid-123", schema_name="tenant_1")
        {"para": "Finanzas#Tesorería, Legales#Asesoría", "cc": "RRHH#Personal"}
    """
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            # Obtener todos los recipients (excluyendo BCC)
            cursor.execute(get_recipients_by_document_query(), (document_id,))
            all_recipients = cursor.fetchall()

            to_list: List[str] = []
            cc_list: List[str] = []

            for r in all_recipients:
                # Formato: "DEPARTAMENTO#SECTOR"
                formatted = f"{r['department_name']}#{r['sector_acronym']}"

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
