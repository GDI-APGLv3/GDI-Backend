
from __future__ import annotations

import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


ENLACE_HOSTS: frozenset[str] = frozenset(
    h.strip().lower()
    for h in os.getenv("ENLACE_HOSTS", "").split(",")
    if h.strip()
)

ENLACE_ALLOWED_PATHS: frozenset[str] = frozenset({
    "/health",
    "/digital-signature/storage",
    "/digital-signature/cancel",
})


class HostFilterMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0].lower().strip()

        if host in ENLACE_HOSTS:
            path = request.url.path
            allowed = (
                path in ENLACE_ALLOWED_PATHS
                or path.startswith("/digital-signature/poll/")
            )
            if not allowed:
                return JSONResponse(
                    status_code=404,
                    content={"detail": "Not Found"},
                )

        return await call_next(request)
