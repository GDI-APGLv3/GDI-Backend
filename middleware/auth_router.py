"""
Auth Router (FASE 1 S8-001): resolución de método de autenticación pluggable.

FASE 1 (scope acotado): solo el camino X-API-Key + X-User-ID normal.
Las ramas backup (sync/*, sin X-User-ID) y service (AGENT_GATEWAY_SECRET)
se agregarán en Fase 2 al migrar los endpoints sync/* y los service-allowlisted
a main.py. Hoy esos paths viven en api_gateway/http_server.py y NO son rutas
de este app, por lo que incluirlos aquí sería código muerto y un riesgo latente.

Detecta CÓMO viene autenticado el request y resuelve el tenant SIN tocar el
flujo JWT existente. Solo AGREGA la puerta API Key normal.

Estados de auth soportados en Fase 1:
- "api_key"  → X-API-Key + X-User-ID validados contra public.api_keys
- JWT        → hay Authorization: Bearer ... → se delega al flujo JWT viejo (is_jwt=True)
- None       → sin credenciales → se deja correr el flujo viejo (terminará en 401)

POLÍTICA DOBLE CREDENCIAL (explícita):
Si el request trae X-API-Key, se valida por API Key SIEMPRE, aunque también
venga Authorization: Bearer. Una X-API-Key inválida → 401, NO hay fallback a JWT.
Política más simple y segura: una sola vía de auth por request.
"""

from dataclasses import dataclass
from typing import Optional

from shared.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ResolvedAuth:
    """Resultado de resolve_auth.

    Si is_jwt=True: el caller debe seguir el flujo JWT existente sin cambios.
    Si auth_source == "api_key": el tenant ya quedó resuelto y el caller
    debe poblar request.state.* y continuar.
    """
    auth_source: str            # "api_key" | "jwt"
    schema_name: Optional[str] = None
    tenant_user_id: Optional[str] = None
    tenant_email: Optional[str] = None
    is_jwt: bool = False


async def resolve_auth(request) -> Optional[ResolvedAuth]:
    """
    Detecta el método de auth del request y resuelve el tenant cuando es API Key.

    POLÍTICA DOBLE CREDENCIAL: si hay X-API-Key, se valida por API Key SIEMPRE.
    Una X-API-Key inválida → ValueError (→ 401 en el caller). No hay fallback a JWT.

    Returns:
        - ResolvedAuth con is_jwt=True      → seguir flujo JWT (no se resolvió acá)
        - ResolvedAuth auth_source="api_key" → tenant resuelto, poblar request.state
        - None                               → sin credenciales; seguir flujo viejo (→ 401)

    Raises:
        ValueError: si la API Key es inválida, inactiva, expirada, o user_id no
                    autorizado. El caller debe traducir a HTTP 401.
    """
    api_key = request.headers.get("X-API-Key")
    auth_header = request.headers.get("Authorization", "")

    # --- 1. API KEY normal: X-API-Key + X-User-ID ---
    # Se evalúa PRIMERO, antes de Bearer. Si hay X-API-Key se usa SIEMPRE este camino.
    if api_key:
        from api_gateway.auth_rest import validate_rest_api_key
        user_id = request.headers.get("X-User-ID")
        ctx = await validate_rest_api_key(api_key, user_id=user_id, request=request)
        return ResolvedAuth(
            auth_source="api_key",
            schema_name=ctx.schema_name,
            tenant_user_id=ctx.user_id,
            tenant_email=None,  # se completa en get_current_user vía BD
        )

    # --- 2. JWT: hay Bearer → delegar al flujo JWT viejo ---
    if auth_header.startswith("Bearer "):
        return ResolvedAuth(auth_source="jwt", is_jwt=True)

    # --- 3. Sin credenciales conocidas → seguir flujo viejo (terminará en 401) ---
    return None
