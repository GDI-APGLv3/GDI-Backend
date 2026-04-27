"""
Utilidades generales compartidas por toda la aplicación.
Funciones auxiliares para formateo, conversiones y operaciones comunes.
"""

import json
import uuid
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Union
from decimal import Decimal


def get_authenticated_user(user_id: str, *, schema_name: str) -> str:
    """
    Helper para validar y obtener usuario autenticado.

    Args:
        user_id: ID del usuario a validar
        schema_name: Schema del tenant (requerido para multi-tenant)

    Returns:
        str: user_id validado

    Raises:
        ValidationError: Si el usuario no existe
    """
    from database import execute_query
    from services.case_queries import get_user_validation_query
    from shared.exceptions import ValidationError
    from config.constants import USER_NOT_FOUND_ERROR

    user_result = execute_query(get_user_validation_query(), (user_id,), schema_name=schema_name)
    
    if not user_result:
        raise ValidationError(USER_NOT_FOUND_ERROR)
    
    return str(user_result[0]['user_id'])


def get_user_global_search_flags(user_id: str, *, schema_name: str) -> dict:
    """
    Obtiene los flags de busqueda global de un usuario.

    Args:
        user_id: ID del usuario
        schema_name: Schema del tenant (keyword-only)

    Returns:
        dict con 'can_global_search_documents' y 'can_global_search_cases' (bool)
        Defaults a False si el usuario no existe o los campos son NULL.
    """
    from database import execute_query

    result = execute_query(
        "SELECT can_global_search_documents, can_global_search_cases FROM users WHERE id = %s LIMIT 1",
        (user_id,),
        schema_name=schema_name
    )

    if not result:
        return {"can_global_search_documents": False, "can_global_search_cases": False}

    return {
        "can_global_search_documents": result[0].get("can_global_search_documents", False),
        "can_global_search_cases": result[0].get("can_global_search_cases", False),
    }


def generate_uuid() -> str:
    """
    Genera un UUID aleatorio como string.
    
    Returns:
        UUID v4 como string
    """
    return str(uuid.uuid4())

def generate_document_number(prefix: str = "DOC") -> str:
    """
    Genera un número de documento único.
    
    Args:
        prefix: Prefijo para el número (por defecto "DOC")
        
    Returns:
        Número de documento único
    """
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    unique_suffix = str(uuid.uuid4())[:8].upper()
    return f"{prefix}-{timestamp}-{unique_suffix}"

def format_datetime(dt: datetime, format_string: str = "%Y-%m-%d %H:%M:%S") -> str:
    """
    Formatea un datetime a string.
    
    Args:
        dt: Datetime a formatear
        format_string: Formato de salida
        
    Returns:
        Datetime formateado como string
    """
    if not dt:
        return ""
    return dt.strftime(format_string)

def format_date(date_obj: date, format_string: str = "%Y-%m-%d") -> str:
    """
    Formatea una fecha a string.
    
    Args:
        date_obj: Fecha a formatear
        format_string: Formato de salida
        
    Returns:
        Fecha formateada como string
    """
    if not date_obj:
        return ""
    return date_obj.strftime(format_string)

def parse_datetime(date_string: str, format_string: str = "%Y-%m-%d %H:%M:%S") -> Optional[datetime]:
    """
    Convierte un string a datetime.
    
    Args:
        date_string: String con fecha/hora
        format_string: Formato del string de entrada
        
    Returns:
        Datetime object o None si hay error
    """
    try:
        return datetime.strptime(date_string, format_string)
    except (ValueError, TypeError):
        return None

def safe_json_loads(json_string: str, default: Any = None) -> Any:
    """
    Convierte JSON string a objeto de forma segura.
    
    Args:
        json_string: String JSON a convertir
        default: Valor por defecto si hay error
        
    Returns:
        Objeto Python o valor por defecto
    """
    try:
        return json.loads(json_string) if json_string else default
    except (json.JSONDecodeError, TypeError):
        return default

def safe_json_dumps(obj: Any, default: str = "{}") -> str:
    """
    Convierte objeto a JSON string de forma segura.
    
    Args:
        obj: Objeto a convertir
        default: Valor por defecto si hay error
        
    Returns:
        JSON string o valor por defecto
    """
    try:
        return json.dumps(obj, ensure_ascii=False, cls=CustomJSONEncoder)
    except (TypeError, ValueError):
        return default

class CustomJSONEncoder(json.JSONEncoder):
    """
    Encoder JSON personalizado que maneja tipos especiales.
    """
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
    """
    Limpia y normaliza un string.
    
    Args:
        text: Texto a limpiar
        max_length: Longitud máxima (opcional)
        
    Returns:
        Texto limpio
    """
    if not text:
        return ""
    
    # Quitar espacios extra y normalizar
    cleaned = " ".join(text.strip().split())
    
    # Truncar si es necesario
    if max_length and len(cleaned) > max_length:
        cleaned = cleaned[:max_length].strip()
    
    return cleaned

