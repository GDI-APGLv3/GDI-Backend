
import json
import uuid
from datetime import datetime, date
from typing import Any, Dict, Optional
from decimal import Decimal


async def get_authenticated_user(user_id: str, *, schema_name: str) -> str:
    from database import fetch_all
    from services.case_queries import get_user_validation_query
    from shared.exceptions import ValidationError
    from config.constants import USER_NOT_FOUND_ERROR

    user_result = await fetch_all(get_user_validation_query(), user_id, schema_name=schema_name)

    if not user_result:
        raise ValidationError(USER_NOT_FOUND_ERROR)

    return str(user_result[0]['user_id'])


async def get_authenticated_user_with_flags(user_id: str, *, schema_name: str) -> Dict[str, Any]:
    from database import fetch_all
    from services.case_queries import get_user_validation_query
    from shared.exceptions import ValidationError
    from config.constants import USER_NOT_FOUND_ERROR

    user_result = await fetch_all(get_user_validation_query(), user_id, schema_name=schema_name)

    if not user_result:
        raise ValidationError(USER_NOT_FOUND_ERROR)

    row = user_result[0]
    return {
        "user_id": str(row["user_id"]),
        "can_global_search_documents": row.get("can_global_search_documents", False),
        "can_global_search_cases": row.get("can_global_search_cases", False),
    }


async def get_user_global_search_flags(user_id: str, *, schema_name: str) -> dict:
    from database import fetch_one

    result = await fetch_one(
        "SELECT can_global_search_documents, can_global_search_cases FROM users WHERE id = $1 LIMIT 1",
        user_id,
        schema_name=schema_name,
    )

    if not result:
        return {"can_global_search_documents": False, "can_global_search_cases": False}

    return {
        "can_global_search_documents": result.get("can_global_search_documents", False),
        "can_global_search_cases": result.get("can_global_search_cases", False),
    }


def generate_uuid() -> str:
    return str(uuid.uuid4())

def generate_document_number(prefix: str = "DOC") -> str:
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    unique_suffix = str(uuid.uuid4())[:8].upper()
    return f"{prefix}-{timestamp}-{unique_suffix}"

def format_datetime(dt: datetime, format_string: str = "%Y-%m-%d %H:%M:%S") -> str:
    if not dt:
        return ""
    return dt.strftime(format_string)

def format_date(date_obj: date, format_string: str = "%Y-%m-%d") -> str:
    if not date_obj:
        return ""
    return date_obj.strftime(format_string)

def parse_datetime(date_string: str, format_string: str = "%Y-%m-%d %H:%M:%S") -> Optional[datetime]:
    try:
        return datetime.strptime(date_string, format_string)
    except (ValueError, TypeError):
        return None

def safe_json_loads(json_string: str, default: Any = None) -> Any:
    try:
        return json.loads(json_string) if json_string else default
    except (json.JSONDecodeError, TypeError):
        return default

def payload_as_dict(value: Any) -> dict:
    for _ in range(3):
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                value = json.loads(value)
                continue
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}
    return value if isinstance(value, dict) else {}

def safe_json_dumps(obj: Any, default: str = "{}") -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, cls=CustomJSONEncoder)
    except (TypeError, ValueError):
        return default

class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, date):
            return obj.isoformat()
        elif isinstance(obj, Decimal):
            return float(obj)
        elif isinstance(obj, uuid.UUID):
            return str(obj)
        return super().default(obj)

def clean_string(text: str, max_length: Optional[int] = None) -> str:
    if not text:
        return ""
    
    cleaned = " ".join(text.strip().split())
    
    if max_length and len(cleaned) > max_length:
        cleaned = cleaned[:max_length].strip()
    
    return cleaned

def normalize_text(text: str) -> str:
    import unicodedata
    
    if not text:
        return ""
    
    text = text.lower()
    
    text = unicodedata.normalize('NFD', text)
    text = ''.join(char for char in text if unicodedata.category(char) != 'Mn')
    
    text = " ".join(text.split())
    
    return text

def calculate_offset(page: int, page_size: int) -> int:
    return (page - 1) * page_size

def calculate_total_pages(total_records: int, page_size: int) -> int:
    if page_size <= 0:
        return 0
    
    return (total_records + page_size - 1) // page_size

def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    if not text or len(text) <= max_length:
        return text

    if len(suffix) >= max_length:
        return text[:max_length]

    return text[:max_length - len(suffix)] + suffix
