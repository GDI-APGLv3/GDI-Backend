"""
Utilidades para obtener configuraciones desde la tabla settings del tenant.

Este módulo proporciona funciones helper para acceder a configuraciones
específicas del tenant de forma centralizada, con caché para evitar queries repetidas.
"""

from typing import Optional, Dict, Tuple, Any
from datetime import datetime, timedelta
from config.constants import DEFAULT_CITY, DEFAULT_LOGO_URL
from shared.logging import get_logger

logger = get_logger(__name__)


# ============================================================================
# CACHÉ DE SETTINGS
# ============================================================================

_settings_cache: Dict[str, Tuple[dict, datetime]] = {}  # schema -> (settings, timestamp)
CACHE_TTL_SECONDS = 300  # 5 minutos


def get_tenant_settings(schema_name: str) -> dict:
    """
    Obtener settings del tenant con caché de 5 minutos.

    Args:
        schema_name: Schema del tenant

    Returns:
        dict con logo_url, isologo_url, city, annual_slogan
    """
    now = datetime.now()

    # Verificar caché
    if schema_name in _settings_cache:
        settings, cached_at = _settings_cache[schema_name]
        if now - cached_at < timedelta(seconds=CACHE_TTL_SECONDS):
            return settings

    # Query a BD (import local para evitar circular imports)
    from database import execute_query

    try:
        results = execute_query(
            "SELECT logo_url, isologo_url, city, annual_slogan FROM settings LIMIT 1",
            schema_name=schema_name
        )

        if results and len(results) > 0:
            result = results[0]
            settings = {
                "logo_url": result.get('logo_url'),
                "isologo_url": result.get('isologo_url'),
                "city": result.get('city') or DEFAULT_CITY,
                "annual_slogan": result.get('annual_slogan') or ""
            }
        else:
            settings = {
                "logo_url": None,
                "isologo_url": None,
                "city": DEFAULT_CITY,
                "annual_slogan": ""
            }

        # Guardar en caché
        _settings_cache[schema_name] = (settings, now)
        return settings

    except Exception as e:
        logger.error(f"Error obteniendo settings: {e}")
        return {
            "logo_url": None,
            "isologo_url": None,
            "city": DEFAULT_CITY,
            "annual_slogan": ""
        }


def get_logo_url(*, schema_name: str) -> str:
    """
    Obtener logo_url del tenant con fallback a DEFAULT_LOGO_URL.

    Args:
        schema_name: Schema del tenant

    Returns:
        str: URL del logo (BD primero, R2 fallback)
    """
    settings = get_tenant_settings(schema_name)
    return settings.get("logo_url") or DEFAULT_LOGO_URL


def invalidate_settings_cache(*, schema_name: Optional[str] = None):
    """
    Invalidar caché de settings.

    Llamar después de UPDATE a la tabla settings.

    Args:
        schema_name: Schema específico a invalidar, o None para invalidar todo
    """
    global _settings_cache
    if schema_name:
        _settings_cache.pop(schema_name, None)
    else:
        _settings_cache.clear()


# ============================================================================
# FUNCIÓN LEGACY (mantener compatibilidad)
# ============================================================================

def get_city_from_settings(cursor=None, *, schema_name: Optional[str] = None) -> str:
    """
    Obtiene el campo city desde la tabla settings del tenant.

    Args:
        cursor: Cursor de BD activo (opcional, para usar en transacción existente)
        schema_name: Nombre del schema del tenant (requerido si no hay cursor)

    Returns:
        str: Ciudad configurada o DEFAULT_CITY como fallback

    Examples:
        >>> # Con cursor existente (dentro de transacción)
        >>> city = get_city_from_settings(cursor=cursor)

        >>> # Sin cursor (crea conexión propia - USA CACHÉ)
        >>> city = get_city_from_settings(schema_name="100_test")
    """
    try:
        if cursor:
            # Usar cursor existente (dentro de transacción) - NO usar caché
            cursor.execute("SELECT city FROM settings LIMIT 1")
            result = cursor.fetchone()

            if result:
                city = result.get('city') if isinstance(result, dict) else result[0]
                if city:
                    return city

            return DEFAULT_CITY

        else:
            # Sin cursor - USAR CACHÉ
            settings = get_tenant_settings(schema_name)
            return settings.get("city", DEFAULT_CITY)

    except Exception as e:
        logger.error(f"Error obteniendo city: {e}, usando default: {DEFAULT_CITY}")
        return DEFAULT_CITY
