
from datetime import datetime
from typing import Optional, Any
from shared.logging import get_logger
from database import fetch_one, transaction, check_document_exists
from shared.exceptions import ValidationError, NotFoundError, AuthorizationError
from services.rlm.queries import (
    get_record_detail_query,
    get_record_detail_for_update_query,
    update_record_field_atomic_query,
    get_user_sector_info_query,
    insert_history_query,
)
from services.rlm.validation import validate_field_update, calculate_next_expiration
from services.rlm.permissions import check_permission

logger = get_logger(__name__)


async def update_field(
    record_id: str,
    field_name: str,
    user_id: str,
    value: Any = None,
    expiration_date: Optional[str] = None,
    document_id: Optional[str] = None,
    notes: Optional[str] = None,
    document_reference: Optional[str] = None,
    document_resume: Optional[str] = None,
    *,
    schema_name: str
) -> dict:
    record = await fetch_one(
        get_record_detail_query(),
        record_id,
        schema_name=schema_name,
    )

    if not record:
        raise NotFoundError(f"Legajo con ID '{record_id}' no encontrado")

    if not await check_permission(record["registry_family_id"], user_id, "can_edit", schema_name=schema_name):
        raise AuthorizationError("No tiene permisos para editar este legajo")

    data_schema = record.get("data_schema") or {}
    validate_field_update(field_name, {}, data_schema)

    user_info = await fetch_one(
        get_user_sector_info_query(),
        user_id,
        schema_name=schema_name,
    )

    async with transaction(schema_name=schema_name, user_id=user_id) as conn:
        locked_record = await conn.fetchrow(
            get_record_detail_for_update_query(),
            record_id
        )

        if not locked_record:
            raise NotFoundError(f"Legajo con ID '{record_id}' no encontrado")

        current_data = locked_record.get("data") or {}
        old_field = current_data.get(field_name, {})

        new_field = dict(old_field) if isinstance(old_field, dict) else {}
        if value is not None:
            new_field["value"] = value
        if expiration_date is not None:
            new_field["expiration_date"] = expiration_date
        if document_id is not None:
            new_field["document_id"] = document_id
        if notes is not None:
            new_field["notes"] = notes
        if document_reference is not None:
            new_field["document_reference"] = document_reference
        if document_resume is not None:
            new_field["document_resume"] = document_resume

        temp_data = dict(current_data)
        temp_data[field_name] = new_field
        next_exp = calculate_next_expiration(temp_data, data_schema)

        jsonb_path = [field_name]
        result = await conn.fetchrow(
            update_record_field_atomic_query(),
            jsonb_path, new_field, next_exp, record_id
        )

        await conn.execute(
            insert_history_query(),
            record_id,
            "field_updated",
            field_name,
            old_field if old_field else None,
            new_field,
            user_id,
            user_info.get("sector_id") if user_info else None,
        )

    logger.info(f"Field '{field_name}' updated on record {record_id[:8]}")

    return {
        "field_name": field_name,
        "field_data": new_field,
        "next_expiration": str(result["next_expiration"]) if result["next_expiration"] else None,
        "updated_at": str(result["updated_at"]),
    }


async def verify_field(
    record_id: str,
    field_name: str,
    user_id: str,
    document_id: str,
    notes: Optional[str] = None,
    *,
    schema_name: str
) -> dict:
    if not await check_document_exists(document_id, schema_name=schema_name):
        raise NotFoundError(f"Documento de respaldo con ID '{document_id}' no encontrado")

    record = await fetch_one(
        get_record_detail_query(),
        record_id,
        schema_name=schema_name,
    )

    if not record:
        raise NotFoundError(f"Legajo con ID '{record_id}' no encontrado")

    if not await check_permission(record["registry_family_id"], user_id, "can_verify", schema_name=schema_name):
        raise AuthorizationError("No tiene permisos para verificar campos en este legajo")

    data_schema = record.get("data_schema") or {}
    if field_name not in data_schema:
        raise ValidationError(f"El campo '{field_name}' no existe")

    if not data_schema[field_name].get("has_verification"):
        raise ValidationError(f"El campo '{field_name}' no admite verificacion")

    user_info = await fetch_one(
        get_user_sector_info_query(),
        user_id,
        schema_name=schema_name,
    )

    async with transaction(schema_name=schema_name, user_id=user_id) as conn:
        locked_record = await conn.fetchrow(
            get_record_detail_for_update_query(),
            record_id
        )

        if not locked_record:
            raise NotFoundError(f"Legajo con ID '{record_id}' no encontrado")

        current_data = locked_record.get("data") or {}
        old_field = current_data.get(field_name, {})

        new_field = dict(old_field) if isinstance(old_field, dict) else {}
        new_field["verified"] = True
        new_field["verified_at"] = datetime.now().isoformat()
        new_field["verified_by"] = user_id
        new_field["verified_by_name"] = user_info.get("full_name", "") if user_info else ""
        new_field["verified_document_id"] = document_id

        doc_info = await conn.fetchrow(
            """SELECT COALESCE(official_number, '') as official_number,
                      COALESCE(resume, '') as resume
               FROM official_documents WHERE id = $1 AND signed_at IS NOT NULL""",
            document_id
        )
        if doc_info:
            new_field["verified_document_number"] = doc_info.get("official_number", "")
            new_field["verified_document_resume"] = doc_info.get("resume", "")
        if notes:
            new_field["verification_notes"] = notes

        temp_data = dict(current_data)
        temp_data[field_name] = new_field
        next_exp = calculate_next_expiration(temp_data, data_schema)

        jsonb_path = [field_name]
        result = await conn.fetchrow(
            update_record_field_atomic_query(),
            jsonb_path, new_field, next_exp, record_id
        )

        await conn.execute(
            insert_history_query(),
            record_id,
            "field_verified",
            field_name,
            old_field if old_field else None,
            new_field,
            user_id,
            user_info.get("sector_id") if user_info else None,
        )

    logger.info(f"Field '{field_name}' verified on record {record_id[:8]}")

    return {
        "field_name": field_name,
        "field_data": new_field,
        "updated_at": str(result["updated_at"]),
    }
