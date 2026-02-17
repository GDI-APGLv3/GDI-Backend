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
    from jose import jwt as jose_jwt, JWTError
    from auth import get_rsa_key
    from database import AUTH0_DOMAIN, AUTH0_AUDIENCE, AUTH0_ALGORITHMS

    # Audiences válidos para MCP
    mcp_resource_uri = os.getenv("MCP_RESOURCE_URI", "http://localhost:8005")
    valid_audiences = [
        AUTH0_AUDIENCE,                          # Backend principal
        mcp_resource_uri,                        # MCP Server local
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
            payload = jose_jwt.decode(
                token,
                rsa_key,
                algorithms=AUTH0_ALGORITHMS,
                audience=audience,
                issuer=f"https://{AUTH0_DOMAIN}/"
            )
            logger.info(f"[MCP Auth0] Token válido con audience: {audience}")
            return payload
        except jose_jwt.JWTClaimsError as e:
            # Audience no coincide, probar siguiente
            last_error = e
            continue
        except jose_jwt.ExpiredSignatureError:
            raise ValueError("Token expirado")
        except JWTError as e:
            raise ValueError(f"Token inválido: {str(e)}")

    # Si ningún audience funcionó, log el error y los detalles
    try:
        # Decodificar sin verificar para debug
        unverified = jose_jwt.get_unverified_claims(token)
        token_aud = unverified.get("aud")
        logger.error(f"[MCP Auth0] Token audience '{token_aud}' no coincide con ninguno válido: {valid_audiences}")
    except Exception:
        pass

    raise ValueError(f"Audience del token no válido. Esperado uno de: {valid_audiences}")


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

    # Verificar JWT con Auth0 (soporta múltiples audiences)
    try:
        payload = verify_mcp_token(token)
    except ValueError as e:
        logger.error(f"[MCP Auth0] Error validando JWT: {e}")
        raise

    email = payload.get("email")
    auth_id = payload.get("sub")

    if not auth_id:
        raise ValueError("JWT no contiene sub (auth_id)")

    # Si no hay email en el token, obtenerlo de Auth0 /userinfo
    if not email:
        logger.info(f"[MCP Auth0] Token sin email, consultando /userinfo...")
        email = get_email_from_userinfo(token)
        if not email:
            # Último intento: buscar por auth_id
            logger.info(f"[MCP Auth0] /userinfo sin email, buscando por auth_id: {auth_id}")
            user_data = find_user_by_auth_id(auth_id)
            if user_data:
                user_id = str(user_data["user_id"])
                municipality_id = str(user_data["municipality_id"])
                schema_name = user_data["schema_name"]
                ctx = MCPContext(
                    api_key="auth0-jwt",
                    municipality_id=municipality_id,
                    schema_name=schema_name,
                    auth_source="mcp_oauth"  # Trazabilidad: Claude Code, ChatGPT, Gemini
                )
                logger.info(f"[MCP Auth0] Usuario autenticado por auth_id: {auth_id}, user_id: {user_id}")
                return ctx, user_id
            raise ValueError(f"Usuario con auth_id {auth_id} no encontrado")

    logger.info(f"[MCP Auth0] JWT válido para: {email}")

    # Buscar usuario en TODOS los tenants
    tenants = find_user_all_tenants(email)

    if not tenants:
        raise ValueError(f"Usuario {email} no encontrado en ninguna municipalidad")

    # Selección de tenant
    if len(tenants) == 1:
        # Un solo tenant → usar ese (comportamiento actual, sin cambios)
        selected = tenants[0]
        logger.info(f"[MCP Auth0] Usuario con un solo tenant: {selected['schema_name']}")
    elif tenant_id:
        # Multi-tenant CON selección explícita
        selected = next((t for t in tenants if str(t["municipality_id"]) == str(tenant_id)), None)
        if not selected:
            tenant_names = [t["municipality_name"] for t in tenants]
            raise ValueError(f"No tienes acceso al tenant {tenant_id}. Tenants disponibles: {tenant_names}")
        logger.info(f"[MCP Auth0] Tenant seleccionado: {selected['schema_name']}")
    else:
        # Multi-tenant SIN selección → lanzar excepción con lista
        logger.info(f"[MCP Auth0] Usuario con {len(tenants)} tenants, requiere selección")
        raise MultiTenantSelectionRequired(tenants)

    user_id = str(selected["user_id"])
    municipality_id = str(selected["municipality_id"])
    schema_name = selected["schema_name"]

    ctx = MCPContext(
        api_key="auth0-jwt",  # Marcador especial para indicar auth via JWT
        municipality_id=municipality_id,
        schema_name=schema_name,
        auth_source="mcp_oauth"  # Trazabilidad: Claude Code, ChatGPT, Gemini
    )

    logger.info(f"[MCP Auth0] Usuario autenticado: {email}, user_id: {user_id}, schema: {schema_name}")
    return ctx, user_id


def get_email_from_userinfo(token: str) -> Optional[str]:
    """
    Obtiene el email del usuario desde Auth0 /userinfo endpoint.

    Útil cuando el access token no incluye el email claim.

    Args:
        token: Access token de Auth0

    Returns:
        Email del usuario o None si no se pudo obtener
    """
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
            if email:
                logger.info(f"[MCP Auth0] Email obtenido de /userinfo: {email}")
            return email
    except Exception as e:
        logger.warning(f"[MCP Auth0] Error obteniendo /userinfo: {e}")
        return None


def find_user_by_auth_id(auth_id: str) -> Optional[Dict[str, Any]]:
    """
    Busca usuario por auth_id (sub de Auth0) en todas las municipalidades.

    Args:
        auth_id: El claim 'sub' del token JWT (ej: "auth0|123456")

    Returns:
        Dict con user_id, municipality_id, schema_name si encontrado
        None si no existe
    """
    from database import execute_query

    try:
        municipalities = execute_query(
            "SELECT id, schema_name FROM public.municipalities WHERE is_active = true",
            schema_name="public"
        )
    except Exception as e:
        logger.error(f"[MCP Auth0] Error obteniendo municipalidades: {e}")
        return None

    if not municipalities:
        return None

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
                return {
                    "user_id": user["user_id"],
                    "email": user.get("email"),
                    "municipality_id": muni["id"],
                    "schema_name": muni["schema_name"]
                }
        except Exception as e:
            logger.warning(f"[MCP Auth0] Error buscando auth_id en {muni['schema_name']}: {e}")
            continue

    return None


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
