import os
import sys
from shared.logging import get_logger
import hashlib
import time
from typing import Tuple, Optional, Dict, Any, List

backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_root)

from api_gateway.context import MCPContext

logger = get_logger(__name__)


class MultiTenantSelectionRequired(Exception):
    def __init__(self, tenants: List[Dict[str, Any]]):
        self.tenants = tenants
        super().__init__(f"Usuario tiene acceso a {len(tenants)} municipalidades")


def _get_mcp_valid_audiences() -> list:
    mcp_resource_uri = os.getenv("MCP_RESOURCE_URI", "")
    audiences = []

    if mcp_resource_uri:
        audiences.append(mcp_resource_uri)
        alt = mcp_resource_uri[:-1] if mcp_resource_uri.endswith("/") else mcp_resource_uri + "/"
        audiences.append(alt)

    if not audiences:
        logger.warning(
            "[MCP Auth0] MCP_RESOURCE_URI no configurado. "
            "En producción setear MCP_RESOURCE_URI al URL público del gateway."
        )
        audiences.append("http://localhost:8005")

    return audiences


GDI_EMAIL_CLAIM = "https://gdilatam.com/email"


def _email_from_payload(payload: Dict[str, Any]) -> Optional[str]:
    return payload.get("email") or payload.get(GDI_EMAIL_CLAIM)


def verify_mcp_token(token: str) -> Dict[str, Any]:
    import jwt as pyjwt
    from jwt.exceptions import PyJWTError, ExpiredSignatureError, InvalidAudienceError, InvalidIssuerError
    from auth import get_rsa_key, _extract_unverified_iss
    from database import AUTH0_ALGORITHMS, AUTH0_ISSUERS

    valid_audiences = _get_mcp_valid_audiences()

    iss = _extract_unverified_iss(token)
    if not iss or iss not in AUTH0_ISSUERS:
        raise ValueError("Issuer del token no válido")

    rsa_key = get_rsa_key(token)

    if not rsa_key:
        raise ValueError("No se pudo encontrar la clave para validar el token")

    last_error = None
    for audience in valid_audiences:
        try:
            payload = pyjwt.decode(
                token,
                rsa_key,
                algorithms=AUTH0_ALGORITHMS,
                audience=audience,
                issuer=iss,
            )
            logger.info(f"[MCP Auth0] Token válido con audience: {audience}")
            return payload
        except (InvalidAudienceError, InvalidIssuerError) as e:
            last_error = e
            continue
        except ExpiredSignatureError:
            raise ValueError("Token expirado")
        except PyJWTError as e:
            raise ValueError(f"Token inválido: {str(e)}")

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
    if not authorization_header:
        raise ValueError("Authorization header requerido")

    if not authorization_header.startswith("Bearer "):
        raise ValueError("Formato inválido. Use: Bearer <token>")

    token = authorization_header[7:]

    payload = verify_mcp_token(token)

    email = _email_from_payload(payload)
    auth_id = payload.get("sub")

    if not auth_id:
        raise ValueError("JWT no contiene sub (auth_id)")

    if not email:
        logger.info(f"[MCP Auth0] JWT sin email claim, consultando /userinfo...")
        email, _ = await get_email_from_userinfo(token)
        if not email:
            logger.info(f"[MCP Auth0] /userinfo sin email, buscando por auth_id: {auth_id}")
            tenants = await find_user_by_auth_id(auth_id)
            if tenants:
                ctx, user_id = _resolve_tenant(tenants, tenant_id)
                logger.info(f"[MCP Auth0] Usuario autenticado por auth_id: {auth_id}, user_id: {user_id}")
                return ctx, user_id
            raise ValueError(f"Usuario con auth_id {auth_id} no encontrado en ninguna municipalidad")

    logger.info(f"[MCP Auth0] JWT válido para: {email}")

    tenants = await find_user_all_tenants(email)
    ctx, user_id = _resolve_tenant(tenants, tenant_id)
    logger.info(f"[MCP Auth0] Usuario autenticado: user_id={user_id}, schema={ctx.schema_name}")
    return ctx, user_id


async def extract_email_from_token(token: str) -> Optional[str]:
    try:
        payload = verify_mcp_token(token)
    except ValueError as e:
        logger.warning(f"[MCP Auth0] extract_email_from_token: JWT inválido: {e}")
        return None

    email = _email_from_payload(payload)
    auth_id = payload.get("sub")

    if not email and not auth_id:
        return None

    if not email:
        email, fetched_auth_id = await get_email_from_userinfo(token)
        if not auth_id:
            auth_id = fetched_auth_id

    if not email and auth_id:
        tenants = await find_user_by_auth_id(auth_id)
        if tenants:
            email = tenants[0].get("email")

    return email


_USERINFO_CACHE: Dict[str, Dict[str, Any]] = {}
_USERINFO_CACHE_TTL = 300
_USERINFO_CACHE_MAX = 100


def _cache_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _get_cached_userinfo(token: str) -> Optional[Tuple[Optional[str], Optional[str]]]:
    key = _cache_key(token)
    entry = _USERINFO_CACHE.get(key)
    if entry and time.time() - entry["ts"] < _USERINFO_CACHE_TTL:
        return entry["email"], entry["sub"]
    if entry:
        _USERINFO_CACHE.pop(key, None)
    return None


def _set_cached_userinfo(token: str, email: Optional[str], sub: Optional[str]) -> None:
    if len(_USERINFO_CACHE) >= _USERINFO_CACHE_MAX:
        now = time.time()
        expired = [k for k, v in _USERINFO_CACHE.items() if now - v["ts"] >= _USERINFO_CACHE_TTL]
        for k in expired:
            del _USERINFO_CACHE[k]
    _USERINFO_CACHE[_cache_key(token)] = {"email": email, "sub": sub, "ts": time.time()}


async def get_email_from_userinfo(token: str) -> Tuple[Optional[str], Optional[str]]:
    cached = _get_cached_userinfo(token)
    if cached is not None:
        logger.info("[MCP Auth0] /userinfo resultado obtenido de cache")
        return cached

    import httpx
    from database import AUTH0_DOMAIN, AUTH0_ISSUERS
    from auth import _extract_unverified_iss

    iss = _extract_unverified_iss(token)
    if iss and iss in AUTH0_ISSUERS:
        userinfo_url = f"{iss}userinfo"
    else:
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
            _set_cached_userinfo(token, email, sub)
            return email, sub
    except Exception as e:
        logger.warning(f"[MCP Auth0] Error obteniendo /userinfo: {e}")
        return None, None


async def find_user_by_auth_id(auth_id: str) -> List[Dict[str, Any]]:
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
    from database import fetch_all, fetch_one

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

    for muni in municipalities:
        try:
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
            logger.warning(f"[MCP Auth0] Error buscando en schema {muni['schema_name']}: {e}")
            continue

    logger.warning(f"[MCP Auth0] Usuario no encontrado en ninguna municipalidad")
    return None


async def find_user_all_tenants(email: str) -> List[Dict[str, Any]]:
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
        logger.warning("[MCP Auth0] No hay municipalidades activas")
        return []

    results = []

    for muni in municipalities:
        try:
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
