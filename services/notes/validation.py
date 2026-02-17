"""
Validaciones para el módulo de NOTAS.
Funciones para validar tipos de documento y destinatarios.
"""

from typing import Dict, List, Any
from uuid import UUID
from shared.logging import get_logger
from shared.exceptions import ValidationError
from database import get_db_connection
from .queries import (
    check_nota_document_type_query,
    check_nota_by_acronym_query,
    validate_sectors_exist_query
)

logger = get_logger(__name__)


def is_nota_document_type(document_type_id: int, *, schema_name: str) -> bool:
    """
    Verifica si un document_type_id corresponde al tipo NOTA.

    Args:
        document_type_id: ID del tipo de documento
        schema_name: Schema del tenant

    Returns:
        True si es tipo NOTA, False en caso contrario
    """
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            cursor.execute(check_nota_document_type_query(), (document_type_id,))
            result = cursor.fetchone()
            return result is not None


def is_nota_document_type_by_acronym(acronym: str, *, schema_name: str) -> bool:
    """
    Verifica si un acronym corresponde al tipo NOTA.

    Args:
        acronym: Acrónimo del tipo de documento
        schema_name: Schema del tenant

    Returns:
        True si es tipo NOTA, False en caso contrario
    """
    return acronym.upper() == 'NOTA'


def get_nota_document_type_id(*, schema_name: str) -> int | None:
    """
    Obtiene el ID del tipo de documento NOTA.

    Args:
        schema_name: Schema del tenant

    Returns:
        ID del tipo NOTA o None si no existe
    """
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            cursor.execute(check_nota_by_acronym_query())
            result = cursor.fetchone()
            return result['id'] if result else None


def validate_recipients_exist(
    cursor,
    recipients: Dict[str, List[str]],
    sender_sector_id: str,
    *, schema_name: str
) -> None:
    """
    Valida que los recipients sean válidos.

    Validaciones:
    1. Al menos 1 destinatario TO
    2. Todos los sector_ids existen y están activos
    3. El sender no está en la lista de recipients

    Args:
        cursor: Cursor de la transacción padre
        recipients: Dict con {to: [], cc: [], bcc: []}
        sender_sector_id: UUID del sector emisor
        schema_name: Schema del tenant (keyword-only)

    Raises:
        ValidationError: Si alguna validación falla
    """
    # Validar que haya al menos un TO
    to_list = recipients.get('to', [])
    if not to_list:
        raise ValidationError("Una NOTA requiere al menos un destinatario TO")

    # Juntar todos los sector_ids
    all_sector_ids = (
        to_list +
        recipients.get('cc', []) +
        recipients.get('bcc', [])
    )

    # Remover duplicados preservando orden
    seen = set()
    unique_sector_ids = []
    for sid in all_sector_ids:
        if sid not in seen:
            seen.add(sid)
            unique_sector_ids.append(sid)

    if not unique_sector_ids:
        raise ValidationError("No hay destinatarios válidos")

    # Validar que el sender no esté en recipients
    if sender_sector_id in unique_sector_ids:
        raise ValidationError("El sector emisor no puede ser destinatario de la nota")

    # Validar que todos los sectores existan y estén activos
    # Validar formato UUID y convertir a tuple para el query
    try:
        validated_ids = tuple(str(UUID(sid)) for sid in unique_sector_ids)
    except ValueError as e:
        raise ValidationError(f"Sector ID inválido: {e}")

    cursor.execute(validate_sectors_exist_query(), (list(validated_ids),))
    existing_sectors = {str(row['id']) for row in cursor.fetchall()}

    missing_sectors = set(unique_sector_ids) - existing_sectors
    if missing_sectors:
        raise ValidationError(
            f"Los siguientes sectores no existen o están inactivos: {list(missing_sectors)}"
        )

    logger.info(f"Recipients validados: {len(unique_sector_ids)} sectores válidos")


def validate_recipients_input(recipients: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    Normaliza, valida y deduplica la estructura del input de recipients.

    Deduplicación silenciosa:
    - Dentro de cada lista: [a, a] → [a]
    - Entre listas (prioridad TO > CC > BCC): si está en TO, se quita de CC/BCC

    Args:
        recipients: Dict recibido del request

    Returns:
        Dict normalizado y deduplicado con listas de UUIDs

    Raises:
        ValidationError: Si la estructura es inválida
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

    # Deduplicar ENTRE categorías (prioridad: TO > CC > BCC)
    to_set = set(normalized['to'])
    normalized['cc'] = [s for s in normalized['cc'] if s not in to_set]
    normalized['bcc'] = [s for s in normalized['bcc'] if s not in to_set]

    cc_set = set(normalized['cc'])
    normalized['bcc'] = [s for s in normalized['bcc'] if s not in cc_set]

    # Log si hubo deduplicación
    final_counts = {k: len(v) for k, v in normalized.items()}
    if original_counts != final_counts:
        logger.info(
            f"Recipients deduplicados: original={original_counts}, final={final_counts}"
        )

    return normalized


def is_nota_document_type_by_id(document_id: str, cursor, *, schema_name: str) -> bool:
    """
    Verifica si un documento es tipo NOTA usando su ID.

    Args:
        document_id: UUID del documento
        cursor: Cursor de la transacción padre
        schema_name: Schema del tenant

    Returns:
        True si es tipo NOTA, False en caso contrario
    """
    query = """
        SELECT dt.acronym
        FROM document_draft dd
        JOIN document_types dt ON dt.id = dd.document_type_id
        WHERE dd.id = %s
    """
    cursor.execute(query, (document_id,))
    result = cursor.fetchone()
    return result is not None and result['acronym'].upper() == 'NOTA'


def validate_nota_recipients_for_signing(document_id: str, *, schema_name: str) -> None:
    """
    Valida recipients de NOTA antes de iniciar firma.

    Validaciones:
    1. Al menos 1 destinatario TO existe
    2. Todos los sectors siguen activos

    Args:
        document_id: UUID del documento
        schema_name: Schema del tenant

    Raises:
        ValidationError: Si falla alguna validación
    """
    query = """
        SELECT nr.sector_id, nr.recipient_type, s.is_active, s.acronym as sector_acronym, d.name as department_name
        FROM notes_recipients nr
        JOIN sectors s ON s.id = nr.sector_id
        JOIN departments d ON d.id = s.department_id
        WHERE nr.document_id = %s
    """
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (document_id,))
            recipients = cursor.fetchall()

            # Validar que haya al menos 1 TO
            to_recipients = [r for r in recipients if r['recipient_type'] == 'TO']
            if not to_recipients:
                raise ValidationError(
                    "Una NOTA requiere al menos un destinatario (TO) para iniciar el proceso de firma. "
                    "Por favor, agregue destinatarios antes de firmar."
                )

            # Validar que todos los sectors estén activos
            inactive = [f"{r['sector_acronym']} ({r['department_name']})" for r in recipients if not r['is_active']]
            if inactive:
                raise ValidationError(
                    f"Los siguientes sectores ya no están activos: {', '.join(inactive)}. "
                    "Por favor, actualice los destinatarios."
                )
