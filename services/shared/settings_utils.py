
from typing import Optional, Dict, Tuple
from datetime import datetime, timedelta
from config.constants import DEFAULT_CITY, DEFAULT_LOGO_URL
from shared.exceptions import TransientLookupError
from shared.logging import get_logger

logger = get_logger(__name__)


_settings_cache: Dict[str, Tuple[dict, datetime]] = {}
CACHE_TTL_SECONDS = 300


async def get_tenant_settings(schema_name: str) -> dict:
    now = datetime.now()

    if schema_name in _settings_cache:
        settings, cached_at = _settings_cache[schema_name]
        if now - cached_at < timedelta(seconds=CACHE_TTL_SECONDS):
            return settings

    from database import fetch_one

    try:
        result = await fetch_one(
            "SELECT logo_url, isologo_url, city, annual_slogan FROM settings LIMIT 1",
            schema_name=schema_name,
        )

        if result:
            settings = {
                "logo_url": result["logo_url"],
                "isologo_url": result["isologo_url"],
                "city": result["city"] or None,
                "annual_slogan": result["annual_slogan"] or "",
            }
        else:
            settings = {
                "logo_url": None,
                "isologo_url": None,
                "city": None,
                "annual_slogan": "",
            }

        _settings_cache[schema_name] = (settings, now)
        return settings

    except Exception as e:
        logger.error(
            "settings.read_failed schema=%s err=%s — no se devuelve default: "
            "no se puede afirmar la configuración de un municipio que no se pudo leer",
            schema_name, e,
        )
        raise TransientLookupError(
            "No se pudo leer la configuración del municipio. "
            "Reintentá en unos segundos."
        ) from e


async def get_logo_url(*, schema_name: str) -> str:
    settings = await get_tenant_settings(schema_name)
    return settings.get("logo_url") or DEFAULT_LOGO_URL


def invalidate_settings_cache(*, schema_name: Optional[str] = None):
    global _settings_cache
    if schema_name:
        _settings_cache.pop(schema_name, None)
    else:
        _settings_cache.clear()


async def get_city_from_settings(conn=None, *, schema_name: Optional[str] = None) -> str:
    try:
        if conn:
            result = await conn.fetchrow("SELECT city FROM settings LIMIT 1")
            if result is None:
                raise TransientLookupError(
                    "No se pudo determinar la ciudad del municipio "
                    "(settings sin fila). No se firma con una ciudad sin verificar."
                )
            if result["city"]:
                return result["city"]
            logger.warning(
                "settings.city_no_configurada schema=%s — se usa el default %s. "
                "Esto es un dato ausente, no un error de lectura.",
                schema_name, DEFAULT_CITY,
            )
            return DEFAULT_CITY
        else:
            settings = await get_tenant_settings(schema_name)
            city = settings.get("city")
            if city:
                return city
            logger.warning(
                "settings.city_no_configurada schema=%s — se usa el default %s. "
                "Esto es un dato ausente, no un error de lectura.",
                schema_name, DEFAULT_CITY,
            )
            return DEFAULT_CITY

    except TransientLookupError:
        raise
    except Exception as e:
        logger.error(
            "settings.city_read_failed schema=%s err=%s — fail-closed (GDI-302)",
            schema_name, e,
        )
        raise TransientLookupError(
            "No se pudo leer la ciudad del municipio. Reintentá en unos segundos."
        ) from e
