"""
Validación de datos para el módulo RLM.
Valida campos enriquecidos contra el data_schema del registro.
"""

from datetime import datetime
from typing import Any, Optional
from shared.logging import get_logger
from shared.exceptions import ValidationError
from database import execute_query

logger = get_logger(__name__)

VALID_FIELD_TYPES = {"text", "number", "date", "select", "boolean", "file", "textarea"}


def validate_record_data(data: dict, data_schema: dict, *, skip_required: bool = False, schema_name: str = None) -> dict:
    """
    Valida los datos de un legajo contra el data_schema del registro.

    Args:
        data: Datos proporcionados por el usuario
        data_schema: Schema de campos definido en registry_families.data_schema
        skip_required: Si True, no valida campos requeridos (usado en creación)
        schema_name: Schema del tenant (para enriquecer campos file)

    Returns:
        Datos validados y normalizados

    Raises:
        ValidationError: Si los datos no cumplen el schema
    """
    validated = {}

    for field_name, field_schema in data_schema.items():
        is_required = field_schema.get("required", False)
        field_value = data.get(field_name)

        # Verificar campos requeridos (solo si no se skip)
        if not skip_required and is_required and (field_value is None or field_value == ""):
            raise ValidationError(
                f"El campo '{field_schema.get('label', field_name)}' es requerido"
            )

        if field_value is not None and field_value != "":
            validated[field_name] = _build_enriched_field(field_name, field_value, field_schema, data, schema_name=schema_name)

    return validated


def _build_enriched_field(field_name: str, value: Any, field_schema: dict, full_data: dict, *, schema_name: str = None) -> dict:
    """
    Construye un campo enriquecido con metadatos opcionales.

    Para campos file: valida que value sea un string (document_id UUID)
    y auto-enriquece buscando official_number, reference y short_resume.

    Args:
        field_name: Nombre del campo
        value: Valor del campo
        field_schema: Schema del campo
        full_data: Todos los datos del request (para campos con subcampos)
        schema_name: Schema del tenant (para enriquecer campos file)

    Returns:
        Dict con valor y metadatos
    """
    is_file = field_schema.get("type") == "file"

    # Validación de campos file: no aceptar objetos
    if is_file and isinstance(value, dict):
        # Intentar extraer document_id de objeto malformado
        doc_id = value.get("document_id") or value.get("id")
        if doc_id and isinstance(doc_id, str):
            logger.warning(f"Campo file '{field_name}' recibió objeto, extrayendo document_id: {doc_id[:8]}")
            value = doc_id
        else:
            raise ValidationError(
                f"El campo '{field_schema.get('label', field_name)}' es tipo archivo y debe recibir un document_id (string UUID), no un objeto"
            )

    # Para campos file: auto-enriquecer con datos del documento
    if is_file and isinstance(value, str) and schema_name:
        doc_info = _lookup_document(value, schema_name=schema_name)
        if doc_info:
            field = {
                "value": doc_info.get("official_number") or value,
                "document_id": value,
                "document_reference": doc_info.get("reference", ""),
                "document_resume": doc_info.get("resume", ""),
            }
        else:
            # Documento no encontrado — guardar UUID como value (se enriquecerá después)
            field = {"value": value, "document_id": value}
    else:
        field = {"value": value}

    # Campos con vencimiento
    if field_schema.get("has_expiration"):
        exp_key = f"{field_name}_expiration"
        exp_date = full_data.get(exp_key)
        if exp_date:
            field["expiration_date"] = exp_date

    # Campos con documento vinculado (has_document, no file — file ya se manejo arriba)
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

    # Campos con verificación
    if field_schema.get("has_verification"):
        field["verified"] = False
        field["verified_at"] = None
        field["verified_by"] = None

    return field


def _lookup_document(document_id: str, *, schema_name: str) -> Optional[dict]:
    """
    Busca un documento oficial por ID para enriquecer campos file.
    Best-effort: si no existe o falla, retorna None.
    """
    try:
        result = execute_query(
            """SELECT official_number, reference, resume
               FROM official_documents WHERE id = %s AND signed_at IS NOT NULL
               UNION ALL
               SELECT NULL as official_number, reference, NULL as resume
               FROM document_draft WHERE id = %s
               LIMIT 1""",
            (document_id, document_id),
            schema_name=schema_name,
            fetch_one=True
        )
        return dict(result) if result else None
    except Exception as e:
        logger.warning(f"Error buscando documento {document_id[:8]}: {e}")
        return None


def validate_field_update(field_name: str, update_data: dict, data_schema: dict) -> None:
    """
    Valida una actualización de campo individual.

    Args:
        field_name: Nombre del campo a actualizar
        update_data: Datos de la actualización
        data_schema: Schema completo del registro

    Raises:
        ValidationError: Si el campo no existe en el schema
    """
    if field_name not in data_schema:
        raise ValidationError(f"El campo '{field_name}' no existe en este tipo de registro")


def calculate_next_expiration(data: dict, data_schema: dict) -> Optional[str]:
    """
    Calcula la próxima fecha de vencimiento de un legajo.

    Recorre todos los campos con has_expiration y devuelve la más próxima.

    Args:
        data: Datos JSONB del legajo
        data_schema: Schema del registro

    Returns:
        Fecha de próximo vencimiento (ISO 8601) o None
    """
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
