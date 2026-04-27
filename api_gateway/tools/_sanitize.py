"""
Sanitization helpers for MCP tool responses.
Strips sensitive data (storage URLs, etc.) before returning to MCP clients.
"""
from typing import Any


_STORAGE_URL_KEYS = {"pdf_url", "signed_pdf_url"}


def strip_storage_urls(data: Any) -> Any:
    """
    Recursively remove pdf_url and signed_pdf_url keys from dicts and lists.

    Args:
        data: Dict, list, or any other value.

    Returns:
        Sanitized copy with storage URL keys removed.
    """
    if isinstance(data, dict):
        return {
            k: strip_storage_urls(v)
            for k, v in data.items()
            if k not in _STORAGE_URL_KEYS
        }
    if isinstance(data, list):
        return [strip_storage_urls(item) for item in data]
    return data
