from typing import Any


_STORAGE_URL_KEYS = {"pdf_url", "signed_pdf_url"}


def strip_storage_urls(data: Any) -> Any:
    if isinstance(data, dict):
        return {
            k: strip_storage_urls(v)
            for k, v in data.items()
            if k not in _STORAGE_URL_KEYS
        }
    if isinstance(data, list):
        return [strip_storage_urls(item) for item in data]
    return data
