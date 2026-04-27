"""
Servicio de búsqueda de usuarios - Clean Code Implementation.
Aplicando principios SOLID, DRY y Single Responsibility Principle.

Arquitectura organizada por responsabilidades:
1. Validación de entrada
2. Construcción de queries (AHORA CENTRALIZADAS)
3. Ejecución de consultas  
4. Formateo de respuestas
5. Funciones de utilidad
6. Invitación de usuarios por email
"""

import re
from shared.logging import get_logger
from typing import Dict, Any, List, Optional
from database import get_db_cursor
from services.users.queries import (
    search_users_by_name_query,
    count_users_by_name_query,
    search_user_by_email_query
)
from config.constants import (
    SEARCH_QUERY_REQUIRED_ERROR,
    SEARCH_QUERY_MIN_LENGTH_ERROR,
    SEARCH_QUERY_MAX_LENGTH_ERROR,
    SEARCH_LIMIT_INVALID_ERROR,
    SEARCH_LIMIT_MAX_ERROR,
    SEARCH_EMAIL_INVALID_FORMAT_ERROR,
    SEARCH_MIN_LENGTH,
    SEARCH_MAX_LENGTH,
    SEARCH_MAX_LIMIT
)
from shared.exceptions import ValidationError

logger = get_logger(__name__)

# ============================================================================
# FUNCIONES PRINCIPALES - Single Responsibility Principle
# ============================================================================

def search_users_for_autocomplete(search_query: str, limit: Optional[int] = None, *, schema_name: str) -> Dict[str, Any]:
    """
    Busca usuarios para autocompletado con información completa.

    Implementa Clean Architecture: Orquesta todas las responsabilidades
    pero delega la implementación a funciones especializadas.

    Args:
        search_query: Texto a buscar al inicio de nombres o apellidos
        limit: Número máximo de resultados (None = sin límite)
        schema_name: Nombre del schema (para multi-tenant)

    Returns:
        Dict con 'users' (List) y 'total_found' (int)

    Raises:
        ValidationError: Si los parámetros son inválidos
    """
    logger.info(f"Iniciando búsqueda de usuarios con query: '{search_query}', limit: {limit}")

    # 1. Validación de entrada - Single Responsibility
    _validate_search_parameters(search_query, limit)

    # 2. Construcción de patrones de búsqueda - Single Responsibility
    search_patterns = _build_user_search_patterns(search_query)

    # 3. Ejecución de consulta principal - Single Responsibility
    users_raw_data = _fetch_users_with_search_patterns(search_patterns, limit, schema_name=schema_name)

    # 4. Conteo total - Single Responsibility
    total_count = _count_users_with_search_patterns(search_patterns, schema_name=schema_name)

    # 5. Formateo de respuesta - Single Responsibility
    formatted_users = _format_user_search_results(users_raw_data)

    logger.info(f"Búsqueda completada: {len(formatted_users)} usuarios retornados de {total_count} encontrados")

    return {
        "users": formatted_users,
        "total_found": total_count
    }

# ============================================================================
# VALIDACIÓN - Single Responsibility Principle
# ============================================================================

def _validate_search_parameters(search_query: str, limit: Optional[int]) -> None:
    """
    Valida parámetros de búsqueda de usuarios.
    Aplicando Single Responsibility: Solo validación.
    """
    if not search_query or not isinstance(search_query, str):
        raise ValidationError(SEARCH_QUERY_REQUIRED_ERROR)
    
    clean_query = search_query.strip()
    if len(clean_query) < SEARCH_MIN_LENGTH:
        raise ValidationError(SEARCH_QUERY_MIN_LENGTH_ERROR.format(min_length=SEARCH_MIN_LENGTH))
    
    if len(clean_query) > SEARCH_MAX_LENGTH:
        raise ValidationError(SEARCH_QUERY_MAX_LENGTH_ERROR.format(max_length=SEARCH_MAX_LENGTH))
    
    if limit is not None:
        if not isinstance(limit, int) or limit < 1:
            raise ValidationError(SEARCH_LIMIT_INVALID_ERROR)
        if limit > SEARCH_MAX_LIMIT:
            raise ValidationError(SEARCH_LIMIT_MAX_ERROR.format(max_limit=SEARCH_MAX_LIMIT))

# ============================================================================
# CONSTRUCCIÓN DE PATRONES - Single Responsibility Principle  
# ============================================================================

def _build_user_search_patterns(search_query: str) -> Dict[str, str]:
    """
    Construye patrones de búsqueda para nombres y apellidos.
    Aplicando Single Responsibility: Solo construcción de patrones.
    """
    clean_query = search_query.strip().lower()
    
    return {
        "pattern_start": f"{clean_query}%",
        "pattern_word_start": f"% {clean_query}%",
        "search_term": clean_query,
        "original_query": clean_query
    }

