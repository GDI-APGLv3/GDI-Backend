import os
import re

_SAFE_FIELD_NAME = re.compile(r"^[a-z0-9_]{1,64}$")


def sanitize_field_names(fields: list) -> list:
    if not fields:
        return []
    return [f for f in fields if isinstance(f, str) and _SAFE_FIELD_NAME.match(f)]


def whitelist_fields(data: dict, fields: list) -> dict:
    if not data or not fields:
        return {}
    safe_fields = sanitize_field_names(fields)
    return {f: data[f] for f in safe_fields if f in data}


def build_public_pdf_url(muni: str, document_id: str) -> str:
    base = os.getenv("PUBLIC_PDF_BASE_URL", "https://public.your-domain.com").rstrip("/")
    return f"{base}/{muni.lower()}/{document_id}.pdf"
