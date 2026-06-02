"""
Autenticación MCP con Auth0 JWT.

Valida JWT de Auth0, extrae email del token, busca usuario en la BD
y retorna MCPContext con user_id automáticamente.

Soporta tokens con audience del MCP Server (para ChatGPT, Claude Code)
además del audience principal del Backend.

Soporta Multi-Tenant: usuarios con acceso a múltiples municipalidades
pueden especificar tenant_id para seleccionar en cuál trabajar.
"""
import os
import sys
import logging
import hashlib
import time
from typing import Tuple, Optional, Dict, Any, List

# Agregar path del backend para imports
backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_root)

from api_gateway.context import MCPContext

logger = logging.getLogger(__name__)


class MultiTenantSelectionRequired(Exception):
    """
    Excepción para señalizar que el usuario tiene acceso a múltiples tenants
    y debe especificar tenant_id para continuar.
    """
    def __init__(self, tenants: List[Dict[str, Any]]):
        self.tenants = tenants
        super().__init__(f"Usuario tiene acceso a {len(tenants)} municipalidades")


def _get_mcp_valid_audiences() -> list:
    """
    Retorna la lista de audiences válidos para tokens MCP.

    El audience primario es MCP_RESOURCE_URI (la URL pública del gateway).
    Debe coincidir con el Custom API configurado en Auth0 para cada ambiente:
      - <your-gateway-app>     → MCP_RESOURCE_URI=https://<your-gateway-app>.fly.dev
      - <your-gateway-app>     → MCP_RESOURCE_URI=https://<your-gateway-app>.fly.dev
      - <your-gateway-app>   → MCP_RESOURCE_URI=https://<your-gateway-app>.fly.dev
      - demo-gateway        → MCP_RESOURCE_URI=https://gateway.your-domain.com

    NOTA S1-008: AUTH0_AUDIENCE (Management API / backend audience,
    https://<your-tenant>.auth0.com/api/v2/) NO es un audience válido para el MCP.
    Solo se aceptan tokens emitidos contra MCP_RESOURCE_URI (Custom API del gateway).
    """
    mcp_resource_uri = os.getenv("MCP_RESOURCE_URI", "")
    audiences = []

    # Audience primario del MCP: debe estar configurado en cada app Fly
    if mcp_resource_uri:
        audiences.append(mcp_resource_uri)

    if not audiences:
        # Fallback seguro solo para desarrollo local
        logger.warning(
            "[MCP Auth0] MCP_RESOURCE_URI no configurado. "
            "En producción setear MCP_RESOURCE_URI al URL público del gateway."
        )
        audiences.append("http://localhost:8005")

    return audiences


def verify_mcp_token(token: str) -> Dict[str, Any]:
    """
    Verifica JWT de Auth0 contra audiences configurados por env var.

    El audience esperado se lee de MCP_RESOURCE_URI (variable de entorno).
    NO hay fallback opaco: si el JWT es inválido o el audience no coincide,
    se lanza ValueError y el caller debe retornar 401.

    Args:
        token: JWT a verificar

    Returns:
        Payload del token decodificado

    Raises:
        ValueError: Si el token es inválido, expirado, o el audience no coincide
    """
    import jwt as pyjwt
    from jwt.exceptions import PyJWTError, ExpiredSignatureError, InvalidAudienceError, InvalidIssuerError
    from auth import get_rsa_key
    from database import AUTH0_DOMAIN, AUTH0_ALGORITHMS

    valid_audiences = _get_mcp_valid_audiences()

    # Obtener la clave RSA
    rsa_key = get_rsa_key(token)

    if not rsa_key:
        raise ValueError("No se pudo encontrar la clave para validar el token")

    # Intentar verificar con cada audience configurado
    last_error = None
    for audience in valid_audiences:
        try:
            payload = pyjwt.decode(
                token,
                rsa_key,
                algorithms=AUTH0_ALGORITHMS,
                audience=audience,
                issuer=f"https://{AUTH0_DOMAIN}/"
            )
            logger.info(f"[MCP Auth0] Token válido con audience: {audience}")
            return payload
        except (InvalidAudienceError, InvalidIssuerError) as e:
            # Audience no coincide, probar siguiente
            last_error = e
            continue
        except ExpiredSignatureError:
            raise ValueError("Token expirado")
        except PyJWTError as e:
            raise ValueError(f"Token inválido: {str(e)}")

    # Ningún audience coincidió — loguear para diagnóstico y fallar con 401
    try:
        unverified = pyjwt.decode(token, options={"verify_signature": False}, algorithms=AUTH0_ALGORITHMS)
        token_aud = unverified.get("aud")
        logger.error(
            f"[MCP Auth0] Token rechazado: audience '{token_aud}' "
            f"no coincide con ninguno de los configurados: {valid_audiences}. "
            f"Verificar que el cliente pide token con audience=MCP_RESOURCE_URI "
            f"y que Auth0 tiene la Custom API configurada."
        )
    except Exception:
        pass

    raise ValueError(f"Audience del token no válido para este gateway. Esperado uno de: {valid_audiences}")


