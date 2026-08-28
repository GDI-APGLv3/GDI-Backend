import time
from collections import defaultdict

from shared.logging import get_logger

logger = get_logger(__name__)


MAX_RATE_LIMIT_BY_KEY_TYPE = {
    "backup": 120,
    "api": 200_000,
    "public": 200_000,
    "tad": 200_000,
}
DEFAULT_MAX_RATE_LIMIT = 200_000

MAX_RATE_LIMIT_TENANT_CAN_SET = {
    "api": 600,
    "public": 600,
    "tad": 120,
    "backup": 120,
}


def cap_rate_limit(value, key_type: str, *, key_id: str = "") -> int:
    tope = MAX_RATE_LIMIT_BY_KEY_TYPE.get(key_type, DEFAULT_MAX_RATE_LIMIT)
    if value and value > tope:
        logger.warning(
            f"[RateLimit] key_type={key_type} key={key_id or '?'} pide "
            f"{value} req/min y el techo es {tope} (GDI-380): se aplica {tope}"
        )
        return tope
    return value


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: int = 1):
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded. Retry after {retry_after}s")


class InMemoryRateLimiter:
    def __init__(self):
        self._requests: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
        now = time.time()
        window_start = now - window_seconds

        timestamps = [t for t in self._requests[key] if t > window_start]
        timestamps.append(now)
        self._requests[key] = timestamps

        if len(timestamps) > limit:
            self._requests[key] = timestamps[:-1]
            retry_after = max(1, int(timestamps[0] + window_seconds - now))
            raise RateLimitExceeded(retry_after=retry_after)

        return True, max(limit - len(timestamps), 0)

    def cleanup(self):
        now = time.time()
        stale = [k for k, ts in self._requests.items() if not ts or ts[-1] < now - 120]
        for k in stale:
            del self._requests[k]


def get_client_ip(request) -> str:
    return (
        request.headers.get("fly-client-ip")
        or (request.headers.get("x-forwarded-for", "").split(",")[0].strip())
        or (request.client.host if request.client else "unknown")
    )


rate_limiter = InMemoryRateLimiter()
