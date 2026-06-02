"""
Servicio de caching con Redis para mejorar performance.

Uso:
    from services.cache import get_cached, invalidate_cache

    # Cachear resultado de función
    result = get_cached("sectors:all", SectorService.get_all_sectors, ttl=300)

    # Invalidar cache
    invalidate_cache("sectors:all")
"""

import redis
import json
import os
import asyncio
import inspect
from typing import Callable, Any, Optional
from functools import wraps
from shared.logging import get_logger

logger = get_logger(__name__)

# Cliente Redis (opcional - funciona sin Redis)
redis_client: Optional[redis.Redis] = None

def init_redis():
    """Inicializar conexión a Redis (opcional)"""
    global redis_client

    redis_url = os.getenv("REDIS_URL")

    if not redis_url:
        logger.info("Redis no configurado - cache deshabilitado")
        return False

    try:
        redis_client = redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2
        )
        # Test connection
        redis_client.ping()
        logger.info("Redis conectado correctamente")
        return True
    except Exception as e:
        logger.error(f"Error conectando a Redis: {e}")
        redis_client = None
        return False

async def get_cached(
    cache_key: str,
    fetch_func: Callable,
    ttl: int = 300,
    force_refresh: bool = False
) -> Any:
    """
    Obtener dato desde cache o ejecutar función si no existe.
    Soporta fetch_func sync y async (coroutine).

    Args:
        cache_key: Clave única para el cache (ej: "sectors:all")
        fetch_func: Función a ejecutar si no hay cache (sync o async)
        ttl: Tiempo de vida en segundos (default: 5 minutos)
        force_refresh: Forzar actualización ignorando cache

    Returns:
        Resultado de la función (desde cache o recién calculado)
    """
    async def _call(func):
        result = func()
        if inspect.isawaitable(result):
            result = await result
        return result

    # Si Redis no está disponible, ejecutar función directamente
    if redis_client is None:
        return await _call(fetch_func)

    try:
        # Si force_refresh, ignorar cache
        if not force_refresh:
            cached = redis_client.get(cache_key)
            if cached:
                logger.debug(f"Cache hit: {cache_key}")
                return json.loads(cached)

        # Cache miss o refresh forzado - ejecutar función
        logger.debug(f"Cache miss: {cache_key}")
        result = await _call(fetch_func)

        # Guardar en cache
        redis_client.setex(
            cache_key,
            ttl,
            json.dumps(result, default=str)  # default=str para UUIDs
        )

        return result

    except Exception as e:
        # Si falla Redis, ejecutar función directamente
        logger.error(f"Cache error {cache_key}: {e}")
        return await _call(fetch_func)

def invalidate_cache(cache_key: str) -> bool:
    """
    Invalidar (eliminar) entrada del cache.

    Args:
        cache_key: Clave del cache a invalidar

    Returns:
        True si se eliminó, False si no existía o hubo error
    """
    if redis_client is None:
        return False

    try:
        deleted = redis_client.delete(cache_key)
        if deleted:
            logger.debug(f"Cache invalidated: {cache_key}")
        return deleted > 0
    except Exception as e:
        logger.error(f"Cache invalidate error {cache_key}: {e}")
        return False

def invalidate_pattern(pattern: str) -> int:
    """
    Invalidar todas las claves que coincidan con un patrón.

    Args:
        pattern: Patrón de búsqueda (ej: "sectors:*")

    Returns:
        Número de claves eliminadas
    """
    if redis_client is None:
        return 0

    try:
        keys = redis_client.keys(pattern)
        if keys:
            deleted = redis_client.delete(*keys)
            logger.debug(f"Cache invalidated pattern {pattern}: {deleted} keys")
            return deleted
        return 0
    except Exception as e:
        logger.error(f"Cache invalidate pattern error {pattern}: {e}")
        return 0

def cache_decorator(cache_key_prefix: str, ttl: int = 300):
    """
    Decorador para cachear resultados de funciones (sync o async).

    Uso:
        @cache_decorator("users:list", ttl=60)
        async def get_all_users():
            return await fetch_all("SELECT * FROM users", schema_name="public")
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Construir cache key con argumentos
            cache_key = f"{cache_key_prefix}:{args}:{kwargs}"
            return await get_cached(cache_key, lambda: func(*args, **kwargs), ttl)
        return wrapper
    return decorator

# Inicializar Redis al importar el módulo
init_redis()