def _resolve_tenant(tenants: List[Dict[str, Any]], tenant_id: str = None) -> Tuple[MCPContext, str]:
    """
    Dada una lista de tenants, selecciona uno o lanza MultiTenantSelectionRequired.
    Retorna (MCPContext, user_id).
    """
    if not tenants:
        raise ValueError("Usuario no encontrado en ninguna municipalidad")

    if tenant_id:
        selected = next((t for t in tenants if str(t["municipality_id"]) == str(tenant_id)), None)
        if not selected:
            tenant_names = [t.get("municipality_name", t["schema_name"]) for t in tenants]
            raise ValueError(f"No tienes acceso al tenant {tenant_id}. Tenants disponibles: {tenant_names}")
        logger.info(f"[MCP Auth0] Tenant seleccionado: {selected['schema_name']}")
    elif len(tenants) == 1:
        selected = tenants[0]
        logger.info(f"[MCP Auth0] Usuario con un solo tenant: {selected['schema_name']}")
    else:
        logger.info(f"[MCP Auth0] Usuario con {len(tenants)} tenants, requiere selección")
        raise MultiTenantSelectionRequired(tenants)

    ctx = MCPContext(
        api_key="auth0-jwt",
        municipality_id=str(selected["municipality_id"]),
        schema_name=selected["schema_name"],
        auth_source="mcp_oauth"
    )
    return ctx, str(selected["user_id"])


