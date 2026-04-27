"""
Gateway middleware: Correlation IDs + REST audit trail + RateLimitExceeded handler.
"""
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

from api_gateway.gateway_audit import log_rest_request
from api_gateway.rate_limiter import RateLimitExceeded


class GatewayMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method == "OPTIONS":
            return await call_next(request)

        # Correlation ID
        cid = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        request.state.correlation_id = cid

        start = time.time()

        try:
            response = await call_next(request)
        except RateLimitExceeded as e:
            # Catch RateLimitExceeded from auth_rest.py (REST API handlers)
            response = JSONResponse(
                {"error": f"Rate limit exceeded. Retry after {e.retry_after}s"},
                status_code=429,
                headers={"Retry-After": str(e.retry_after)},
            )

        duration_ms = int((time.time() - start) * 1000)

        # Add correlation ID to response
        response.headers["X-Correlation-ID"] = cid

        # Audit REST requests (skip health, root, mcp, well-known)
        path = request.url.path
        if path not in ("/health", "/", "/mcp") and not path.startswith("/.well-known"):
            user_id = getattr(request.state, "user_id", None)
            schema = getattr(request.state, "schema_name", None)

            log_rest_request(
                cid=cid,
                user_id=user_id,
                schema=schema,
                method=request.method,
                path=path,
                http_status=response.status_code,
                duration_ms=duration_ms,
            )

        return response
