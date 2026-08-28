
from shared.logging import get_logger
import re
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from database import fetch_all, fetch_one
from config.constants import DEFAULT_LOGO_URL, DEFAULT_ISOLOGO_URL

_SAFE_SCHEMA_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_]{0,62}$')

logger = get_logger(__name__)

_user_tenant_cache: Dict[str, Dict] = {}
CACHE_TTL_MINUTES = 5

_valid_schemas_cache: Optional[Dict] = None


async def get_user_tenants(email: str) -> List[Dict]:
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
    tenants = await get_user_tenants(email)
    allowed_schemas = [t["schema_name"] for t in tenants]

    has_access = schema_name in allowed_schemas

    if not has_access:
        logger.warning(f"Usuario sin acceso a schema '{schema_name}'")

    return has_access


def invalidate_user_cache(email: str) -> None:
    if email in _user_tenant_cache:
        del _user_tenant_cache[email]
        logger.info("Cache invalidado para usuario")


def invalidate_schemas_cache() -> None:
    global _valid_schemas_cache
    _valid_schemas_cache = None
    logger.info("Cache de schemas válidos invalidado")


def clear_all_cache() -> None:
    global _user_tenant_cache, _valid_schemas_cache
    _user_tenant_cache.clear()
    _valid_schemas_cache = None
    logger.info("Cache de tenants y schemas COMPLETAMENTE limpiado")


async def get_valid_schemas() -> List[str]:
    global _valid_schemas_cache

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

        _valid_schemas_cache = {
            "schemas": schemas,
            "cached_at": datetime.now(),
        }

        logger.debug(f"Schemas válidos: {schemas}")
        return schemas

    except Exception as e:
        logger.error(
            "TENANT_WHITELIST_UNAVAILABLE: no se pudieron validar los "
            "municipios contra public.municipalities (%s: %s). "
            "El request será rechazado (fail-closed).",
            type(e).__name__, e,
        )
        raise RuntimeError(
            "No se pudo obtener la lista de tenants válidos"
        ) from e


async def is_valid_schema_regex(schema_name: str) -> bool:
    return bool(_SAFE_SCHEMA_RE.match(schema_name))


async def is_valid_schema(schema_name: str) -> bool:
    import database as _db

    if _db.TESTING_MODE and _db.pool is None:
        is_valid = await is_valid_schema_regex(schema_name)
        if not is_valid:
            logger.warning(f"Schema inválido (regex) detectado en TESTING_MODE: '{schema_name}'")
        return is_valid

    try:
        valid_schemas = await get_valid_schemas()
    except Exception as e:
        logger.error(
            "is_valid_schema: whitelist no disponible, rechazando '%s' (%s: %s)",
            schema_name, type(e).__name__, e,
        )
        return False

    is_valid = schema_name in valid_schemas

    if not is_valid:
        logger.warning(f"Schema inválido detectado: '{schema_name}'")

    return is_valid