async def validate_mcp_jwt(authorization_header: str, tenant_id: str = None) -> Tuple[MCPContext, str]:
    """
    Valida JWT de Auth0 y retorna contexto MCP con user_id.

    El JWT debe estar firmado por Auth0 y contener un audience válido
    configurado en MCP_RESOURCE_URI. No hay fallback opaco: si el JWT
    falla por cualquier razón (audience incorrecto, expirado, firma inválida)
    se lanza ValueError y el endpoint retorna 401.

    Soporta Multi-Tenant: si el usuario tiene acceso a múltiples municipalidades,
    debe especificar tenant_id para seleccionar en cuál trabajar.

    Args:
        authorization_header: "Bearer <jwt_token>"
        tenant_id: UUID de la municipalidad (requerido si usuario tiene múltiples tenants)

    Returns:
        Tuple[MCPContext, user_id] donde user_id es el UUID del usuario

    Raises:
        ValueError: Si token inválido, audience incorrecto, o usuario no encontrado
        MultiTenantSelectionRequired: Si hay múltiples tenants y no se especificó tenant_id
    """
    if not authorization_header:
        raise ValueError("Authorization header requerido")

    if not authorization_header.startswith("Bearer "):
        raise ValueError("Formato inválido. Use: Bearer <token>")

    token = authorization_header[7:]  # Quitar "Bearer "

    # Validar JWT con audience estricto — sin fallback opaco
    payload = verify_mcp_token(token)

    email = payload.get("email")
    auth_id = payload.get("sub")

    if not auth_id:
        raise ValueError("JWT no contiene sub (auth_id)")

    # JWT válido pero sin email claim → consultar /userinfo con el JWT verificado
    if not email:
        logger.info(f"[MCP Auth0] JWT sin email claim, consultando /userinfo...")
        email, _ = await get_email_from_userinfo(token)
        if not email:
            # Último recurso: buscar por auth_id (sub del JWT verificado)
            logger.info(f"[MCP Auth0] /userinfo sin email, buscando por auth_id: {auth_id}")
            tenants = await find_user_by_auth_id(auth_id)
            if tenants:
                ctx, user_id = _resolve_tenant(tenants, tenant_id)
                logger.info(f"[MCP Auth0] Usuario autenticado por auth_id: {auth_id}, user_id: {user_id}")
                return ctx, user_id
            raise ValueError(f"Usuario con auth_id {auth_id} no encontrado en ninguna municipalidad")

    logger.info(f"[MCP Auth0] JWT válido para: {email}")

    # Buscar usuario en TODOS los tenants por email
    tenants = await find_user_all_tenants(email)
    ctx, user_id = _resolve_tenant(tenants, tenant_id)
    logger.info(f"[MCP Auth0] Usuario autenticado: user_id={user_id}, schema={ctx.schema_name}")
    return ctx, user_id


async def extract_email_from_token(token: str) -> Optional[str]:
    """
    Extrae email de un JWT validado de Auth0.

    El JWT debe ser válido (firma, audience, issuer). No hay fallback opaco:
    si el JWT es inválido retorna None y el caller debe rechazar con 401.

    Args:
        token: Access token JWT

    Returns:
        Email del usuario si el JWT es válido, None en caso contrario
    """
    try:
        payload = verify_mcp_token(token)
    except ValueError as e:
        logger.warning(f"[MCP Auth0] extract_email_from_token: JWT inválido: {e}")
        return None

    email = payload.get("email")
    auth_id = payload.get("sub")

    if not email and not auth_id:
        return None

    # JWT válido pero sin email claim → consultar /userinfo con el JWT verificado
    if not email:
        email, fetched_auth_id = await get_email_from_userinfo(token)
        if not auth_id:
            auth_id = fetched_auth_id

    # Sin email pero con auth_id (del JWT verificado) → buscar en BD
    if not email and auth_id:
        tenants = await find_user_by_auth_id(auth_id)
        if tenants:
            email = tenants[0].get("email")

    return email


# =====================================================================
# Auth0 /userinfo cache (in-memory, TTL 5 min)
# =====================================================================
_USERINFO_CACHE: Dict[str, Dict[str, Any]] = {}
_USERINFO_CACHE_TTL = 300  # 5 minutes
_USERINFO_CACHE_MAX = 100


def _cache_key(token: str) -> str:
    """SHA-256 hash of token as cache key."""
    return hashlib.sha256(token.encode()).hexdigest()


def _get_cached_userinfo(token: str) -> Optional[Tuple[Optional[str], Optional[str]]]:
    """Return cached (email, sub) if valid, else None."""
    key = _cache_key(token)
    entry = _USERINFO_CACHE.get(key)
    if entry and time.time() - entry["ts"] < _USERINFO_CACHE_TTL:
        return entry["email"], entry["sub"]
    if entry:
        _USERINFO_CACHE.pop(key, None)
    return None


