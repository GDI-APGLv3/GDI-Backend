
from datetime import datetime
from typing import Any, Optional
from shared.logging import get_logger
from shared.exceptions import ValidationError, reraise_if_transient
from database import fetch_one

logger = get_logger(__name__)

VALID_FIELD_TYPES = {"text", "number", "date", "select", "boolean", "file", "textarea", "email"}


async def validate_record_data(data: dict, data_schema: dict, *, skip_required: bool = False, schema_name: str = None) -> dict:
    validated = {}

    for field_name, field_schema in data_schema.items():
        is_required = field_schema.get("required", False)
        field_value = data.get(field_name)

        if not skip_required and is_required and (field_value is None or field_value == ""):
            raise ValidationError(
                f"El campo '{field_schema.get('label', field_name)}' es requerido"
            )

        if field_value is not None and field_value != "":
            validated[field_name] = await _build_enriched_field(field_name, field_value, field_schema, data, schema_name=schema_name)

    return validated


async def _build_enriched_field(field_name: str, value: Any, field_schema: dict, full_data: dict, *, schema_name: str = None) -> dict:
    is_file = field_schema.get("type") == "file"

    if is_file and isinstance(value, dict):
        doc_id = value.get("document_id") or value.get("id")
        if doc_id and isinstance(doc_id, str):
            logger.warning(f"Campo file '{field_name}' recibió objeto, extrayendo document_id: {doc_id[:8]}")
            value = doc_id
        else:
            raise ValidationError(
                f"El campo '{field_schema.get('label', field_name)}' es tipo archivo y debe recibir un document_id (string UUID), no un objeto"
            )

    if is_file and isinstance(value, str) and schema_name:
        doc_info = await _lookup_document(value, schema_name=schema_name)
        if doc_info:
            field = {
                "value": doc_info.get("official_number") or value,
                "document_id": value,
                "document_reference": doc_info.get("reference", ""),
                "document_resume": doc_info.get("resume", ""),
            }
        else:
            field = {"value": value, "document_id": value}
    else:
        field = {"value": value}

    if field_schema.get("has_expiration"):
        exp_key = f"{field_name}_expiration"
        exp_date = full_data.get(exp_key)
        if exp_date:
            field["expiration_date"] = exp_date

    if not is_file and (field_schema.get("has_document") or False):
        doc_key = f"{field_name}_document_id"
        doc_id = full_data.get(doc_key)
        if doc_id:
            field["document_id"] = doc_id
        doc_ref = full_data.get(f"{field_name}_document_reference")
        if doc_ref:
            field["document_reference"] = doc_ref
        doc_resume = full_data.get(f"{field_name}_document_resume")
        if doc_resume:
            field["document_resume"] = doc_resume

    if field_schema.get("has_verification"):
        field["verified"] = False
        field["verified_at"] = None
        field["verified_by"] = None

    return field


async def _lookup_document(document_id: str, *, schema_name: str) -> Optional[dict]:
    try:
        result = await fetch_one(
            """SELECT official_number, reference, resume
               FROM official_documents WHERE id = $1 AND signed_at IS NOT NULL
               UNION ALL
               SELECT NULL as official_number, reference, NULL as resume
               FROM document_draft WHERE id = $1
               LIMIT 1""",
            document_id,
            schema_name=schema_name,
        )
        return dict(result) if result else None
    except Exception as e:
        reraise_if_transient(e, context=f"lookup del documento {document_id[:8]}")
        logger.warning(f"Error buscando documento {document_id[:8]}: {e}")
        return None


def validate_field_update(field_name: str, update_data: dict, data_schema: dict) -> None:
    if field_name not in data_schema:
        raise ValidationError(f"El campo '{field_name}' no existe en este tipo de registro")


def calculate_next_expiration(data: dict, data_schema: dict) -> Optional[str]:
    earliest = None

    for field_name, field_schema in data_schema.items():
        if not field_schema.get("has_expiration"):
            continue

        field_data = data.get(field_name, {})
        if isinstance(field_data, dict):
            exp_date_str = field_data.get("expiration_date")
            if exp_date_str:
                try:
                    exp_date = datetime.fromisoformat(exp_date_str).date() if isinstance(exp_date_str, str) else exp_date_str
                    if earliest is None or exp_date < earliest:
                        earliest = exp_date
                except (ValueError, TypeError):
                    continue

    return earliest.isoformat() if earliest else None
