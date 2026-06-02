import os
import time
import asyncio
import logging
from collections import defaultdict
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger("rate_limit")

RATE_LIMIT = 60
# Multiplicador para auth por API Key / service: validate_rest_api_key ya hace
# rate-limit per-key (rate_limit_per_minute de BD), así que el límite por IP acá
# se relaja 10x para no estrangular integraciones legítimas (FASE 1 S8-001).
API_KEY_RATE_MULTIPLIER = 10
WINDOW_SECONDS = 60
EXCLUDED_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}
CLEANUP_INTERVAL = 300  # 5 min
# Tiempo mínimo entre intentos de reconexión a Redis tras un error (segundos).
# Evita martillar Redis en un loop si está caído.
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
        self._last_redis_error_at: float = 0.0  # timestamp del ultimo fallo Redis
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
            self._last_redis_error_at = 0.0
            logger.info("[RateLimit] Redis conectado")
        except Exception as e:
            self._redis = None
            logger.warning(f"[RateLimit] Redis no disponible ({e}), usando in-memory")

    def _maybe_reconnect_redis(self) -> None:
        """Intenta reconectar a Redis si pasó suficiente tiempo desde el último error."""
        if time.time() - self._last_redis_error_at >= _REDIS_RETRY_INTERVAL:
            logger.info("[RateLimit] Intentando reconectar a Redis...")
            self._try_redis()

    async def dispatch(self, request, call_next):
        if request.method == "OPTIONS" or request.url.path in EXCLUDED_PATHS:
            return await call_next(request)

        client_ip = _get_client_ip(request)

        # Límite efectivo: 10x si el request entró por API Key.
        # validate_rest_api_key ya aplica rate-limit per-key (rate_limit_per_minute de BD),
        # así que el límite por IP acá se relaja para no estrangular integraciones.
        # auth_source lo setea TenantMiddleware, que ejecuta ANTES que este middleware
        # (HostFilter → Tenant → RateLimit → CORS).
        # "service" incluido por si se agrega en Fase 2; hoy no es una ruta activa en main.py.
        limit = RATE_LIMIT
        if getattr(request.state, "auth_source", None) in ("api_key", "service"):
            limit = RATE_LIMIT * API_KEY_RATE_MULTIPLIER

        if self._mode == "redis" and self._redis:
            return await self._dispatch_redis(request, call_next, client_ip, limit)
        return await self._dispatch_memory(request, call_next, client_ip, limit)

    async def _dispatch_redis(self, request, call_next, client_ip: str, limit: int = RATE_LIMIT):
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
            # Fail-closed: NO bypassear el rate-limit cuando Redis falla.
            # Degradar a in-memory para mantener la protección activa.
            logger.warning(f"[RateLimit] Redis error ({e}), degradando a in-memory (fail-closed)")
            self._redis = None
            self._mode = "in-memory"
            self._last_redis_error_at = time.time()
            # Programar reconexión diferida sin bloquear la request actual.
            loop = asyncio.get_event_loop()
            loop.call_later(_REDIS_RETRY_INTERVAL, self._maybe_reconnect_redis)
            return await self._dispatch_memory(request, call_next, client_ip)

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

    async def _dispatch_memory(self, request, call_next, client_ip: str, limit: int = RATE_LIMIT):
        now = time.time()
        window_start = now - WINDOW_SECONDS

        # Filter old + always append current request FIRST (fixes first-request bug)
        timestamps = [t for t in self._requests[client_ip] if t > window_start]
        timestamps.append(now)
        self._requests[client_ip] = timestamps

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

        # Periodic cleanup
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
