
import re
from uuid import UUID
from typing import Optional, List, Dict, Any
import nh3
from database import check_user_exists, check_document_exists

def validate_uuid(uuid_string: str) -> bool:
    try:
        UUID(uuid_string)
        return True
    except (ValueError, TypeError):
        return False

def validate_required_string(value: Any, field_name: str, min_length: int = 1, max_length: Optional[int] = None) -> Optional[str]:
    if not value:
        return f"{field_name} es requerido"
    
    if not isinstance(value, str):
        return f"{field_name} debe ser un string"
    
    value = value.strip()
    
    if len(value) < min_length:
        return f"{field_name} debe tener al menos {min_length} caracteres"
    
    if max_length and len(value) > max_length:
        return f"{field_name} no puede exceder {max_length} caracteres"
    
    return None

async def validate_user_id(user_id: str, *, schema_name: str) -> Optional[str]:
    if not validate_uuid(user_id):
        return "user_id debe ser un UUID válido"

    if not await check_user_exists(user_id, schema_name=schema_name):
        return f"Usuario con ID '{user_id}' no encontrado"

    return None

async def validate_document_id(document_id: str, *, schema_name: str) -> Optional[str]:
    if not validate_uuid(document_id):
        return "document_id debe ser un UUID válido"

    if not await check_document_exists(document_id, schema_name=schema_name):
        return f"Documento con ID '{document_id}' no encontrado"

    return None

def validate_document_reference(reference: str) -> Optional[str]:
    return validate_required_string(reference, "reference", min_length=1, max_length=250)

def validate_rejection_reason(reason: str) -> Optional[str]:
    return validate_required_string(reason, "reason", min_length=10, max_length=500)

def validate_document_type_acronym(acronym: str) -> Optional[str]:
    if not acronym:
        return "document_type_acronym es requerido"
    
    if not isinstance(acronym, str):
        return "document_type_acronym debe ser un string"
    
    if not re.match(r'^[A-Z0-9]+$', acronym.upper()):
        return "document_type_acronym debe contener solo letras y números"
    
    if len(acronym) > 10:
        return "document_type_acronym no puede exceder 10 caracteres"
    
    return None

def validate_email(email: str) -> Optional[str]:
    if not email:
        return "Email es requerido"
    
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        return "Email no tiene formato válido"
    
    return None

def validate_pagination_params(page: int, page_size: int) -> Optional[str]:
    if page < 1:
        return "page debe ser mayor a 0"
    
    if page_size < 1:
        return "page_size debe ser mayor a 0"
    
    if page_size > 100:
        return "page_size no puede ser mayor a 100"
    
    return None

async def validate_document_signers(
    signers: List[Dict[str, Any]], *, schema_name: str, internal: bool = False
) -> Optional[str]:
    from config.constants import SYSTEM_TEST_USER_UUID

    if not signers:
        return "Debe especificar al menos un firmante"

    numerator_count = 0
    user_ids_seen = set()

    for i, signer in enumerate(signers):
        if not isinstance(signer, dict):
            return f"Firmante {i+1}: debe ser un objeto válido"

        if "user_id" not in signer:
            return f"Firmante {i+1}: user_id es requerido"

        if "is_numerator" not in signer:
            return f"Firmante {i+1}: is_numerator es requerido"

        user_id = signer["user_id"]
        is_numerator = signer["is_numerator"]

        if not internal and str(user_id).lower() == SYSTEM_TEST_USER_UUID.lower():
            return f"Firmante {i+1}: usuario no válido como firmante"

        user_error = await validate_user_id(user_id, schema_name=schema_name)
        if user_error:
            return f"Firmante {i+1}: {user_error}"
        
        if user_id in user_ids_seen:
            return f"Firmante {i+1}: usuario duplicado"
        user_ids_seen.add(user_id)
        
        if not isinstance(is_numerator, bool):
            return f"Firmante {i+1}: is_numerator debe ser true o false"
        
        if is_numerator:
            numerator_count += 1
    
    if numerator_count == 0:
        return "Debe especificar exactamente un numerador (is_numerator: true)"
    
    if numerator_count > 1:
        return "Solo puede haber un numerador por documento"
    
    return None

_DATA_IMAGE_OK = ("data:image/png", "data:image/jpeg", "data:image/webp", "data:image/gif")

_STYLE_PROHIBIDO = ("url(", "@import", "expression(", "javascript:")


def _filtro_formato_inline(tag: str, attr: str, value: str):
    valor = (value or "").strip()
    if attr == "style":
        bajo = valor.lower()
        return None if any(p in bajo for p in _STYLE_PROHIBIDO) else value
    if valor[:5].lower() == "data:":
        bajo = valor.lower()
        if tag == "img" and attr == "src" and bajo.startswith(_DATA_IMAGE_OK):
            return value
        return None
    return value


def sanitize_html(html_content: str, *, permitir_formato_inline: bool = False) -> str:
    if not html_content:
        return ""

    allowed_tags = {
        "p", "br", "hr", "div", "span", "blockquote", "pre",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "strong", "b", "em", "i", "u", "s", "sub", "sup", "mark",
        "ul", "ol", "li",
        "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "colgroup", "col",
        "img", "a",
        "figure", "figcaption", "details", "summary",
    }

    allowed_attributes = {
        "*": {"class"},
        "a": {"href", "title", "target"},
        "img": {"src", "alt", "title", "width", "height"},
        "td": {"colspan", "rowspan"},
        "th": {"colspan", "rowspan"},
        "col": {"span"},
        "colgroup": {"span"},
        "table": {"border", "cellpadding", "cellspacing"},
    }

    url_schemes = {"http", "https", "mailto"}
    attribute_filter = None

    if permitir_formato_inline:
        allowed_attributes = {tag: set(attrs) for tag, attrs in allowed_attributes.items()}
        allowed_attributes["*"].add("style")
        url_schemes = url_schemes | {"data"}
        attribute_filter = _filtro_formato_inline

    return nh3.clean(
        html_content,
        tags=allowed_tags,
        attributes=allowed_attributes,
        url_schemes=url_schemes,
        attribute_filter=attribute_filter,
        link_rel="noopener noreferrer",
    )


_IMAGE_MAGIC_BYTES = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/webp": (b"RIFF",),
}


def detect_image_mime(data: bytes) -> Optional[str]:
    if not data:
        return None

    if data.startswith(_IMAGE_MAGIC_BYTES["image/png"][0]):
        return "image/png"

    if data.startswith(_IMAGE_MAGIC_BYTES["image/jpeg"][0]):
        return "image/jpeg"

    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"

    return None
