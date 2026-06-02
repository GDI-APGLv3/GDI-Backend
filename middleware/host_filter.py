"""
Host-based filter middleware.

Filtra requests segun el header `Host` para limitar la superficie de paths
expuestos en subdominios dedicados (ej. callbacks publicos de firma digital).

El backend principal (`<your-backend-app>.fly.dev`, `<your-backend-app>.fly.dev`,
etc.) sigue exponiendo todos los endpoints como hoy.

Subdominios listados en `ENLACE_HOSTS` solo responden a paths en
`ENLACE_ALLOWED_PATHS`; el resto devuelve 404 limpio.

Razon: el callback publico que AutoFirma postea (`/digital-signature/post`)
debe vivir en un dominio propio (`enlace.your-domain.com`) que el firewall
del municipio pueda whitelistear. No queremos que ese mismo subdominio
exponga `/users/`, `/documents/`, etc. — aunque la auth los proteja, reduce
superficie a scanners y facilita el whitelist.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp


# Subdominios dedicados al callback publico de firma digital.
# Cualquier request cuyo `Host:` coincida con uno de estos solo puede
# llegar a los paths en ENLACE_ALLOWED_PATHS.
ENLACE_HOSTS: frozenset[str] = frozenset({
    "enlace.your-domain.com",
    # Multi-cliente futuro (V2): habilitar cuando se decida naming
    # "enlace-cliente.your-domain.com",
})

# Paths permitidos cuando el Host es un ENLACE_HOST.
# `/health` queda permitido para que monitoring externo y validacion
# de cert SSL Let's Encrypt funcionen contra el subdominio.
ENLACE_ALLOWED_PATHS: frozenset[str] = frozenset({
    "/health",
    # Endpoints de firma digital (Fase 2):
    "/digital-signature/storage",   # callback publico de AutoFirma (sin JWT)
    "/digital-signature/cancel",    # cancelacion (con JWT, desde frontend)
})


class HostFilterMiddleware(BaseHTTPMiddleware):
    """
    Filtra requests con `Host:` en ENLACE_HOSTS para que solo respondan
    a paths en ENLACE_ALLOWED_PATHS. El resto devuelve 404.

    Requests con cualquier otro `Host:` pasan sin filtrar (backend completo).
    """

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
