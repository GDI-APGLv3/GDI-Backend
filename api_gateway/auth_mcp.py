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


def verify_mcp_token(token: str) -> Dict[str, Any]:
    """
    Verifica JWT de Auth0 aceptando múltiples audiences.

    Soporta:
    - Audience del Backend principal (AUTH0_AUDIENCE)
    - Audience del MCP Server (MCP_RESOURCE_URI)

    Args:
        token: JWT a verificar

    Returns:
        Payload del token decodificado

    Raises:
        ValueError: Si el token es inválido
    """
    import jwt as pyjwt
    from jwt.exceptions import PyJWTError, ExpiredSignatureError, InvalidAudienceError, InvalidIssuerError
    from auth import get_rsa_key
    from database import AUTH0_DOMAIN, AUTH0_AUDIENCE, AUTH0_ALGORITHMS

    # Audiences válidos para MCP
    mcp_resource_uri = os.getenv("MCP_RESOURCE_URI", "http://localhost:8005")
    valid_audiences = [
        AUTH0_AUDIENCE,                          # Backend principal
        mcp_resource_uri,                        # MCP Server (Fly.io)
        "https://mcp.gdilatam.com",              # MCP Server producción
    ]

    # Obtener la clave RSA
    rsa_key = get_rsa_key(token)

    if not rsa_key:
        raise ValueError("No se pudo encontrar la clave para validar el token")

    # Intentar verificar con cada audience
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

    # Si ningún audience funcionó, log el error y los detalles
    try:
        # Decodificar sin verificar para debug
        unverified = pyjwt.decode(token, options={"verify_signature": False}, algorithms=AUTH0_ALGORITHMS)
        token_aud = unverified.get("aud")
        logger.error(f"[MCP Auth0] Token audience '{token_aud}' no coincide con ninguno válido: {valid_audiences}")
    except Exception:
        pass

    raise ValueError(f"Audience del token no válido. Esperado uno de: {valid_audiences}")


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


def validate_mcp_jwt(authorization_header: str, tenant_id: str = None) -> Tuple[MCPContext, str]:
    """
    Valida JWT de Auth0 y retorna contexto MCP con user_id.

    Soporta Multi-Tenant: si el usuario tiene acceso a múltiples municipalidades,
    debe especificar tenant_id para seleccionar en cuál trabajar.

    Args:
        authorization_header: "Bearer <jwt_token>"
        tenant_id: UUID de la municipalidad (requerido si usuario tiene múltiples tenants)

    Returns:
        Tuple[MCPContext, user_id] donde user_id es el UUID del usuario

    Raises:
        ValueError: Si token inválido o usuario no encontrado
        MultiTenantSelectionRequired: Si hay múltiples tenants y no se especificó tenant_id
    """
    if not authorization_header:
        raise ValueError("Authorization header requerido")

    if not authorization_header.startswith("Bearer "):
        raise ValueError("Formato inválido. Use: Bearer <token>")

    token = authorization_header[7:]  # Quitar "Bearer "

    # =====================================================================
    # Intentar como JWT primero. Si falla, intentar como opaque token.
    # Auth0 devuelve opaque tokens cuando el cliente no envía audience
    # (común en Claude Code y ChatGPT que usan RFC 8707 resource param
    # en vez del audience param que Auth0 espera).
    # =====================================================================
    email = None
    payload = None

    try:
        payload = verify_mcp_token(token)
        email = payload.get("email")
        auth_id = payload.get("sub")

        if not auth_id:
            raise ValueError("JWT no contiene sub (auth_id)")

        # Si JWT no tiene email, obtenerlo de /userinfo
        if not email:
            logger.info(f"[MCP Auth0] JWT sin email, consultando /userinfo...")
            email, _ = get_email_from_userinfo(token)
            if not email:
                # Último intento: buscar por auth_id en todos los tenants
                logger.info(f"[MCP Auth0] /userinfo sin email, buscando por auth_id: {auth_id}")
                tenants = find_user_by_auth_id(auth_id)
                if tenants:
                    ctx, user_id = _resolve_tenant(tenants, tenant_id)
                    logger.info(f"[MCP Auth0] Usuario autenticado por auth_id: {auth_id}, user_id: {user_id}")
                    return ctx, user_id
                raise ValueError(f"Usuario con auth_id {auth_id} no encontrado")

        logger.info(f"[MCP Auth0] JWT válido para: {email}")

    except ValueError as jwt_error:
        # SEC-08: opaque tokens bypass audience validation - known limitation
        # JWT falló - intentar como opaque token via /userinfo
        # Auth0 devuelve opaque tokens cuando no se especifica audience
        logger.info(f"[MCP Auth0] JWT falló ({jwt_error}), intentando como opaque token via /userinfo")
        email, auth_id = get_email_from_userinfo(token)
        if not email and not auth_id:
            raise ValueError(f"Token no es JWT válido ni opaque token funcional. Error JWT: {jwt_error}")
        # Validar auth_id contra BD (más seguro que solo email)
        if auth_id:
            tenants = find_user_by_auth_id(auth_id)
            if tenants:
                ctx, user_id = _resolve_tenant(tenants, tenant_id)
                logger.warning(
                    f"[MCP Auth0] SEC-08: Opaque token accepted without audience validation. "
                    f"auth_id: {auth_id}, user_id: {user_id}. "
                    f"A token issued for another app in the same Auth0 tenant would be accepted."
                )
                return ctx, user_id
        if not email:
            raise ValueError(f"Token no es JWT válido ni opaque token funcional. Error JWT: {jwt_error}")
        logger.warning(
            f"[MCP Auth0] SEC-08: Opaque token accepted without audience validation. "
            f"email: {email}. A token issued for another app in the same Auth0 tenant would be accepted."
        )

    # Buscar usuario en TODOS los tenants
    tenants = find_user_all_tenants(email)
    ctx, user_id = _resolve_tenant(tenants, tenant_id)
    logger.info(f"[MCP Auth0] Usuario autenticado: {email}, user_id: {user_id}, schema: {ctx.schema_name}")
    return ctx, user_id