def extract_numbers(text: str) -> List[str]:
    """
    Extrae todos los números de un texto.
    
    Args:
        text: Texto del cual extraer números
        
    Returns:
        Lista de números encontrados como strings
    """
    import re
    if not text:
        return []
    
    # Encuentra números (enteros y decimales)
    numbers = re.findall(r'\d+\.?\d*', text)
    return numbers

def normalize_text(text: str) -> str:
    """
    Normaliza texto para búsquedas y comparaciones.
    
    Args:
        text: Texto a normalizar
        
    Returns:
        Texto normalizado (minúsculas, sin acentos, etc.)
    """
    import unicodedata
    
    if not text:
        return ""
    
    # Convertir a minúsculas
    text = text.lower()
    
    # Remover acentos
    text = unicodedata.normalize('NFD', text)
    text = ''.join(char for char in text if unicodedata.category(char) != 'Mn')
    
    # Limpiar espacios extra
    text = " ".join(text.split())
    
    return text

def chunk_list(lst: List[Any], chunk_size: int) -> List[List[Any]]:
    """
    Divide una lista en chunks de tamaño específico.
    
    Args:
        lst: Lista a dividir
        chunk_size: Tamaño de cada chunk
        
    Returns:
        Lista de chunks
    """
    if chunk_size <= 0:
        return [lst]
    
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]

def merge_dicts(dict1: Dict, dict2: Dict, override: bool = True) -> Dict:
    """
    Fusiona dos diccionarios.
    
    Args:
        dict1: Primer diccionario (base)
        dict2: Segundo diccionario (a fusionar)
        override: Si True, dict2 sobrescribe valores de dict1
        
    Returns:
        Diccionario fusionado
    """
    result = dict1.copy()
    
    for key, value in dict2.items():
        if override or key not in result:
            result[key] = value
    
    return result

def get_nested_value(data: Dict, path: str, default: Any = None, separator: str = ".") -> Any:
    """
    Obtiene un valor anidado de un diccionario usando path con notación de puntos.
    
    Args:
        data: Diccionario fuente
        path: Path del valor (ej: "user.profile.name")
        default: Valor por defecto si no se encuentra
        separator: Separador del path
        
    Returns:
        Valor encontrado o valor por defecto
    """
    try:
        keys = path.split(separator)
        value = data
        
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        
        return value
    except (AttributeError, KeyError, TypeError):
        return default

def set_nested_value(data: Dict, path: str, value: Any, separator: str = ".") -> Dict:
    """
    Establece un valor anidado en un diccionario usando path con notación de puntos.
    
    Args:
        data: Diccionario destino
        path: Path donde establecer el valor
        value: Valor a establecer
        separator: Separador del path
        
    Returns:
        Diccionario modificado
    """
    keys = path.split(separator)
    current = data
    
    # Navegar hasta el penúltimo nivel
    for key in keys[:-1]:
        if key not in current:
            current[key] = {}
        current = current[key]
    
    # Establecer el valor final
    current[keys[-1]] = value
    return data

def calculate_offset(page: int, page_size: int) -> int:
    """
    Calcula el offset para consultas paginadas.
    
    Args:
        page: Número de página (1-based)
        page_size: Tamaño de página
        
    Returns:
        Offset para la consulta
    """
    return (page - 1) * page_size

def calculate_total_pages(total_records: int, page_size: int) -> int:
    """
    Calcula el número total de páginas para paginación.
    
    Args:
        total_records: Total de registros
        page_size: Tamaño de página
        
    Returns:
        Número total de páginas
    """
    if page_size <= 0:
        return 0
    
    return (total_records + page_size - 1) // page_size

def format_file_size(size_bytes: int) -> str:
    """
    Formatea un tamaño de archivo en bytes a formato legible.
    
    Args:
        size_bytes: Tamaño en bytes
        
    Returns:
        Tamaño formateado (ej: "1.5 MB")
    """
    if size_bytes == 0:
        return "0 B"
    
    size_names = ["B", "KB", "MB", "GB", "TB"]
    size_index = 0
    size = float(size_bytes)
    
    while size >= 1024 and size_index < len(size_names) - 1:
        size /= 1024
        size_index += 1
    
    if size_index == 0:
        return f"{int(size)} {size_names[size_index]}"
    else:
        return f"{size:.1f} {size_names[size_index]}"

def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    """
    Trunca texto a una longitud máxima con sufijo opcional.

    Args:
        text: Texto a truncar
        max_length: Longitud máxima
        suffix: Sufijo para texto truncado

    Returns:
        Texto truncado
    """
    if not text or len(text) <= max_length:
        return text

    if len(suffix) >= max_length:
        return text[:max_length]

    return text[:max_length - len(suffix)] + suffix
