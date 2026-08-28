import time

from shared.logging import get_logger
from services.cache import get_redis
from api_gateway.rate_limiter import RateLimitExceeded

logger = get_logger(__name__)


def get_public_client_ip(request) -> str:
    return request.headers.get("fly-client-ip") or (request.client.host if request.client else "unknown")


def check_public_rate_limit(ip: str, limit: int, window_seconds: int = 60) -> None:
    client = get_redis()
    if client is None:
        logger.warning("[PublicInfo] Redis no disponible para rate limit publico, fail-closed (bloqueando)")
        raise RateLimitExceeded(retry_after=window_seconds)

    try:
        bucket = int(time.time() // window_seconds)
        key = f"pub:ratelimit:{ip}:{bucket}"
        current = client.incr(key)
        if current == 1:
            client.expire(key, window_seconds + 5)
        if current > limit:
            raise RateLimitExceeded(retry_after=window_seconds)
    except RateLimitExceeded:
        raise
    except Exception as e:
        logger.warning(f"[PublicInfo] Rate limit check fallo (Redis), fail-closed: {e}")
        raise RateLimitExceeded(retry_after=window_seconds)
