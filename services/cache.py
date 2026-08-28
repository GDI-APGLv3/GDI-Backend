
import redis
import json
import os
import time
import inspect
import asyncio
from typing import Callable, Any, Optional
from functools import wraps
from shared.logging import get_logger

logger = get_logger(__name__)

redis_client: Optional[redis.Redis] = None

_REINIT_COOLDOWN_SECONDS = 30
_last_init_attempt = 0.0

def init_redis():
    global redis_client, _last_init_attempt

    _last_init_attempt = time.monotonic()
    redis_url = os.getenv("REDIS_URL")

    if not redis_url:
        logger.info("Redis no configurado - cache deshabilitado")
        return False

    try:
        redis_client = redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5
        )
        redis_client.ping()
        logger.info("Redis conectado correctamente")
        return True
    except Exception as e:
        logger.error(f"Error conectando a Redis: {e}")
        redis_client = None
        return False

def get_redis() -> Optional[redis.Redis]:
    if redis_client is None and (time.monotonic() - _last_init_attempt) >= _REINIT_COOLDOWN_SECONDS:
        init_redis()
    return redis_client

async def get_cached(
    cache_key: str,
    fetch_func: Callable,
    ttl: int = 300,
    force_refresh: bool = False,
    cache_if: Optional[Callable[[Any], bool]] = None
) -> Any:
    async def _call(func):
        result = func()
        if inspect.isawaitable(result):
            result = await result
        return result

    client = get_redis()
    if client is None:
        return await _call(fetch_func)

    try:
        if not force_refresh:
            cached = await asyncio.to_thread(client.get, cache_key)
            if cached:
                logger.debug(f"Cache hit: {cache_key}")
                return json.loads(cached)

        logger.debug(f"Cache miss: {cache_key}")
        result = await _call(fetch_func)

        if cache_if is None or cache_if(result):
            await asyncio.to_thread(
                client.setex,
                cache_key,
                ttl,
                json.dumps(result, default=str),
            )
        else:
            logger.debug(f"Cache skip (cache_if=False): {cache_key}")

        return result

    except Exception as e:
        logger.error(f"Cache error {cache_key}: {e}")
        return await _call(fetch_func)

async def invalidate_cache(cache_key: str) -> bool:
    client = get_redis()
    if client is None:
        return False

    try:
        deleted = await asyncio.to_thread(client.delete, cache_key)
        if deleted:
            logger.debug(f"Cache invalidated: {cache_key}")
        return deleted > 0
    except Exception as e:
        logger.error(f"Cache invalidate error {cache_key}: {e}")
        return False

def invalidate_pattern(pattern: str) -> int:
    client = get_redis()
    if client is None:
        return 0

    try:
        keys = client.keys(pattern)
        if keys:
            deleted = client.delete(*keys)
            logger.debug(f"Cache invalidated pattern {pattern}: {deleted} keys")
            return deleted
        return 0
    except Exception as e:
        logger.error(f"Cache invalidate pattern error {pattern}: {e}")
        return 0

def cache_decorator(cache_key_prefix: str, ttl: int = 300):
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            cache_key = f"{cache_key_prefix}:{args}:{kwargs}"
            return await get_cached(cache_key, lambda: func(*args, **kwargs), ttl)
        return wrapper
    return decorator

init_redis()
