import os
import time
import asyncio
import logging
from collections import defaultdict
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger("rate_limit")

RATE_LIMIT = 60
WINDOW_SECONDS = 60
EXCLUDED_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}
CLEANUP_INTERVAL = 300  # 5 min


def _get_client_ip(request) -> str:
    return (
        request.headers.get("fly-client-ip")
        or (request.headers.get("x-forwarded-for", "").split(",")[0].strip())
        or (request.client.host if request.client else "unknown")
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self._redis = None
        self._mode = "in-memory"
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._last_cleanup = time.time()
        self._try_redis()

    def _try_redis(self):
        redis_url = os.environ.get("REDIS_URL")
        if not redis_url:
            logger.info("[RateLimit] Middleware iniciado - modo: in-memory")
            return
        try:
            import redis
            self._redis = redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=3)
            self._redis.ping()
            self._mode = "redis"
            logger.info("[RateLimit] Redis conectado")
        except Exception as e:
            self._redis = None
            logger.warning(f"[RateLimit] Redis no disponible ({e}), usando in-memory")

    async def dispatch(self, request, call_next):
        if request.method == "OPTIONS" or request.url.path in EXCLUDED_PATHS:
            return await call_next(request)

        client_ip = _get_client_ip(request)

        if self._mode == "redis" and self._redis:
            return await self._dispatch_redis(request, call_next, client_ip)
        return await self._dispatch_memory(request, call_next, client_ip)

    async def _dispatch_redis(self, request, call_next, client_ip: str):
        now = int(time.time())
        window_key = now // WINDOW_SECONDS
        key = f"ratelimit:{client_ip}:{window_key}"
        reset_at = (window_key + 1) * WINDOW_SECONDS

        try:
            pipe = self._redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, WINDOW_SECONDS + 1)
            count, _ = pipe.execute()
        except Exception as e:
            logger.warning(f"[RateLimit] Redis error ({e}), fail-open")
            return await call_next(request)

        if count > RATE_LIMIT:
            retry_after = reset_at - now
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded. Max {RATE_LIMIT} requests per minute."},
                headers={
                    "X-RateLimit-Limit": str(RATE_LIMIT),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_at),
                    "Retry-After": str(max(retry_after, 1)),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT)
        response.headers["X-RateLimit-Remaining"] = str(max(RATE_LIMIT - count, 0))
        response.headers["X-RateLimit-Reset"] = str(reset_at)
        return response

    async def _dispatch_memory(self, request, call_next, client_ip: str):
        now = time.time()
        window_start = now - WINDOW_SECONDS

        # Filter old + always append current request FIRST (fixes first-request bug)
        timestamps = [t for t in self._requests[client_ip] if t > window_start]
        timestamps.append(now)
        self._requests[client_ip] = timestamps

        if len(timestamps) > RATE_LIMIT:
            reset_at = int(timestamps[0] + WINDOW_SECONDS)
            retry_after = max(reset_at - int(now), 1)
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded. Max {RATE_LIMIT} requests per minute."},
                headers={
                    "X-RateLimit-Limit": str(RATE_LIMIT),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_at),
                    "Retry-After": str(retry_after),
                },
            )

        # Periodic cleanup
        if now - self._last_cleanup > CLEANUP_INTERVAL:
            self._last_cleanup = now
            stale = [ip for ip, ts in self._requests.items() if not ts or ts[-1] <= window_start]
            for ip in stale:
                del self._requests[ip]

        response = await call_next(request)
        remaining = max(RATE_LIMIT - len(timestamps), 0)
        reset_at = int(timestamps[0] + WINDOW_SECONDS)
        response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_at)
        return response
