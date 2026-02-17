"""
Validación de acceso multi-tenant con cache.
Gestiona permisos de usuarios para acceder a diferentes municipalidades (schemas).
"""

import logging
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from database import execute_query
from config.constants import DEFAULT_LOGO_URL, DEFAULT_ISOLOGO_URL

logger = logging.getLogger(__name__)

# Cache de accesos de usuarios a tenants
# Estructura: {email: {"tenants": List[TenantAccess], "cached_at": datetime}}
_user_tenant_cache: Dict[str, Dict] = {}
CACHE_TTL_MINUTES = 30


def get_user_tenants(email: str) -> List[Dict]:
    """
    Obtiene la lista de municipalidades (tenants) a las que un usuario tiene acceso.

    Args:
        email: Email del usuario

    Returns:
        Lista de diccionarios con schema_name, display_name, is_default
    """
    cached_entry = _user_tenant_cache.get(email)
    if cached_entry:
        cached_at = cached_entry.get("cached_at")
        if cached_at and datetime.now() - cached_at < timedelta(minutes=CACHE_TTL_MINUTES):
            logger.debug(f"Cache HIT para {email} (edad: {datetime.now() - cached_at})")
            return cached_entry["tenants"]
        else:
            logger.debug(f"Cache EXPIRED para {email}")

    # Cache miss o expirado - consultar BD
    logger.info(f"Consultando tenants para {email}")

    # Query a user_registry (tabla en schema public)
    # Usamos m.name (fuente de verdad) en lugar de ur.display_name
    query = """
        SELECT
            ur.schema_name,
            m.name as display_name,
            ur.is_default
        FROM public.user_registry ur
        INNER JOIN public.municipalities m ON ur.schema_name = m.schema_name
        WHERE ur.email = %s AND m.is_active = true
        ORDER BY ur.is_default DESC, m.name ASC
    """

    try:
        results = execute_query(query, (email.lower(),), fetch=True, schema_name="public")
        tenants = []
        for row in (results or []):
            tenant_data = {
                "schema_name": row["schema_name"],
                "display_name": row["display_name"],
                "is_default": row["is_default"],
                "logo_url": DEFAULT_LOGO_URL,
                "isologo_url": DEFAULT_ISOLOGO_URL,
                "primary_color": None
            }
            # Obtener logos de settings del tenant
            try:
                settings_query = "SELECT logo_url, isologo_url, primary_color FROM settings LIMIT 1"
                settings_result = execute_query(
                    settings_query,
                    fetch=True,
                    fetch_one=True,
                    schema_name=row["schema_name"]
                )
                if settings_result:
                    tenant_data["logo_url"] = settings_result.get("logo_url") or DEFAULT_LOGO_URL
                    tenant_data["isologo_url"] = settings_result.get("isologo_url") or DEFAULT_ISOLOGO_URL
                    tenant_data["primary_color"] = settings_result.get("primary_color")
            except Exception as settings_err:
                logger.warning(f"Error obteniendo logos de {row['schema_name']}: {settings_err}")

            tenants.append(tenant_data)
    except Exception as e:
        logger.error(f"Error consultando user_registry: {e}")
        tenants = []

    # Guardar en cache
    _user_tenant_cache[email] = {
        "tenants": tenants,
        "cached_at": datetime.now()
    }

    logger.info(f"Usuario {email} tiene acceso a {len(tenants)} tenant(s)")
    return tenants


def validate_tenant_access(email: str, schema_name: str) -> bool:
    """
    Valida si un usuario tiene acceso a una municipalidad específica.

    Args:
        email: Email del usuario
        schema_name: Nombre del schema a validar

    Returns:
        True si tiene acceso, False en caso contrario
    """
    tenants = get_user_tenants(email)
    allowed_schemas = [t["schema_name"] for t in tenants]

    has_access = schema_name in allowed_schemas

    if not has_access:
        logger.warning(f"Usuario {email} NO tiene acceso a schema '{schema_name}'")

    return has_access


def invalidate_user_cache(email: str) -> None:
    """
    Invalida el cache de tenants para un usuario específico.
    Útil cuando se modifican los permisos del usuario.

    Args:
        email: Email del usuario
    """
    if email in _user_tenant_cache:
        del _user_tenant_cache[email]
        logger.info(f"Cache invalidado para {email}")


def clear_all_cache() -> None:
    """
    Limpia TODO el cache de tenants.
    Útil al reiniciar el servidor o para debugging.
    """
    global _user_tenant_cache
    _user_tenant_cache.clear()
    logger.info("Cache de tenants COMPLETAMENTE limpiado")


def get_valid_schemas() -> List[str]:
    """
    Obtiene la lista de schemas válidos desde la tabla municipalities.
    Se usa para validar contra SQL injection en SET search_path.

    Returns:
        Lista de nombres de schemas activos

    Note:
        Incluye 'public' por defecto para compatibilidad.
    """
    query = """
        SELECT schema_name
        FROM municipalities
        WHERE is_active = true
    """

    try:
        results = execute_query(query, fetch=True, schema_name="public") or []
        schemas = [row["schema_name"] for row in results]

        # Siempre incluir 'public' para compatibilidad
        if "public" not in schemas:
            schemas.append("public")

        logger.debug(f"Schemas válidos: {schemas}")
        return schemas

    except Exception as e:
        logger.error(f"Error obteniendo schemas válidos: {e}")
        # Fallback: solo public
        return ["public"]


def is_valid_schema(schema_name: str) -> bool:
    """
    Verifica si un schema_name es válido (existe en municipalities).
    Protege contra SQL injection en SET search_path.

    Args:
        schema_name: Nombre del schema a validar

    Returns:
        True si es válido, False en caso contrario
    """
    valid_schemas = get_valid_schemas()
    is_valid = schema_name in valid_schemas

    if not is_valid:
        logger.warning(f"Schema inválido detectado: '{schema_name}'")

    return is_valid
