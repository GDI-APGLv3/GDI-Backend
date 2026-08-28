import hashlib
import json
import os
import time
from typing import Optional

from shared.logging import get_logger
from services.cache import get_redis

logger = get_logger(__name__)

PUBLIC_AI_DAILY_QUOTA = int(os.getenv("PUBLIC_AI_DAILY_QUOTA", "2000"))
_EMBED_CACHE_TTL_SECONDS = 86400
_QUOTA_KEY_TTL_SECONDS = 90000


def _today_key() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def check_and_consume_quota() -> bool:
    client = get_redis()
    if client is None:
        return False
    try:
        key = f"pub:ai_quota:{_today_key()}"
        current = client.incr(key)
        if current == 1:
            client.expire(key, _QUOTA_KEY_TTL_SECONDS)
        return current <= PUBLIC_AI_DAILY_QUOTA
    except Exception as e:
        logger.warning(f"[PublicInfo] Chequeo de cupo IA fallo, degradando a lookup: {e}")
        return False


def _embed_cache_key(schema_name: str, query_normalized: str) -> str:
    digest = hashlib.sha1(query_normalized.encode("utf-8")).hexdigest()
    return f"pub:embed:{schema_name}:{digest}"


def get_cached_embedding(query_normalized: str, *, schema_name: str) -> Optional[list]:
    client = get_redis()
    if client is None:
        return None
    try:
        key = _embed_cache_key(schema_name, query_normalized)
        cached = client.get(key)
        return json.loads(cached) if cached else None
    except Exception as e:
        logger.warning(f"[PublicInfo] Lectura de cache de embeddings fallo: {e}")
        return None


def set_cached_embedding(query_normalized: str, embedding: list, *, schema_name: str) -> None:
    client = get_redis()
    if client is None:
        return
    try:
        key = _embed_cache_key(schema_name, query_normalized)
        client.setex(key, _EMBED_CACHE_TTL_SECONDS, json.dumps(embedding))
    except Exception as e:
        logger.warning(f"[PublicInfo] Escritura de cache de embeddings fallo: {e}")
