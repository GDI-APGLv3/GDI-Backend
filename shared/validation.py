"""
Funciones de validación compartidas utilizadas por múltiples módulos.
Centraliza las validaciones comunes para mantener consistencia.
"""

import re
from uuid import UUID
from typing import Optional, List, Dict, Any
import nh3
from database import check_user_exists, check_document_exists

def validate_uuid(uuid_string: str) -> bool:
    """
    Valida que un string sea un UUID válido.
    
    Args:
        uuid_string: String a validar
        
    Returns:
        True si es UUID válido, False en caso contrario
    """
    try:
        UUID(uuid_string)
        return True
    except (ValueError, TypeError):
        return False

def validate_required_string(value: Any, field_name: str, min_length: int = 1, max_length: Optional[int] = None) -> Optional[str]:
    """
    Valida que un campo sea string requerido con longitud específica.
    
    Args:
        value: Valor a validar
        field_name: Nombre del campo (para mensajes de error)
        min_length: Longitud mínima requerida
        max_length: Longitud máxima permitida (opcional)
        
    Returns:
        None si es válido, mensaje de error si no es válido
    """
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

def validate_user_id(user_id: str, *, schema_name: str) -> Optional[str]:
    """
    Valida que un user_id sea UUID válido y que el usuario exista.

    Args:
        user_id: UUID del usuario a validar
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        None si es válido, mensaje de error si no es válido
    """
    # Validar formato UUID
    if not validate_uuid(user_id):
        return "user_id debe ser un UUID válido"

    # Validar que el usuario existe
    if not check_user_exists(user_id, schema_name=schema_name):
        return f"Usuario con ID '{user_id}' no encontrado"

    return None

def validate_document_id(document_id: str, *, schema_name: str) -> Optional[str]:
    """
    Valida que un document_id sea UUID válido y que el documento exista.

    Args:
        document_id: UUID del documento a validar
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        None si es válido, mensaje de error si no es válido
    """
    # Validar formato UUID
    if not validate_uuid(document_id):
        return "document_id debe ser un UUID válido"

    # Validar que el documento existe
    if not check_document_exists(document_id, schema_name=schema_name):
        return f"Documento con ID '{document_id}' no encontrado"

    return None

def validate_document_reference(reference: str) -> Optional[str]:
    """
    Valida que la referencia de un documento sea válida.
    
    Args:
        reference: Referencia del documento
        
    Returns:
        None si es válido, mensaje de error si no es válido
    """
    return validate_required_string(reference, "reference", min_length=1, max_length=250)

def validate_rejection_reason(reason: str) -> Optional[str]:
    """
    Valida que el motivo de rechazo sea válido.
    
    Args:
        reason: Motivo del rechazo
        
    Returns:
        None si es válido, mensaje de error si no es válido
    """
    return validate_required_string(reason, "reason", min_length=10, max_length=500)

def validate_document_type_acronym(acronym: str) -> Optional[str]:
    """
    Valida que el acrónimo de tipo de documento sea válido.
    
    Args:
        acronym: Acrónimo del tipo de documento
        
    Returns:
        None si es válido, mensaje de error si no es válido
    """
    if not acronym:
        return "document_type_acronym es requerido"
    
    if not isinstance(acronym, str):
        return "document_type_acronym debe ser un string"
    
    # Validar formato básico (letras y números, sin espacios)
    if not re.match(r'^[A-Z0-9]+$', acronym.upper()):
        return "document_type_acronym debe contener solo letras y números"
    
    if len(acronym) > 10:
        return "document_type_acronym no puede exceder 10 caracteres"
    
    return None

def validate_email(email: str) -> Optional[str]:
    """
    Valida que un email tenga formato válido.
    
    Args:
        email: Email a validar
        
    Returns:
        None si es válido, mensaje de error si no es válido
    """
    if not email:
        return "Email es requerido"
    
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        return "Email no tiene formato válido"
    
    return None

def validate_pagination_params(page: int, page_size: int) -> Optional[str]:
    """
    Valida parámetros de paginación.
    
    Args:
        page: Número de página
        page_size: Tamaño de página
        
    Returns:
        None si son válidos, mensaje de error si no son válidos
    """
    if page < 1:
        return "page debe ser mayor a 0"
    
    if page_size < 1:
        return "page_size debe ser mayor a 0"
    
    if page_size > 100:
        return "page_size no puede ser mayor a 100"
    
    return None

def validate_document_signers(signers: List[Dict[str, Any]], *, schema_name: str) -> Optional[str]:
    """
    Valida una lista de firmantes de documento.

    Args:
        signers: Lista de firmantes con user_id e is_numerator
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        None si son válidos, mensaje de error si no son válidos
    """
    if not signers:
        return "Debe especificar al menos un firmante"
    
    numerator_count = 0
    user_ids_seen = set()
    
    for i, signer in enumerate(signers):
        # Validar estructura
        if not isinstance(signer, dict):
            return f"Firmante {i+1}: debe ser un objeto válido"
        
        if "user_id" not in signer:
            return f"Firmante {i+1}: user_id es requerido"
        
        if "is_numerator" not in signer:
            return f"Firmante {i+1}: is_numerator es requerido"
        
        user_id = signer["user_id"]
        is_numerator = signer["is_numerator"]
        
        # Validar user_id
        user_error = validate_user_id(user_id, schema_name=schema_name)
        if user_error:
            return f"Firmante {i+1}: {user_error}"
        
        # Validar duplicados
        if user_id in user_ids_seen:
            return f"Firmante {i+1}: usuario duplicado"
        user_ids_seen.add(user_id)
        
        # Validar is_numerator
        if not isinstance(is_numerator, bool):
            return f"Firmante {i+1}: is_numerator debe ser true o false"
        
        if is_numerator:
            numerator_count += 1
    
    # Validar que hay exactamente un numerador
    if numerator_count == 0:
        return "Debe especificar exactamente un numerador (is_numerator: true)"
    
    if numerator_count > 1:
        return "Solo puede haber un numerador por documento"
    
    return None

def sanitize_html(html_content: str) -> str:
    """
    Sanitiza contenido HTML usando nh3 con allowlist de tags seguros.
    Previene XSS stored eliminando tags/atributos no permitidos.

    Args:
        html_content: Contenido HTML a sanitizar

    Returns:
        Contenido HTML sanitizado
    """
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
        "*": {"class", "id"},
        "a": {"href", "title", "target"},  # rel lo maneja link_rel automaticamente
        "img": {"src", "alt", "title", "width", "height"},
        "td": {"colspan", "rowspan"},
        "th": {"colspan", "rowspan"},
        "col": {"span"},
        "colgroup": {"span"},
        "table": {"border", "cellpadding", "cellspacing"},
    }

    return nh3.clean(
        html_content,
        tags=allowed_tags,
        attributes=allowed_attributes,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer",
    )