# ============================================================================
# EJECUCIÓN DE CONSULTAS - Single Responsibility Principle
# ============================================================================

def _fetch_users_with_search_patterns(search_patterns: Dict[str, str], limit: Optional[int], *, schema_name: str) -> List[Dict]:
    """
    Ejecuta la consulta principal para obtener usuarios.
    Aplicando Single Responsibility: Solo ejecución de consulta.

    ACTUALIZADO: Usa get_db_cursor y queries centralizadas.
    """
    logger.info(f"Fetching users with search patterns: {search_patterns['original_query']}, limit: {limit}")

    try:
        query = search_users_by_name_query()

        with get_db_cursor(schema_name=schema_name) as cursor:
            cursor.execute(
                query,
                {
                    "pattern_start": search_patterns["pattern_start"],
                    "pattern_word_start": search_patterns["pattern_word_start"],
                    "search_term": search_patterns["search_term"],
                    "limit": limit
                }
            )
            results = cursor.fetchall()
        
        logger.info(f"Found {len(results)} users matching search patterns")
        return results
        
    except Exception as e:
        logger.error(f"Error fetching users with search patterns: {str(e)}", exc_info=True)
        raise

def _count_users_with_search_patterns(search_patterns: Dict[str, str], *, schema_name: str) -> int:
    """
    Ejecuta la consulta de conteo de usuarios encontrados.
    Aplicando Single Responsibility: Solo conteo.

    ACTUALIZADO: Usa get_db_cursor y queries centralizadas.
    """
    logger.info(f"Counting users with search patterns: {search_patterns['original_query']}")

    try:
        query = count_users_by_name_query()

        with get_db_cursor(schema_name=schema_name) as cursor:
            cursor.execute(
                query,
                {
                    "pattern_start": search_patterns["pattern_start"],
                    "pattern_word_start": search_patterns["pattern_word_start"],
                    "search_term": search_patterns["search_term"]
                }
            )
            result = cursor.fetchone()
        
        count = result['count'] if result else 0
        logger.info(f"Total count: {count} users")
        return count
        
    except Exception as e:
        logger.error(f"Error counting users with search patterns: {str(e)}", exc_info=True)
        raise

# ============================================================================
# FORMATEO DE RESPUESTAS - Single Responsibility Principle
# ============================================================================

def _format_user_search_results(users_raw_data: List[Dict]) -> List[Dict]:
    """
    Formatea los datos crudos de usuarios a la estructura esperada.
    Aplicando Single Responsibility: Solo formateo de datos.
    
    ACTUALIZADO: Incluye campo email para consistencia con usuarios virtuales.
    """
    formatted_users = []
    
    for user in users_raw_data:
        formatted_user = {
            "user_id": user['user_id'],
            "full_name": user['full_name'],
            "email": user.get('email'),  # Incluir email para todos los usuarios
            "department_acronym": user.get('department_acronym'),  # Acrónimo del departamento (ej: ADGEN, OBPU)
            "sector_acronym": user.get('sector_acronym'),  # Acrónimo del sector
            "seal_name": user.get('seal_name'),  # Nombre del sello desde city_seals
            "profile_picture_url": user.get('profile_picture_url'),
            "is_active": bool(user.get('is_active', 1))  # Conversión directa: 1=True, 0=False, None=True(default)
        }
        
        formatted_users.append(formatted_user)
    
    return formatted_users

# ============================================================================
# FUNCIONES DE UTILIDAD - Single Responsibility Principle
# ============================================================================

def _extract_user_basic_info_from_row(user_row: Dict) -> Dict[str, Any]:
    """
    Extrae información básica del usuario de una fila de BD.
    Aplicando Single Responsibility: Solo extracción de datos básicos.
    """
    return {
        "user_id": user_row['id'],
        "first_name": user_row['first_name'],
        "last_name": user_row['last_name'],
        "full_name": f"{user_row['first_name']} {user_row['last_name']}",
        "email": user_row['email'],
        "is_active": user_row['is_active']
    }

def _extract_user_department_info_from_row(user_row: Dict) -> Optional[Dict[str, str]]:
    """
    Extrae información del departamento del usuario de una fila de BD.
    Aplicando Single Responsibility: Solo extracción de datos de departamento.
    """
    if user_row.get('department_acronym'):
        return {
            "acronym": user_row['department_acronym'],
            "code": user_row.get('department_code')
        }
    return None

def _extract_user_seal_info_from_row(user_row: Dict) -> Optional[str]:
    """
    Extrae información del sello del usuario de una fila de BD.
    Aplicando Single Responsibility: Solo extracción de datos de sello.
    """
    return user_row.get('seal_name')

# ============================================================================
# FUNCIONES DE INVITACIÓN POR EMAIL - Single Responsibility Principle
# ============================================================================

