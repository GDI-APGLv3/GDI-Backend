"""
Validación de acceso multi-tenant con cache.
Gestiona permisos de usuarios para acceder a diferentes municipalidades (schemas).
"""

import logging
import re
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from database import fetch_all, fetch_one
from config.constants import DEFAULT_LOGO_URL, DEFAULT_ISOLOGO_URL

# Regex para schema names seguros: alfanumérico+guión_bajo, max 63 chars (límite PostgreSQL).
# Permite nombres como "100_test" (schema de desarrollo) que empiezan con dígito.
_SAFE_SCHEMA_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_]{0,62}$')

logger = logging.getLogger(__name__)

# Cache de accesos de usuarios a tenants
# Estructura: {email: {"tenants": List[TenantAccess], "cached_at": datetime}}
# TTL corto: cambios de permisos se reflejan en ≤5 min.
_user_tenant_cache: Dict[str, Dict] = {}
CACHE_TTL_MINUTES = 5

# Cache de schemas válidos (lista global de municipios activos).
# Estructura: {"schemas": List[str], "cached_at": datetime} o None si nunca se pobló.
# TTL idéntico al cache de tenants (5 min). Los municipios casi nunca cambian;
# un municipio recién creado/activado tardará hasta 5 min en ser aceptado (aceptable).
# La creación/activación de municipios ocurre en BackOffice-Back (no en este repo),
# por lo que no existe punto de invalidación explícito local — el TTL es suficiente.
_valid_schemas_cache: Optional[Dict] = None


async def get_user_tenants(email: str) -> List[Dict]:
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
            logger.debug("Cache HIT para usuario")
            return cached_entry["tenants"]

    logger.info("Consultando tenants para usuario")

    try:
        results = await fetch_all(
            """
            SELECT
                ur.schema_name,
                m.id as municipality_id,
                m.name as display_name,
                ur.is_default
            FROM public.user_registry ur
            INNER JOIN public.municipalities m ON ur.schema_name = m.schema_name
            WHERE ur.email = $1 AND m.is_active = true
            ORDER BY ur.is_default DESC, m.name ASC
            """,
            email.lower(),
            schema_name="public",
        )
        tenants = []
        for row in (results or []):
            tenant_data = {
                "schema_name": row["schema_name"],
                # SEC-31: municipality_id es el UUID público opaco (no expone el schema interno)
                "municipality_id": str(row["municipality_id"]) if row["municipality_id"] else None,
                "display_name": row["display_name"],
                "is_default": row["is_default"],
                "logo_url": DEFAULT_LOGO_URL,
                "isologo_url": DEFAULT_ISOLOGO_URL,
                "primary_color": None
            }
            try:
                settings_result = await fetch_one(
                    "SELECT logo_url, isologo_url, primary_color FROM settings LIMIT 1",
                    schema_name=row["schema_name"],
                )
                if settings_result:
                    tenant_data["logo_url"] = settings_result["logo_url"] or DEFAULT_LOGO_URL
                    tenant_data["isologo_url"] = settings_result["isologo_url"] or DEFAULT_ISOLOGO_URL
                    tenant_data["primary_color"] = settings_result["primary_color"]
            except Exception as settings_err:
                logger.warning(f"Error obteniendo logos de {row['schema_name']}: {settings_err}")

            tenants.append(tenant_data)
    except Exception as e:
        logger.error(f"Error consultando user_registry: {e}")
        tenants = []

    _user_tenant_cache[email] = {
        "tenants": tenants,
        "cached_at": datetime.now()
    }

    logger.info(f"Usuario tiene acceso a {len(tenants)} tenant(s)")
    return tenants


async def validate_tenant_access(email: str, schema_name: str) -> bool:
    """
    Valida si un usuario tiene acceso a una municipalidad específica.

    Args:
        email: Email del usuario
        schema_name: Nombre del schema a validar

    Returns:
        True si tiene acceso, False en caso contrario
    """
    tenants = await get_user_tenants(email)
    allowed_schemas = [t["schema_name"] for t in tenants]

    has_access = schema_name in allowed_schemas

    if not has_access:
        logger.warning(f"Usuario sin acceso a schema '{schema_name}'")

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
        logger.info("Cache invalidado para usuario")


