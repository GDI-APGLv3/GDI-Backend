import os
from dotenv import load_dotenv
from typing import Optional

load_dotenv()


def _clean_val(val: Optional[str]) -> Optional[str]:
    if val is None:
        return None
    v = val.strip()
    if v.startswith("`") and v.endswith("`"):
        v = v[1:-1]
    if v.startswith("\"") and v.endswith("\""):
        v = v[1:-1]
    return v.strip()


DEFAULT_TIMEOUT = int(_clean_val(os.getenv("DEFAULT_TIMEOUT")) or "30")

LOGO_FETCH_TIMEOUT = int(_clean_val(os.getenv("LOGO_FETCH_TIMEOUT")) or "5")

PDF_GENERATION_TIMEOUT = int(_clean_val(os.getenv("PDF_GENERATION_TIMEOUT")) or "60")

WEASYPRINT_RESOURCE_TIMEOUT = int(_clean_val(os.getenv("WEASYPRINT_RESOURCE_TIMEOUT")) or "4")

ENV = _clean_val(os.getenv("ENV")) or "development"

API_KEY = _clean_val(os.getenv("API_KEY"))
if not API_KEY:
    raise ValueError("API_KEY environment variable must be set")

CF_R2_PREFIX_TOSIGN = _clean_val(os.getenv("CF_R2_PREFIX_TOSIGN") or "to-sign")
CF_R2_PREFIX_OFICIAL = _clean_val(os.getenv("CF_R2_PREFIX_OFICIAL") or "oficial")
CF_R2_PREFIX_ASSETS = _clean_val(os.getenv("CF_R2_PREFIX_ASSETS") or "assets")