def _set_cached_userinfo(token: str, email: Optional[str], sub: Optional[str]) -> None:
    """Cache a successful userinfo result. Lazy cleanup when over max."""
    if len(_USERINFO_CACHE) >= _USERINFO_CACHE_MAX:
        now = time.time()
        expired = [k for k, v in _USERINFO_CACHE.items() if now - v["ts"] >= _USERINFO_CACHE_TTL]
        for k in expired:
            del _USERINFO_CACHE[k]
    _USERINFO_CACHE[_cache_key(token)] = {"email": email, "sub": sub, "ts": time.time()}


async def get_email_from_userinfo(token: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Obtiene el email y sub del usuario desde Auth0 /userinfo endpoint.

    Utiliza cache en memoria (TTL 5 min) para evitar rate limits de Auth0.

    Útil cuando el access token no incluye el email claim,
    o cuando es un opaque token y necesitamos el sub (auth_id).

    Args:
        token: Access token de Auth0

    Returns:
        Tuple (email, sub) del usuario. Ambos pueden ser None.
    """
    # Check cache first
    cached = _get_cached_userinfo(token)
    if cached is not None:
        logger.info("[MCP Auth0] /userinfo resultado obtenido de cache")
        return cached

    import httpx
    from database import AUTH0_DOMAIN

    userinfo_url = f"https://{AUTH0_DOMAIN}/userinfo"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                userinfo_url,
                headers={"Authorization": f"Bearer {token}"}
            )
            response.raise_for_status()
            data = response.json()
            email = data.get("email")
            sub = data.get("sub")
            if email:
                logger.info(f"[MCP Auth0] Email obtenido de /userinfo para sub: {sub}")
            # Cache successful result (do NOT cache errors)
            _set_cached_userinfo(token, email, sub)
            return email, sub
    except Exception as e:
        logger.warning(f"[MCP Auth0] Error obteniendo /userinfo: {e}")
        return None, None


async def find_user_by_auth_id(auth_id: str) -> List[Dict[str, Any]]:
    """
    Busca usuario por auth_id (sub de Auth0) en TODAS las municipalidades.
    Retorna lista de tenants donde existe (puede ser 0, 1 o N).

    Args:
        auth_id: El claim 'sub' del token JWT (ej: "auth0|123456")

    Returns:
        Lista de dicts con user_id, municipality_id, municipality_name, schema_name
    """
    from database import fetch_all, fetch_one

    try:
        municipalities = await fetch_all(
            "SELECT id, schema_name, name FROM public.municipalities WHERE is_active = true",
            schema_name="public"
        )
    except Exception as e:
        logger.error(f"[MCP Auth0] Error obteniendo municipalidades: {e}")
        return []

    if not municipalities:
        return []

    results = []
    for muni in municipalities:
        try:
            user = await fetch_one(
                "SELECT id as user_id, email FROM users WHERE auth_id = $1 AND estado = 1",
                auth_id,
                schema_name=muni['schema_name']
            )
            if user:
                logger.info(f"[MCP Auth0] Usuario con auth_id {auth_id} encontrado en {muni['schema_name']}")
                results.append({
                    "user_id": user["user_id"],
                    "email": user.get("email"),
                    "municipality_id": muni["id"],
                    "municipality_name": muni.get("name", muni["schema_name"]),
                    "schema_name": muni["schema_name"]
                })
        except Exception as e:
            logger.warning(f"[MCP Auth0] Error buscando auth_id en {muni['schema_name']}: {e}")
            continue

    return results


async def find_user_across_schemas(email: str) -> Optional[Dict[str, Any]]:
    """
    Busca usuario por email en todas las municipalidades activas.

    Recorre todos los schemas activos buscando un usuario con el email dado.
    Retorna el primero encontrado (un usuario puede estar en múltiples municipalidades).

    Args:
        email: Email del usuario a buscar

    Returns:
        Dict con user_id, email, municipality_id, schema_name si encontrado
        None si no existe en ninguna municipalidad
    """
    from database import fetch_all, fetch_one

    # Obtener todas las municipalidades activas
    try:
        municipalities = await fetch_all(
            "SELECT id, schema_name FROM public.municipalities WHERE is_active = true",
            schema_name="public"
        )
    except Exception as e:
        logger.error(f"[MCP Auth0] Error obteniendo municipalidades: {e}")
        raise ValueError("Error consultando municipalidades")

    if not municipalities:
        logger.warning("[MCP Auth0] No hay municipalidades activas")
        return None

    # Buscar en cada municipalidad
    for muni in municipalities:
        try:
            # Buscar usuario activo por email
            user = await fetch_one(
                "SELECT id as user_id, email FROM users WHERE email = $1 AND estado = 1",
                email,
                schema_name=muni['schema_name']
            )

            if user:
                logger.info(f"[MCP Auth0] Usuario user_id={user['user_id']} encontrado en schema {muni['schema_name']}")
                return {
                    "user_id": user["user_id"],
                    "email": user["email"],
                    "municipality_id": muni["id"],
                    "schema_name": muni["schema_name"]
                }

        except Exception as e:
            # Continuar con siguiente municipalidad si hay error
            logger.warning(f"[MCP Auth0] Error buscando en schema {muni['schema_name']}: {e}")
            continue

    logger.warning(f"[MCP Auth0] Usuario no encontrado en ninguna municipalidad")
    return None


async def find_user_in_schema(email: str, schema_name: str) -> Optional[Dict[str, Any]]:
    """
    Busca usuario por email en un schema específico.

    Args:
        email: Email del usuario
        schema_name: Schema donde buscar

    Returns:
        Dict con user_id, email si encontrado, None si no
    """
    from database import fetch_one

    try:
        user = await fetch_one(
            "SELECT id as user_id, email, full_name FROM users WHERE email = $1 AND estado = 1",
            email,
            schema_name=schema_name
        )
        return dict(user) if user else None
    except Exception as e:
        logger.error(f"[MCP Auth0] Error buscando usuario en {schema_name}: {e}")
        return None


async def find_user_all_tenants(email: str) -> List[Dict[str, Any]]:
    """
    Busca usuario por email en TODAS las municipalidades activas.

    A diferencia de find_user_across_schemas que retorna el primero encontrado,
    esta función retorna TODOS los tenants donde el usuario tiene cuenta.

    Args:
        email: Email del usuario a buscar

    Returns:
        Lista de dicts con user_id, municipality_id, municipality_name, schema_name
        para cada tenant donde existe el usuario.
        Lista vacía si no existe en ninguna municipalidad.
    """
    from database import fetch_all, fetch_one

    # Obtener todas las municipalidades activas
    try:
        municipalities = await fetch_all(
            "SELECT id, schema_name, name FROM public.municipalities WHERE is_active = true",
            schema_name="public"
        )
    except Exception as e:
        logger.error(f"[MCP Auth0] Error obteniendo municipalidades: {e}")
        return []

    if not municipalities:
        logger.warning("[MCP Auth0] No hay municipalidades activas")
        return []

    results = []

    # Buscar en cada municipalidad
    for muni in municipalities:
        try:
            # LOWER() para búsqueda case-insensitive
            user = await fetch_one(
                "SELECT id as user_id, email FROM users WHERE LOWER(email) = LOWER($1) AND estado = 1",
                email,
                schema_name=muni['schema_name']
            )

            if user:
                results.append({
                    "user_id": user["user_id"],
                    "municipality_id": muni["id"],
                    "municipality_name": muni.get("name", muni["schema_name"]),
                    "schema_name": muni["schema_name"]
                })
                logger.info(f"[MCP Auth0] Usuario user_id={user['user_id']} encontrado en {muni['schema_name']}")

        except Exception as e:
            logger.warning(f"[MCP Auth0] Error buscando en schema {muni['schema_name']}: {e}")
            continue

    if not results:
        logger.warning(f"[MCP Auth0] Usuario no encontrado en ninguna municipalidad")

    return results
