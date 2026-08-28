
from typing import Dict, List, Any
from uuid import UUID
from shared.logging import get_logger
from shared.exceptions import ValidationError
from database import fetch_one, fetch_all
from .queries import (
    check_nota_document_type_query,
    validate_sectors_exist_query,
)

logger = get_logger(__name__)


async def is_nota_document_type(document_type_id: int, *, schema_name: str) -> bool:
    result = await fetch_one(
        check_nota_document_type_query(), document_type_id, schema_name=schema_name
    )
    return result is not None


async def validate_recipients_exist(
    conn,
    recipients: Dict[str, List[str]],
    sender_sector_id: str,
    *,
    schema_name: str,
) -> None:
    to_list = recipients.get("to", [])
    if not to_list:
        raise ValidationError("Una NOTA requiere al menos un destinatario TO")

    all_sector_ids = to_list + recipients.get("cc", []) + recipients.get("bcc", [])

    seen: set = set()
    unique_sector_ids: List[str] = []
    for sid in all_sector_ids:
        if sid not in seen:
            seen.add(sid)
            unique_sector_ids.append(sid)

    if not unique_sector_ids:
        raise ValidationError("No hay destinatarios válidos")

    if sender_sector_id in unique_sector_ids:
        raise ValidationError("El sector emisor no puede ser destinatario de la nota")

    try:
        validated_ids = [str(UUID(sid)) for sid in unique_sector_ids]
    except ValueError as e:
        raise ValidationError(f"Sector ID inválido: {e}")

    rows = await conn.fetch(validate_sectors_exist_query(), validated_ids)
    existing_sectors = {str(row["id"]) for row in rows}

    missing_sectors = set(unique_sector_ids) - existing_sectors
    if missing_sectors:
        raise ValidationError(
            f"Los siguientes sectores no existen o están inactivos: {list(missing_sectors)}"
        )

    logger.info(f"Recipients validados: {len(unique_sector_ids)} sectores válidos")


def validate_recipients_input(recipients: Dict[str, Any]) -> Dict[str, List[str]]:
    if not isinstance(recipients, dict):
        raise ValidationError("Recipients debe ser un objeto con claves 'to', 'cc', 'bcc'")

    normalized: Dict[str, List[str]] = {"to": [], "cc": [], "bcc": []}

    for key in ["to", "cc", "bcc"]:
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
    for key in ["to", "cc", "bcc"]:
        normalized[key] = list(dict.fromkeys(normalized[key]))

    to_set = set(normalized["to"])
    normalized["cc"] = [s for s in normalized["cc"] if s not in to_set]
    normalized["bcc"] = [s for s in normalized["bcc"] if s not in to_set]

    cc_set = set(normalized["cc"])
    normalized["bcc"] = [s for s in normalized["bcc"] if s not in cc_set]

    final_counts = {k: len(v) for k, v in normalized.items()}
    if original_counts != final_counts:
        logger.info(
            f"Recipients deduplicados: original={original_counts}, final={final_counts}"
        )

    return normalized


async def is_nota_document_type_by_id(document_id: str, conn, *, schema_name: str) -> bool:
    query = """
        SELECT dt.type
        FROM document_draft dd
        JOIN document_types dt ON dt.id = dd.document_type_id
        WHERE dd.id = $1
    """
    result = await conn.fetchrow(query, document_id)
    return result is not None and (result["type"] or "").upper() == "NOTA"


async def validate_nota_recipients_for_signing(document_id: str, *, schema_name: str) -> None:
    query = """
        SELECT nr.sector_id, nr.recipient_type, s.is_active,
               s.acronym as sector_acronym, d.name as department_name
        FROM notes_recipients nr
        JOIN sectors s ON s.id = nr.sector_id
        JOIN departments d ON d.id = s.department_id
        WHERE nr.document_id = $1
    """
    recipients = await fetch_all(query, document_id, schema_name=schema_name)

    to_recipients = [r for r in recipients if r["recipient_type"] == "TO"]
    if not to_recipients:
        raise ValidationError(
            "Una NOTA requiere al menos un destinatario (TO) para iniciar el proceso de firma. "
            "Por favor, agregue destinatarios antes de firmar."
        )

    inactive = [
        f"{r['sector_acronym']} ({r['department_name']})"
        for r in recipients
        if not r["is_active"]
    ]
    if inactive:
        raise ValidationError(
            f"Los siguientes sectores ya no están activos: {', '.join(inactive)}. "
            "Por favor, actualice los destinatarios."
        )