def invalidate_schemas_cache() -> None:
    """
    Invalida el cache de schemas válidos.
    Útil si se crea o activa un municipio desde este proceso (raro; normalmente
    ocurre en BackOffice-Back, donde el TTL de 5 min es suficiente).
    """
    global _valid_schemas_cache
    _valid_schemas_cache = None
    logger.info("Cache de schemas válidos invalidado")


def clear_all_cache() -> None:
    """
    Limpia TODO el cache de tenant_validation: tenants por usuario y schemas válidos.
    Útil al reiniciar el servidor o para debugging.
    """
    global _user_tenant_cache, _valid_schemas_cache
    _user_tenant_cache.clear()
    _valid_schemas_cache = None
    logger.info("Cache de tenants y schemas COMPLETAMENTE limpiado")


async def get_valid_schemas() -> List[str]:
    """
    Obtiene la lista de schemas válidos desde la tabla municipalities.
    Se usa para validar contra SQL injection en SET search_path.

    Resultado cacheado en memoria por CACHE_TTL_MINUTES (5 min).
    Patrón idéntico al cache de get_user_tenants: variable de módulo con
    {schemas, cached_at} y chequeo de timedelta antes de cada consulta.

    Returns:
        Lista de nombres de schemas activos

    Note:
        Incluye 'public' por defecto para compatibilidad.
    """
    global _valid_schemas_cache

    # Chequeo de TTL — mismo patrón que _user_tenant_cache
    if _valid_schemas_cache is not None:
        cached_at = _valid_schemas_cache.get("cached_at")
        if cached_at and datetime.now() - cached_at < timedelta(minutes=CACHE_TTL_MINUTES):
            logger.debug("Cache HIT para schemas válidos")
            return _valid_schemas_cache["schemas"]

    logger.info("Consultando schemas válidos desde BD")

    try:
        results = await fetch_all(
            "SELECT schema_name FROM municipalities WHERE is_active = true",
            schema_name="public",
        )
        schemas = [row["schema_name"] for row in results]

        # Siempre incluir 'public' para compatibilidad
        if "public" not in schemas:
            schemas.append("public")

        _valid_schemas_cache = {
            "schemas": schemas,
            "cached_at": datetime.now(),
        }

        logger.debug(f"Schemas válidos: {schemas}")
        return schemas

    except Exception as e:
        logger.error(f"Error obteniendo schemas válidos: {e}")
        # Fallback: solo public (producción sin BD).
        # NO se cachea el fallback para reintentar en el próximo request.
        return ["public"]


async def is_valid_schema_regex(schema_name: str) -> bool:
    """Validación por regex cuando el pool no está disponible (TESTING_MODE local sin BD).
    Solo acepta identificadores PostgreSQL seguros: letra inicial, alfanumérico+_, max 63 chars.
    """
    return bool(_SAFE_SCHEMA_RE.match(schema_name))


async def is_valid_schema(schema_name: str) -> bool:
    """
    Verifica si un schema_name es válido (existe en municipalities).
    Protege contra SQL injection en SET search_path.

    En producción: consulta la tabla municipalities (fuente de verdad).
    En TESTING_MODE sin pool disponible: fallback a regex seguro para dev local.
    """
    import database as _db

    if _db.TESTING_MODE and _db.pool is None:
        # Pool no disponible en dev local: el DB whitelist no aplica, regex es suficiente
        is_valid = await is_valid_schema_regex(schema_name)
        if not is_valid:
            logger.warning(f"Schema inválido (regex) detectado en TESTING_MODE: '{schema_name}'")
        return is_valid

    valid_schemas = await get_valid_schemas()
    is_valid = schema_name in valid_schemas

    if not is_valid:
        logger.warning(f"Schema inválido detectado: '{schema_name}'")

    return is_valid
