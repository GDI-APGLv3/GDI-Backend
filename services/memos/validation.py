
from typing import Dict, List, Any
from uuid import UUID
from shared.logging import get_logger
from shared.exceptions import ValidationError
from database import fetch_one, fetch_all
from .queries import (
    check_memo_document_type_query,
    check_memo_by_acronym_query,
    validate_users_exist_query
)

logger = get_logger(__name__)


async def is_memo_document_type(document_type_id: int, *, schema_name: str) -> bool:
    result = await fetch_one(
        check_memo_document_type_query(), document_type_id,
        schema_name=schema_name
    )
    return result is not None


async def is_memo_document_type_by_id(document_id: str, conn, *, schema_name: str) -> bool:
    query = """
        SELECT dt.type
        FROM document_draft dd
        JOIN document_types dt ON dt.id = dd.document_type_id
        WHERE dd.id = $1
    """
    result = await conn.fetchrow(query, document_id)
    return result is not None and (result["type"] or "").upper() == "MEMO"


def is_memo_document_type_by_acronym(acronym: str, *, schema_name: str) -> bool:
    return acronym.upper() == 'MEMO'


async def get_memo_document_type_id(*, schema_name: str) -> int | None:
    result = await fetch_one(
        check_memo_by_acronym_query(),
        schema_name=schema_name
    )
    return result['id'] if result else None


async def validate_memo_recipients_exist(
    conn,
    recipients: Dict[str, List[str]],
    sender_user_id: str,
    *, schema_name: str
) -> None:
    to_list = recipients.get('to', [])
    if not to_list:
        raise ValidationError("Un MEMO requiere al menos un destinatario TO")

    all_user_ids = (
        to_list +
        recipients.get('cc', []) +
        recipients.get('bcc', [])
    )

    seen: set = set()
    unique_user_ids: List[str] = []
    for uid in all_user_ids:
        if uid not in seen:
            seen.add(uid)
            unique_user_ids.append(uid)

    if not unique_user_ids:
        raise ValidationError("No hay destinatarios validos")

    if sender_user_id in unique_user_ids:
        raise ValidationError("El emisor no puede ser destinatario del memo")

    try:
        validated_ids = [str(UUID(uid)) for uid in unique_user_ids]
    except ValueError as e:
        raise ValidationError(f"User ID invalido: {e}")

    rows = await conn.fetch(validate_users_exist_query(), validated_ids)
    existing_users = {str(row['id']) for row in rows}

    missing_users = set(unique_user_ids) - existing_users
    if missing_users:
        raise ValidationError(
            f"Los siguientes usuarios no existen o estan inactivos: {list(missing_users)}"
        )

    logger.info(f"Recipients validados: {len(unique_user_ids)} usuarios validos")


def validate_memo_recipients_input(recipients: Dict[str, Any]) -> Dict[str, List[str]]:
    if not isinstance(recipients, dict):
        raise ValidationError("Recipients debe ser un objeto con claves 'to', 'cc', 'bcc'")

    normalized: Dict[str, List[str]] = {'to': [], 'cc': [], 'bcc': []}

    for key in ['to', 'cc', 'bcc']:
        value = recipients.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            raise ValidationError(f"Recipients.{key} debe ser una lista de UUIDs")
        for i, item in enumerate(value):
            if not isinstance(item, str):
                raise ValidationError(f"Recipients.{key}[{i}] debe ser un UUID string")
            normalized[key].append(item)

    original_counts = {k: len(v) for k, v in normalized.items()}
    for key in ['to', 'cc', 'bcc']:
        normalized[key] = list(dict.fromkeys(normalized[key]))

    to_set = set(normalized['to'])
    normalized['cc'] = [s for s in normalized['cc'] if s not in to_set]
    normalized['bcc'] = [s for s in normalized['bcc'] if s not in to_set]

    cc_set = set(normalized['cc'])
    normalized['bcc'] = [s for s in normalized['bcc'] if s not in cc_set]

    final_counts = {k: len(v) for k, v in normalized.items()}
    if original_counts != final_counts:
        logger.info(f"Recipients deduplicados: original={original_counts}, final={final_counts}")

    return normalized


async def validate_memo_recipients_for_signing(document_id: str, *, schema_name: str) -> None:
    query = """
        SELECT mr.recipient_user_id, mr.recipient_type,
               (u.estado = 1) as is_active, u.full_name as recipient_name,
               s.acronym as sector_acronym
        FROM memo_recipients mr
        JOIN users u ON u.id = mr.recipient_user_id
        LEFT JOIN sectors s ON s.id = mr.recipient_sector_id
        WHERE mr.document_id = $1
    """
    recipients = await fetch_all(query, document_id, schema_name=schema_name)

    to_recipients = [r for r in recipients if r['recipient_type'] == 'TO']
    if not to_recipients:
        raise ValidationError(
            "Un MEMO requiere al menos un destinatario (TO) para iniciar el proceso de firma. "
            "Por favor, agregue destinatarios antes de firmar."
        )

    inactive = [
        f"{r['recipient_name']}" + (f" ({r['sector_acronym']})" if r['sector_acronym'] else "")
        for r in recipients if not r['is_active']
    ]
    if inactive:
        raise ValidationError(
            f"Los siguientes usuarios ya no estan activos: {', '.join(inactive)}. "
            "Por favor, actualice los destinatarios."
        )
