import time
from typing import Optional

from database import fetch_one
from shared.logging import get_logger

logger = get_logger(__name__)

_BUCKET_CACHE_TTL_SECONDS = 120

_bucket_cache: dict[str, tuple[Optional[str], float]] = {}


async def get_bucket_publico(*, schema_name: str) -> Optional[str]:
    now = time.time()
    cached = _bucket_cache.get(schema_name)
    if cached and (now - cached[1]) < _BUCKET_CACHE_TTL_SECONDS:
        return cached[0]

    row = await fetch_one(
        "SELECT bucket_publico FROM settings LIMIT 1",
        schema_name=schema_name,
    )
    bucket = row["bucket_publico"] if row else None
    _bucket_cache[schema_name] = (bucket, now)
    return bucket
