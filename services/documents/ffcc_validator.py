
import math
import uuid
from datetime import date, datetime
from typing import Any
from shared.logging import get_logger
from shared.exceptions import ValidationError

logger = get_logger(__name__)

FFCC_NUMBER_ABS_LIMIT = 1e12
FFCC_TEXT_SANITY_MAXLEN = 20000
FFCC_DATE_MIN_YEAR = 1900
FFCC_DATE_MAX_YEAR = 2200

VALID_FFCC_TYPES = {"text", "textarea", "number", "date", "select", "boolean", "file", "email"}

_EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def validate_ffcc_content(
    content_json: dict,
    field_definitions: list,
    *,
    schema_name: str,
    enforce_required: bool = True,
) -> dict:
    if not isinstance(content_json, dict):
        raise ValidationError("El contenido del formulario debe ser un objeto JSON")

    if not isinstance(field_definitions, list):
        raise ValidationError("La definicion de campos debe ser una lista")

    for field in field_definitions:
        if not isinstance(field, dict):
            logger.warning(f"Campo mal formado en field_definitions: {field!r}")
            continue

        field_name = field.get("name", "")
        field_label = field.get("label", field_name)
        field_type = field.get("type", "text")
        is_required = field.get("required", False)

        value = content_json.get(field_name)
        has_value = value is not None and value != "" and value != []

        if enforce_required and is_required and not has_value:
            raise ValidationError(
                f"El campo '{field_label}' es requerido"
            )

        if not has_value:
            continue

        if field_type not in VALID_FFCC_TYPES:
            logger.warning(
                f"Tipo de campo desconocido '{field_type}' para campo '{field_name}' "
                f"(schema={schema_name}). Se ignora validacion de tipo."
            )
            continue

        _validate_field_by_type(field_name, field_label, field_type, value, field)

    return content_json


def _validate_field_by_type(
    field_name: str,
    field_label: str,
    field_type: str,
    value: Any,
    field_schema: dict,
) -> None:
    if field_type in ("text", "textarea", "email"):
        if not isinstance(value, str):
            raise ValidationError(
                f"El campo '{field_label}' debe ser texto"
            )
        max_length = field_schema.get("max_length")
        if max_length is not None and len(value) > max_length:
            raise ValidationError(
                f"El campo '{field_label}' no puede superar {max_length} caracteres"
            )
        if max_length is None and len(value) > FFCC_TEXT_SANITY_MAXLEN:
            raise ValidationError(
                f"El campo '{field_label}' es demasiado largo "
                f"(maximo {FFCC_TEXT_SANITY_MAXLEN} caracteres)"
            )
        if field_type == "email":
            import re
            if not re.match(_EMAIL_RE, value):
                raise ValidationError(
                    f"El campo '{field_label}' debe ser un email valido"
                )

    elif field_type == "number":
        if not isinstance(value, (int, float)):
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise ValidationError(
                    f"El campo '{field_label}' debe ser un numero"
                )
        if isinstance(value, bool):
            raise ValidationError(
                f"El campo '{field_label}' debe ser un numero"
            )
        if isinstance(value, float) and not math.isfinite(value):
            raise ValidationError(
                f"El campo '{field_label}' debe ser un numero finito"
            )
        min_val = field_schema.get("min")
        max_val = field_schema.get("max")
        if min_val is not None and value < min_val:
            raise ValidationError(
                f"El campo '{field_label}' debe ser mayor o igual a {min_val}"
            )
        if max_val is not None and value > max_val:
            raise ValidationError(
                f"El campo '{field_label}' debe ser menor o igual a {max_val}"
            )
        if min_val is None and max_val is None and abs(value) > FFCC_NUMBER_ABS_LIMIT:
            raise ValidationError(
                f"El campo '{field_label}' excede el maximo razonable "
                f"(+-{FFCC_NUMBER_ABS_LIMIT:.0f})"
            )

    elif field_type == "date":
        if not isinstance(value, str):
            raise ValidationError(
                f"El campo '{field_label}' debe ser una fecha en formato ISO (YYYY-MM-DD)"
            )
        import re
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
            raise ValidationError(
                f"El campo '{field_label}' debe estar en formato YYYY-MM-DD"
            )
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            raise ValidationError(
                f"El campo '{field_label}' no es una fecha valida"
            )
        if not (FFCC_DATE_MIN_YEAR <= parsed.year <= FFCC_DATE_MAX_YEAR):
            raise ValidationError(
                f"El campo '{field_label}' tiene un anio fuera de rango "
                f"({FFCC_DATE_MIN_YEAR}-{FFCC_DATE_MAX_YEAR})"
            )

    elif field_type == "select":
        options = field_schema.get("options") or []
        if value not in options:
            raise ValidationError(
                f"El campo '{field_label}' debe ser uno de: {', '.join(str(o) for o in options)}"
            )

    elif field_type == "boolean":
        if not isinstance(value, bool):
            raise ValidationError(
                f"El campo '{field_label}' debe ser verdadero o falso"
            )

    elif field_type == "file":
        if isinstance(value, dict):
            doc_id = value.get("document_id") or value.get("id") or value.get("value")
            if not doc_id:
                raise ValidationError(
                    f"El campo '{field_label}' debe contener un document_id valido"
                )
            value = doc_id

        if not isinstance(value, str):
            raise ValidationError(
                f"El campo '{field_label}' debe ser un UUID de documento (string)"
            )
        try:
            uuid.UUID(value)
        except ValueError:
            raise ValidationError(
                f"El campo '{field_label}' debe ser un UUID valido de documento"
            )
