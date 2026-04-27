"""
Validaciones para el modulo de MEMOS.
Funciones para validar tipos de documento y destinatarios.

Diferencias clave con NOTAS:
- Valida user_ids en vez de sector_ids
- El sender (user_id) no puede estar en la lista de recipients
- Verifica que usuarios esten activos (is_active = true)
"""

from typing import Dict, List, Any
from uuid import UUID
from shared.logging import get_logger
from shared.exceptions import ValidationError
from database import get_db_connection
from .queries import (
    check_memo_document_type_query,
    check_memo_by_acronym_query,
    validate_users_exist_query
)

logger = get_logger(__name__)


def is_memo_document_type(document_type_id: int, *, schema_name: str) -> bool:
    """
    Verifica si un document_type_id corresponde al tipo MEMO.

    Args:
        document_type_id: ID del tipo de documento
        schema_name: Schema del tenant

    Returns:
        True si es tipo MEMO, False en caso contrario
    """
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            cursor.execute(check_memo_document_type_query(), (document_type_id,))
            result = cursor.fetchone()
            return result is not None


def is_memo_document_type_by_acronym(acronym: str, *, schema_name: str) -> bool:
    """
    Verifica si un acronym corresponde al tipo MEMO.

    Args:
        acronym: Acronimo del tipo de documento
        schema_name: Schema del tenant

    Returns:
        True si es tipo MEMO, False en caso contrario
    """
    return acronym.upper() == 'MEMO'


def get_memo_document_type_id(*, schema_name: str) -> int | None:
    """
    Obtiene el ID del tipo de documento MEMO.

    Args:
        schema_name: Schema del tenant

    Returns:
        ID del tipo MEMO o None si no existe
    """
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            cursor.execute(check_memo_by_acronym_query())
            result = cursor.fetchone()
            return result['id'] if result else None


def validate_memo_recipients_exist(
    cursor,
    recipients: Dict[str, List[str]],
    sender_user_id: str,
    *, schema_name: str
) -> None:
    """
    Valida que los recipients sean validos.

    Validaciones:
    1. Al menos 1 destinatario TO
    2. Todos los user_ids existen y estan activos
    3. El sender no esta en la lista de recipients

    Args:
        cursor: Cursor de la transaccion padre
        recipients: Dict con {to: [], cc: [], bcc: []}
        sender_user_id: UUID del usuario emisor
        schema_name: Schema del tenant (keyword-only)

    Raises:
        ValidationError: Si alguna validacion falla
    """
    # Validar que haya al menos un TO
    to_list = recipients.get('to', [])
    if not to_list:
        raise ValidationError("Un MEMO requiere al menos un destinatario TO")

    # Juntar todos los user_ids
    all_user_ids = (
        to_list +
        recipients.get('cc', []) +
        recipients.get('bcc', [])
    )

    # Remover duplicados preservando orden
    seen = set()
    unique_user_ids = []
    for uid in all_user_ids:
        if uid not in seen:
            seen.add(uid)
            unique_user_ids.append(uid)

    if not unique_user_ids:
        raise ValidationError("No hay destinatarios validos")

    # Validar que el sender no este en recipients
    if sender_user_id in unique_user_ids:
        raise ValidationError("El emisor no puede ser destinatario del memo")

    # Validar formato UUID y convertir a tuple para el query
    try:
        validated_ids = tuple(str(UUID(uid)) for uid in unique_user_ids)
    except ValueError as e:
        raise ValidationError(f"User ID invalido: {e}")

    cursor.execute(validate_users_exist_query(), (list(validated_ids),))
    existing_users = {str(row['id']) for row in cursor.fetchall()}

    missing_users = set(unique_user_ids) - existing_users
    if missing_users:
        raise ValidationError(
            f"Los siguientes usuarios no existen o estan inactivos: {list(missing_users)}"
        )

    logger.info(f"Recipients validados: {len(unique_user_ids)} usuarios validos")


