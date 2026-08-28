import time

from shared.logging import get_logger
from services.cache import get_redis

log = get_logger(__name__)


class IpRateLimitExceeded(Exception):

    def __init__(self, retry_after: int = 60):
        self.retry_after = retry_after
        super().__init__(f"rate_limit_exceeded retry_after={retry_after}s")


def get_client_ip(request) -> str:
    return (
        request.headers.get("fly-client-ip")
        or (request.client.host if request.client else "unknown")
    )


def check_ip_rate_limit(
    ip: str, *, bucket_name: str, limit: int, window_seconds: int = 60
) -> None:
    client = get_redis()
    if client is None:
        log.warning("[ip_rate_limit] Redis no disponible — fail-closed (%s)", bucket_name)
        raise IpRateLimitExceeded(retry_after=window_seconds)

    try:
        ventana = int(time.time() // window_seconds)
        key = f"iprl:{bucket_name}:{ip}:{ventana}"
        actual = client.incr(key)
        if actual == 1:
            client.expire(key, window_seconds + 5)
        if actual > limit:
            raise IpRateLimitExceeded(retry_after=window_seconds)
    except IpRateLimitExceeded:
        raise
    except Exception as e:
        log.warning("[ip_rate_limit] chequeo falló (%s), fail-closed: %s", bucket_name, e)
        raise IpRateLimitExceeded(retry_after=window_seconds)