def extract_email_from_token(token: str) -> Optional[str]:
    """
    Extrae email de un JWT o opaque token de Auth0.

    Centraliza la lógica "token → email" para handlers que solo necesitan
    el email sin construir MCPContext completo.

    Soporta: JWT con email, JWT sin email, opaque token con email,
    opaque token sin email pero con auth_id en BD.

    Args:
        token: Access token (JWT o opaque)

    Returns:
        Email del usuario, o None si no se pudo obtener
    """
    email = None
    auth_id = None

    try:
        payload = verify_mcp_token(token)
        email = payload.get("email")
        auth_id = payload.get("sub")
    except ValueError:
        # JWT falló — intentar como opaque token via /userinfo
        email, auth_id = get_email_from_userinfo(token)

    if not email and not auth_id:
        return None

    # JWT válido pero sin email → consultar /userinfo
    if not email:
        email, _ = get_email_from_userinfo(token)

    # Sin email pero con auth_id → buscar email en BD
    if not email and auth_id:
        tenants = find_user_by_auth_id(auth_id)
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


def get_email_from_userinfo(token: str) -> Tuple[Optional[str], Optional[str]]:
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
        # Llamada síncrona con httpx
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                userinfo_url,
                headers={"Authorization": f"Bearer {token}"}
            )
            response.raise_for_status()
            data = response.json()
            email = data.get("email")
            sub = data.get("sub")
            if email:
                logger.info(f"[MCP Auth0] Email obtenido de /userinfo: {email}")
            # Cache successful result (do NOT cache errors)
            _set_cached_userinfo(token, email, sub)
            return email, sub
    except Exception as e:
        logger.warning(f"[MCP Auth0] Error obteniendo /userinfo: {e}")
        return None, None


def find_user_by_auth_id(auth_id: str) -> List[Dict[str, Any]]:
    """
    Busca usuario por auth_id (sub de Auth0) en TODAS las municipalidades.
    Retorna lista de tenants donde existe (puede ser 0, 1 o N).

    Args:
        auth_id: El claim 'sub' del token JWT (ej: "auth0|123456")

    Returns:
        Lista de dicts con user_id, municipality_id, municipality_name, schema_name
    """
    from database import execute_query

    try:
        municipalities = execute_query(
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
            user = execute_query(
                "SELECT id as user_id, email FROM users WHERE auth_id = %s AND estado = 1",
                (auth_id,),
                fetch_one=True,
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


def find_user_across_schemas(email: str) -> Optional[Dict[str, Any]]:
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
    from database import execute_query

    # Obtener todas las municipalidades activas
    try:
        municipalities = execute_query(
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
            user = execute_query(
                f"SELECT id as user_id, email FROM users WHERE email = %s AND estado = 1",
                (email,),
                fetch_one=True,
                schema_name=muni['schema_name']
            )

            if user:
                logger.info(f"[MCP Auth0] Usuario {email} encontrado en schema {muni['schema_name']}")
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

    logger.warning(f"[MCP Auth0] Usuario {email} no encontrado en ninguna municipalidad")
    return None


def find_user_in_schema(email: str, schema_name: str) -> Optional[Dict[str, Any]]:
    """
    Busca usuario por email en un schema específico.

    Args:
        email: Email del usuario
        schema_name: Schema donde buscar

    Returns:
        Dict con user_id, email si encontrado, None si no
    """
    from database import execute_query

    try:
        user = execute_query(
            "SELECT id as user_id, email, full_name FROM users WHERE email = %s AND estado = 1",
            (email,),
            fetch_one=True,
            schema_name=schema_name
        )
        return user
    except Exception as e:
        logger.error(f"[MCP Auth0] Error buscando usuario en {schema_name}: {e}")
        return None


def find_user_all_tenants(email: str) -> List[Dict[str, Any]]:
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
    from database import execute_query

    # Obtener todas las municipalidades activas
    try:
        municipalities = execute_query(
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
            user = execute_query(
                "SELECT id as user_id, email FROM users WHERE LOWER(email) = LOWER(%s) AND estado = 1",
                (email,),
                fetch_one=True,
                schema_name=muni['schema_name']
            )

            if user:
                results.append({
                    "user_id": user["user_id"],
                    "municipality_id": muni["id"],
                    "municipality_name": muni.get("name", muni["schema_name"]),
                    "schema_name": muni["schema_name"]
                })
                logger.info(f"[MCP Auth0] Usuario {email} encontrado en {muni['schema_name']}")

        except Exception as e:
            logger.warning(f"[MCP Auth0] Error buscando en schema {muni['schema_name']}: {e}")
            continue

    if not results:
        logger.warning(f"[MCP Auth0] Usuario {email} no encontrado en ninguna municipalidad")

    return results