def is_email(text: str) -> bool:
    """
    Detecta si el texto es un email válido.
    Aplicando Single Responsibility: Solo validación de formato de email.
    """
    if not text or not isinstance(text, str):
        return False
    
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(email_pattern, text.strip()))

def search_or_create_user_by_email(email: str, *, schema_name: str) -> Dict[str, Any]:
    """
    Busca usuario por email, NO lo crea si no existe (solo devuelve email virtual).

    Args:
        email: Email del usuario a buscar
        schema_name: Nombre del schema (para multi-tenant)

    Returns:
        Dict con 'users' (List) y 'total_found' (int)

    Raises:
        ValidationError: Si el email no es válido
    """
    logger.info(f"Searching user by email: {email}")

    # 1. Validar que sea un email válido
    if not is_email(email):
        logger.warning(f"Invalid email format: {email}")
        raise ValidationError(SEARCH_EMAIL_INVALID_FORMAT_ERROR)

    # 2. Buscar usuario existente por email (activo o inactivo)
    existing_user = _fetch_user_by_email(email.strip().lower(), schema_name=schema_name)
    
    if existing_user:
        # Usuario ya existe, devolver (activo o inactivo)
        logger.info(f"User found by email: {existing_user['user_id']}")
        formatted_user = _format_existing_user_for_search(existing_user)
        return {
            "users": [formatted_user],
            "total_found": 1
        }
    else:
        # Usuario no existe, devolver email virtual (SIN crear en BD)
        logger.info(f"User not found, returning virtual user for email: {email}")
        formatted_user = _format_virtual_user_for_search(email.strip().lower())
        
        return {
            "users": [formatted_user],
            "total_found": 1
        }

def _fetch_user_by_email(email: str, *, schema_name: str) -> Optional[Dict]:
    """
    Busca usuario por email, sin importar si está activo o inactivo.
    Aplicando Single Responsibility: Solo búsqueda por email.

    ACTUALIZADO: Usa get_db_cursor y query centralizada.
    """
    logger.info(f"Fetching user by email from database")

    try:
        query = search_user_by_email_query()

        with get_db_cursor(schema_name=schema_name) as cursor:
            cursor.execute(query, {"email": email})
            result = cursor.fetchone()
        
        if result:
            logger.info(f"User found by email: {result['user_id']}")
        else:
            logger.info("No user found with this email")
            
        return result
        
    except Exception as e:
        logger.error(f"Error fetching user by email: {str(e)}", exc_info=True)
        raise

# FUNCIÓN ELIMINADA: _create_inactive_user_with_email
# Ya no se crean usuarios en búsqueda por email
# La creación se movió a /documents/save para mejor flujo

def _format_existing_user_for_search(user_data: Dict) -> Dict[str, Any]:
    """
    Formatea usuario existente (activo o inactivo) para respuesta de búsqueda.
    Aplicando Single Responsibility: Solo formateo de usuario existente.
    """
    return {
        "user_id": user_data['user_id'],
        "full_name": user_data['full_name'],
        "email": user_data.get('email'),  # Incluir email para usuarios existentes también
        "department_acronym": user_data.get('department_acronym'),
        "seal_name": user_data.get('seal_name'),
        "profile_picture_url": user_data.get('profile_picture_url'),
        "is_active": bool(user_data.get('is_active', 0))  # estado: 1=True, 0=False
    }

def _format_virtual_user_for_search(email: str) -> Dict[str, Any]:
    """
    Formatea usuario virtual (email que no existe en BD) para respuesta de búsqueda.
    NO persiste datos en BD - solo retorna estructura temporal.
    Aplicando Single Responsibility: Solo formateo de email virtual.
    """
    return {
        "user_id": None,  # No hay user_id real
        "full_name": email,  # Mostrar el email como nombre
        "email": email,  # Incluir email para que frontend lo identifique
        "department_acronym": None,
        "seal_name": None,
        "profile_picture_url": None,
        "is_active": False  # Indica que no es usuario activo
    }

# ============================================================================
# COMENTARIOS DE ARQUITECTURA
# ============================================================================
"""
Esta implementación sigue los principios de Clean Code:

1. **Single Responsibility Principle**: Cada función tiene una responsabilidad específica
2. **Open/Closed Principle**: Fácil agregar nuevos tipos de búsqueda sin modificar código existente  
3. **Liskov Substitution**: Las funciones pueden ser intercambiadas por implementaciones alternativas
4. **Interface Segregation**: Separación clara entre validación, consulta y formateo
5. **Dependency Inversion**: Depende de abstracciones (shared modules) no de implementaciones

Organización por responsabilidades:
- Validación: _validate_*
- Construcción: _build_*  
- Ejecución: _fetch_* y _count_*
- Formateo: _format_*
- Utilidades: _extract_*
- Invitación: is_email, search_or_create_user_by_email, _create_inactive_user_with_email

Cada sección está claramente separada y documentada.
"""