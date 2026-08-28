
import html
from typing import Any
from shared.logging import get_logger

logger = get_logger(__name__)


def ffcc_to_html(schema: list, data: dict) -> str:
    if not isinstance(schema, list):
        logger.warning(f"ffcc_to_html: schema no es lista ({type(schema).__name__}), retornando tabla vacia")
        return "<table></table>"

    if not isinstance(data, dict):
        logger.warning(f"ffcc_to_html: data no es dict ({type(data).__name__}), usando dict vacio")
        data = {}

    rows = []
    for field in schema:
        if not isinstance(field, dict):
            continue

        field_name = field.get("name", "")
        field_type = field.get("type", "text")

        label_raw = field.get("label") or field_name
        label = html.escape(str(label_raw))

        raw_value = data.get(field_name)

        display = _render_field_value(field_name, field_type, raw_value)

        escaped_value = html.escape(display)

        if field_type == "textarea":
            escaped_value = escaped_value.replace("\n", "<br>")

        rows.append(f"<tr><th>{label}</th><td>{escaped_value}</td></tr>")

    return f"<table>{''.join(rows)}</table>"


def _render_field_value(field_name: str, field_type: str, raw_value: Any) -> str:
    if raw_value is None:
        return ""

    if field_type == "boolean":
        if isinstance(raw_value, bool):
            return "Si" if raw_value else "No"
        str_val = str(raw_value).lower()
        if str_val in ("true", "1", "yes", "si"):
            return "Si"
        return "No"

    elif field_type == "file":
        if isinstance(raw_value, dict):
            enriched = raw_value.get("value") or raw_value.get("official_number")
            if enriched:
                return str(enriched)
            doc_id = raw_value.get("document_id") or raw_value.get("id")
            if doc_id:
                return str(doc_id)
            return ""
        return str(raw_value) if raw_value else ""

    else:
        return str(raw_value) if raw_value is not None else ""