def validate_memo_recipients_input(recipients: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    Normaliza, valida y deduplica la estructura del input de recipients.

    Deduplicacion silenciosa:
    - Dentro de cada lista: [a, a] -> [a]
    - Entre listas (prioridad TO > CC > BCC): si esta en TO, se quita de CC/BCC

    Args:
        recipients: Dict recibido del request

    Returns:
        Dict normalizado y deduplicado con listas de UUIDs

    Raises:
        ValidationError: Si la estructura es invalida
    """
    if not isinstance(recipients, dict):
        raise ValidationError("Recipients debe ser un objeto con claves 'to', 'cc', 'bcc'")

    normalized = {
        'to': [],
        'cc': [],
        'bcc': []
    }

    for key in ['to', 'cc', 'bcc']:
        value = recipients.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            raise ValidationError(f"Recipients.{key} debe ser una lista de UUIDs")
        # Validar que cada elemento sea string (UUID)
        for i, item in enumerate(value):
            if not isinstance(item, str):
                raise ValidationError(f"Recipients.{key}[{i}] debe ser un UUID string")
            normalized[key].append(item)

    # Deduplicar DENTRO de cada lista (preserva orden)
    original_counts = {k: len(v) for k, v in normalized.items()}
    for key in ['to', 'cc', 'bcc']:
        normalized[key] = list(dict.fromkeys(normalized[key]))

    # Deduplicar ENTRE categorias (prioridad: TO > CC > BCC)
    to_set = set(normalized['to'])
    normalized['cc'] = [s for s in normalized['cc'] if s not in to_set]
    normalized['bcc'] = [s for s in normalized['bcc'] if s not in to_set]

    cc_set = set(normalized['cc'])
    normalized['bcc'] = [s for s in normalized['bcc'] if s not in cc_set]

    # Log si hubo deduplicacion
    final_counts = {k: len(v) for k, v in normalized.items()}
    if original_counts != final_counts:
        logger.info(
            f"Recipients deduplicados: original={original_counts}, final={final_counts}"
        )

    return normalized


def is_memo_document_type_by_id(document_id: str, cursor, *, schema_name: str) -> bool:
    """
    Verifica si un documento es tipo MEMO usando su ID.

    Args:
        document_id: UUID del documento
        cursor: Cursor de la transaccion padre
        schema_name: Schema del tenant

    Returns:
        True si es tipo MEMO, False en caso contrario
    """
    query = """
        SELECT dt.acronym
        FROM document_draft dd
        JOIN document_types dt ON dt.id = dd.document_type_id
        WHERE dd.id = %s
    """
    cursor.execute(query, (document_id,))
    result = cursor.fetchone()
    return result is not None and result['acronym'].upper() == 'MEMO'


def validate_memo_recipients_for_signing(document_id: str, *, schema_name: str) -> None:
    """
    Valida recipients de MEMO antes de iniciar firma.

    Validaciones:
    1. Al menos 1 destinatario TO existe
    2. Todos los usuarios siguen activos

    Args:
        document_id: UUID del documento
        schema_name: Schema del tenant

    Raises:
        ValidationError: Si falla alguna validacion
    """
    query = """
        SELECT mr.recipient_user_id, mr.recipient_type,
               (u.estado = 1) as is_active, u.full_name as recipient_name,
               s.acronym as sector_acronym
        FROM memo_recipients mr
        JOIN users u ON u.id = mr.recipient_user_id
        LEFT JOIN sectors s ON s.id = mr.recipient_sector_id
        WHERE mr.document_id = %s
    """
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (document_id,))
            recipients = cursor.fetchall()

            # Validar que haya al menos 1 TO
            to_recipients = [r for r in recipients if r['recipient_type'] == 'TO']
            if not to_recipients:
                raise ValidationError(
                    "Un MEMO requiere al menos un destinatario (TO) para iniciar el proceso de firma. "
                    "Por favor, agregue destinatarios antes de firmar."
                )

            # Validar que todos los usuarios esten activos
            inactive = [
                f"{r['recipient_name']}" + (f" ({r['sector_acronym']})" if r['sector_acronym'] else "")
                for r in recipients if not r['is_active']
            ]
            if inactive:
                raise ValidationError(
                    f"Los siguientes usuarios ya no estan activos: {', '.join(inactive)}. "
                    "Por favor, actualice los destinatarios."
                )
