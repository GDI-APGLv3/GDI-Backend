import os
import time
import asyncio
import logging
from collections import defaultdict
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger("rate_limit")

RATE_LIMIT = int(os.environ.get("RATE_LIMIT_PER_MIN", "60"))
RATE_LIMIT_PER_USER = int(os.environ.get("RATE_LIMIT_PER_USER_PER_MIN", "180"))
API_KEY_RATE_MULTIPLIER = 10
WINDOW_SECONDS = 60
EXCLUDED_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}
CLEANUP_INTERVAL = 300
_REDIS_RETRY_INTERVAL = 30


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
        self._last_redis_error_at: float = 0.0
        self._try_redis()

    def _try_redis(self):
        redis_url = os.environ.get("REDIS_URL")
        if not redis_url:
            logger.info("[RateLimit] Middleware iniciado - modo: in-memory")
            return
        try:
            import redis
            self._redis = redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_keepalive=True,
                health_check_interval=30,
            )
            self._redis.ping()
            self._mode = "redis"
            self._last_redis_error_at = 0.0
            logger.info("[RateLimit] Redis conectado")
        except Exception as e:
            self._redis = None
            logger.warning(f"[RateLimit] Redis no disponible ({e}), usando in-memory")

    def _maybe_reconnect_redis(self) -> None:
        if time.time() - self._last_redis_error_at >= _REDIS_RETRY_INTERVAL:
            logger.info("[RateLimit] Intentando reconectar a Redis...")
            import threading
            threading.Thread(target=self._try_redis, daemon=True).start()

    async def dispatch(self, request, call_next):
        if request.method == "OPTIONS" or request.url.path in EXCLUDED_PATHS:
            return await call_next(request)

        client_ip = _get_client_ip(request)

        auth_source = getattr(request.state, "auth_source", None)
        tenant_user_id = getattr(request.state, "tenant_user_id", None)

        limit = RATE_LIMIT
        identity = client_ip
        if auth_source in ("api_key", "service"):
            limit = RATE_LIMIT * API_KEY_RATE_MULTIPLIER
        elif auth_source == "jwt" and tenant_user_id:
            limit = RATE_LIMIT_PER_USER
            identity = f"user:{tenant_user_id}"

        if self._mode == "redis" and self._redis:
            return await self._dispatch_redis(request, call_next, identity, limit)
        return await self._dispatch_memory(request, call_next, identity, limit)

    def _incr_window(self, key: str):
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, WINDOW_SECONDS + 1)
        return pipe.execute()

    async def _dispatch_redis(self, request, call_next, identity: str, limit: int):
        now = int(time.time())
        window_key = now // WINDOW_SECONDS
        key = f"ratelimit:{identity}:{window_key}"
        reset_at = (window_key + 1) * WINDOW_SECONDS

        try:
            count, _ = await asyncio.to_thread(self._incr_window, key)
        except Exception as e:
            logger.warning(f"[RateLimit] Redis error ({e}), degradando a in-memory (fail-closed)")
            self._redis = None
            self._mode = "in-memory"
            self._last_redis_error_at = time.time()
            loop = asyncio.get_event_loop()
            loop.call_later(_REDIS_RETRY_INTERVAL, self._maybe_reconnect_redis)
            return await self._dispatch_memory(request, call_next, identity, limit)

        if count > limit:
            retry_after = reset_at - now
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded. Max {limit} requests per minute."},
                headers={
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_at),
                    "Retry-After": str(max(retry_after, 1)),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(limit - count, 0))
        response.headers["X-RateLimit-Reset"] = str(reset_at)
        return response

    async def _dispatch_memory(self, request, call_next, identity: str, limit: int):
        now = time.time()
        window_start = now - WINDOW_SECONDS

        timestamps = [t for t in self._requests[identity] if t > window_start]
        timestamps.append(now)
        self._requests[identity] = timestamps

        if len(timestamps) > limit:
            reset_at = int(timestamps[0] + WINDOW_SECONDS)
            retry_after = max(reset_at - int(now), 1)
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded. Max {limit} requests per minute."},
                headers={
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_at),
                    "Retry-After": str(retry_after),
                },
            )

        if now - self._last_cleanup > CLEANUP_INTERVAL:
            self._last_cleanup = now
            stale = [ip for ip, ts in self._requests.items() if not ts or ts[-1] <= window_start]
            for ip in stale:
                del self._requests[ip]

        response = await call_next(request)
        remaining = max(limit - len(timestamps), 0)
        reset_at = int(timestamps[0] + WINDOW_SECONDS)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_at)
        return response
